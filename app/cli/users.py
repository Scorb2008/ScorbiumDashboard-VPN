import click
import asyncio
from rich.console import Console
from rich.table import Table
from datetime import datetime

console = Console()

STATUS_MAP = {
    "active": "Активен",
    "banned": "Забанен",
    "inactive": "Неактивен"
}

async def _list_users(limit: int, offset: int, page: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from sqlalchemy import select, func
    
    async with AsyncSessionFactory() as session:
        count_result = await session.execute(select(func.count(User.id)))
        total = count_result.scalar()
        
        stmt = select(User).order_by(User.id).offset(offset).limit(limit)
        result = await session.execute(stmt)
        users = result.scalars().all()
        
        table = Table(title=f"Пользователи (страница {page}, всего {total})", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Имя")
        table.add_column("Username")
        table.add_column("Баланс", justify="right")
        table.add_column("Подписок")
        table.add_column("Статус")
        
        for user in users:
            from app.models.vpn_key import VpnKey
            sub_stmt = select(func.count(VpnKey.id)).where(
                VpnKey.user_id == user.id,
                VpnKey.status == "active"
            )
            sub_result = await session.execute(sub_stmt)
            sub_count = sub_result.scalar()
            
            status = STATUS_MAP.get(user.status, user.status) if user.status else "Неактивен"
            status_style = "green" if user.status == "active" else "red" if user.status == "banned" else "yellow"
            
            table.add_row(
                str(user.id),
                user.first_name or "",
                f"@{user.username}" if user.username else "-",
                f"{user.balance or 0:.2f}",
                str(sub_count),
                f"[{status_style}]{status}[/{status_style}]"
            )
        
        console.print(table)
        
        if total > limit:
            total_pages = (total + limit - 1) // limit
            click.echo(f"\nСтраница {page} из {total_pages}")

async def _search_user(query: str):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from sqlalchemy import select, or_
    
    async with AsyncSessionFactory() as session:
        if query.isdigit():
            stmt = select(User).where(User.id == int(query))
        elif query.startswith("@"):
            stmt = select(User).where(User.username.ilike(f"{query[1:]}%"))
        else:
            stmt = select(User).where(
                or_(
                    User.first_name.ilike(f"%{query}%"),
                    User.last_name.ilike(f"%{query}%")
                )
            )
        
        result = await session.execute(stmt)
        users = result.scalars().all()
        
        if not users:
            click.secho("Пользователи не найдены", fg="yellow")
            return
        
        table = Table(title=f"Результаты поиска: {query}", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Имя")
        table.add_column("Username")
        table.add_column("Баланс", justify="right")
        table.add_column("Статус")
        
        for user in users:
            status = STATUS_MAP.get(user.status, user.status) if user.status else "Неактивен"
            table.add_row(
                str(user.id),
                f"{user.first_name or ''} {user.last_name or ''}".strip(),
                f"@{user.username}" if user.username else "-",
                f"{user.balance or 0:.2f}",
                status
            )
        
        console.print(table)

async def _user_info(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from app.models.vpn_key import VpnKey
    from app.models.payment import Payment
    from sqlalchemy import select, func
    
    async with AsyncSessionFactory() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        
        click.echo("")
        click.secho("ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ", bold=True, fg="cyan")
        click.echo("=" * 50)
        click.echo(f"ID: {user.id}")
        click.echo(f"Имя: {user.first_name or ''} {user.last_name or ''}".strip())
        click.echo(f"Username: @{user.username}" if user.username else "Username: -")
        click.echo(f"Telegram ID: {user.tg_id}")
        click.echo(f"Баланс: {user.balance or 0:.2f}")
        click.echo(f"Статус: {STATUS_MAP.get(user.status, user.status) if user.status else 'Неактивен'}")
        click.echo(f"Язык: {user.language or 'ru'}")
        click.echo(f"Реферальный код: {user.referral_code or '-'}")
        click.echo(f"Создан: {user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '-'}")
        
        stmt = select(VpnKey).where(
            VpnKey.user_id == user_id,
            VpnKey.status == "active"
        ).order_by(VpnKey.expires_at)
        result = await session.execute(stmt)
        subs = result.scalars().all()
        
        if subs:
            click.echo("")
            click.secho("АКТИВНЫЕ ПОДПИСКИ:", bold=True)
            for sub in subs:
                click.echo(f"  • ID: {sub.id}, План: {sub.plan_name}, Истекает: {sub.expires_at.strftime('%Y-%m-%d') if sub.expires_at else '-'}")
        else:
            click.echo("")
            click.secho("Нет активных подписок", fg="yellow")
        
        stmt = select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc()).limit(5)
        result = await session.execute(stmt)
        payments = result.scalars().all()
        
        if payments:
            click.echo("")
            click.secho("ПОСЛЕДНИЕ ПЛАТЕЖИ:", bold=True)
            for p in payments:
                status_map = {"success": "Успешно", "pending": "Ожидание", "failed": "Ошибка"}
                click.echo(f"  • ID: {p.id}, Сумма: {p.amount}, Статус: {status_map.get(p.status, p.status)}, Дата: {p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else '-'}")

async def _ban_user(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        
        if user.status == "banned":
            click.secho("Пользователь уже забанен", fg="yellow")
            return
        
        click.echo(f"Пользователь: {user.first_name} {user.last_name} (@{user.username})")
        if not click.confirm("Вы уверены, что хотите забанить этого пользователя?", default=False):
            click.secho("Отменено", fg="yellow")
            return
        
        user.status = "banned"
        await session.commit()
        click.secho(f"✓ Пользователь {user_id} забанен", fg="green", bold=True)

async def _unban_user(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        
        if user.status != "banned":
            click.secho("Пользователь не забанен", fg="yellow")
            return
        
        user.status = "active"
        await session.commit()
        click.secho(f"✓ Пользователь {user_id} разбанен", fg="green", bold=True)

async def _change_balance(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        
        click.echo(f"Пользователь: {user.first_name} {user.last_name}")
        click.echo(f"Текущий баланс: {user.balance or 0:.2f}")
        click.echo("")
        
        action = click.prompt("Действие", type=click.Choice(["add", "deduct"], case_sensitive=False))
        amount = click.prompt("Сумма", type=float)
        
        if amount <= 0:
            click.secho("Сумма должна быть больше 0", fg="red")
            return
        
        if action == "add":
            user.balance = (user.balance or 0) + amount
            click.secho(f"✓ Добавлено {amount:.2f}. Новый баланс: {user.balance:.2f}", fg="green", bold=True)
        else:
            if (user.balance or 0) < amount:
                click.secho("Недостаточно средств на балансе", fg="red")
                return
            user.balance = (user.balance or 0) - amount
            click.secho(f"✓ Списано {amount:.2f}. Новый баланс: {user.balance:.2f}", fg="green", bold=True)
        
        await session.commit()

async def _gift_subscription():
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from app.models.plan import Plan
    from app.models.vpn_key import VpnKey
    from sqlalchemy import select
    from datetime import timedelta
    
    async with AsyncSessionFactory() as session:
        user_id = click.prompt("ID пользователя", type=int)
        
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        
        click.echo(f"Пользователь: {user.first_name} {user.last_name}")
        
        stmt = select(Plan).where(Plan.is_active == True)
        result = await session.execute(stmt)
        plans = result.scalars().all()
        
        if not plans:
            click.secho("Нет активных тарифов", fg="yellow")
            return
        
        click.echo("\nДоступные тарифы:")
        for plan in plans:
            click.echo(f"  {plan.id}. {plan.name} - {plan.duration_days} дней, {plan.price}")
        
        plan_id = click.prompt("ID тарифа", type=int)
        plan = next((p for p in plans if p.id == plan_id), None)
        
        if not plan:
            click.secho("Тариф не найден", fg="red")
            return
        
        if not click.confirm(f"Подарить подписку '{plan.name}' пользователю {user.first_name}?", default=True):
            click.secho("Отменено", fg="yellow")
            return
        
        expires_at = datetime.utcnow() + timedelta(days=plan.duration_days)
        vpn_key = VpnKey(
            user_id=user.id,
            plan_id=plan.id,
            plan_name=plan.name,
            key="gifted_manual",
            status="active",
            expires_at=expires_at,
            price=0
        )
        
        session.add(vpn_key)
        await session.commit()
        
        click.secho(f"✓ Подписка '{plan.name}' подарена пользователю {user.first_name}", fg="green", bold=True)
        click.echo(f"  ID ключа: {vpn_key.id}")
        click.echo(f"  Истекает: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}")

def list_users(limit=20, offset=0, page=1):
    import asyncio
    asyncio.run(_list_users(limit, offset, page))

def search():
    query = click.prompt("Поисковый запрос (ID, @username, или имя)")
    import asyncio
    asyncio.run(_search_user(query))

def info():
    user_id = click.prompt("ID пользователя", type=int)
    import asyncio
    asyncio.run(_user_info(user_id))

def ban():
    user_id = click.prompt("ID пользователя", type=int)
    import asyncio
    asyncio.run(_ban_user(user_id))

def unban():
    user_id = click.prompt("ID пользователя", type=int)
    import asyncio
    asyncio.run(_unban_user(user_id))

def balance():
    user_id = click.prompt("ID пользователя", type=int)
    import asyncio
    asyncio.run(_change_balance(user_id))

def gift():
    import asyncio
    asyncio.run(_gift_subscription())
