from decimal import Decimal

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from app.cli import run_cli_async

console = Console()

PAYMENT_STATUS_MAP = {
    "succeeded": "Успешно",
    "pending": "Ожидание",
    "failed": "Ошибка",
    "cancelled": "Отменен",
    "refunded": "Возврат",
}


def _user_name(user) -> str:
    return (user.full_name or "").strip() or (f"@{user.username}" if user.username else f"User #{user.id}")


def _user_title(user) -> str:
    return f"{_user_name(user)} (@{user.username})" if user.username else _user_name(user)


def _user_status(user) -> tuple[str, str]:
    if getattr(user, "is_banned", False):
        return "Забанен", "red"
    if getattr(user, "is_active", False):
        return "Активен", "green"
    return "Неактивен", "yellow"


def _sub_name(vpn_key) -> str:
    if getattr(vpn_key, "plan", None) and getattr(vpn_key.plan, "name", None):
        return vpn_key.plan.name
    return vpn_key.name or f"Ключ #{vpn_key.id}"


async def _list_users(limit: int, offset: int, page: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    async with AsyncSessionFactory() as session:
        total = await session.scalar(select(func.count(User.id))) or 0

        sub_count_sq = (
            select(func.count(VpnKey.id))
            .where(VpnKey.user_id == User.id, VpnKey.status == VpnKeyStatus.ACTIVE.value)
            .correlate(User)
            .scalar_subquery()
        )

        stmt = (
            select(User, sub_count_sq.label("sub_count"))
            .order_by(User.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()

        table = Table(title=f"Пользователи (страница {page}, всего {total})", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Имя")
        table.add_column("Username")
        table.add_column("Баланс", justify="right")
        table.add_column("Подписок", justify="right")
        table.add_column("Статус")

        for user, sub_count in rows:
            status, style = _user_status(user)
            table.add_row(
                str(user.id),
                _user_name(user),
                f"@{user.username}" if user.username else "-",
                f"{Decimal(user.balance or 0):.2f}",
                str(sub_count or 0),
                f"[{style}]{status}[/{style}]",
            )

        console.print(table)
        if total > limit:
            total_pages = (total + limit - 1) // limit
            click.echo(f"\nСтраница {page} из {total_pages}")


async def _search_user(query: str):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User

    async with AsyncSessionFactory() as session:
        if query.isdigit():
            stmt = select(User).where(User.id == int(query))
        else:
            cleaned = query[1:] if query.startswith("@") else query
            stmt = select(User).where(
                or_(
                    User.username.ilike(f"{cleaned}%"),
                    User.full_name.ilike(f"%{cleaned}%"),
                )
            )

        users = (await session.execute(stmt.order_by(User.id))).scalars().all()
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
            status, _ = _user_status(user)
            table.add_row(
                str(user.id),
                _user_name(user),
                f"@{user.username}" if user.username else "-",
                f"{Decimal(user.balance or 0):.2f}",
                status,
            )

        console.print(table)


async def _user_info(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.payment import Payment
    from app.models.user import User
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return

        status, _ = _user_status(user)
        click.echo("")
        click.secho("ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ", bold=True, fg="cyan")
        click.echo("=" * 50)
        click.echo(f"ID: {user.id}")
        click.echo(f"Имя: {_user_name(user)}")
        click.echo(f"Username: @{user.username}" if user.username else "Username: -")
        click.echo(f"Баланс: {Decimal(user.balance or 0):.2f}")
        click.echo(f"Статус: {status}")
        click.echo(f"Язык: {user.language or 'ru'}")
        click.echo(f"Автопродление: {'включено' if user.autorenew else 'выключено'}")
        click.echo(f"Реферальный код: {user.referral_code or '-'}")
        click.echo(f"Последняя активность: {user.last_seen.strftime('%Y-%m-%d %H:%M:%S') if user.last_seen else '-'}")

        subs = (
            await session.execute(
                select(VpnKey)
                .options(selectinload(VpnKey.plan))
                .where(VpnKey.user_id == user_id, VpnKey.status == VpnKeyStatus.ACTIVE.value)
                .order_by(VpnKey.expires_at)
            )
        ).scalars().all()
        if subs:
            click.echo("")
            click.secho("АКТИВНЫЕ ПОДПИСКИ:", bold=True)
            for sub in subs:
                click.echo(
                    f"  • ID: {sub.id}, Тариф: {_sub_name(sub)}, "
                    f"Истекает: {sub.expires_at.strftime('%Y-%m-%d') if sub.expires_at else '-'}"
                )
        else:
            click.echo("")
            click.secho("Нет активных подписок", fg="yellow")

        payments = (
            await session.execute(
                select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc()).limit(5)
            )
        ).scalars().all()
        if payments:
            click.echo("")
            click.secho("ПОСЛЕДНИЕ ПЛАТЕЖИ:", bold=True)
            for payment in payments:
                click.echo(
                    f"  • ID: {payment.id}, Сумма: {Decimal(payment.amount or 0):.2f}, "
                    f"Статус: {PAYMENT_STATUS_MAP.get(payment.status, payment.status)}, "
                    f"Дата: {payment.created_at.strftime('%Y-%m-%d %H:%M') if payment.created_at else '-'}"
                )


async def _ban_user(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        if user.is_banned:
            click.secho("Пользователь уже забанен", fg="yellow")
            return

        click.echo(f"Пользователь: {_user_title(user)}")
        if not click.confirm("Вы уверены, что хотите забанить этого пользователя?", default=False):
            click.secho("Отменено", fg="yellow")
            return

        user.is_banned = True
        user.is_active = False
        await session.commit()
        click.secho(f"✓ Пользователь {user_id} забанен", fg="green", bold=True)


async def _unban_user(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        if not user.is_banned:
            click.secho("Пользователь не забанен", fg="yellow")
            return

        user.is_banned = False
        user.is_active = True
        await session.commit()
        click.secho(f"✓ Пользователь {user_id} разбанен", fg="green", bold=True)


async def _change_balance(user_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return

        current_balance = Decimal(user.balance or 0)
        click.echo(f"Пользователь: {_user_name(user)}")
        click.echo(f"Текущий баланс: {current_balance:.2f}")
        click.echo("")

        action = click.prompt("Действие", type=click.Choice(["add", "deduct"], case_sensitive=False))
        amount = Decimal(str(click.prompt("Сумма", type=float)))

        if amount <= 0:
            click.secho("Сумма должна быть больше 0", fg="red")
            return

        if action == "add":
            user.balance = current_balance + amount
            click.secho(f"✓ Добавлено {amount:.2f}. Новый баланс: {Decimal(user.balance):.2f}", fg="green", bold=True)
        else:
            if current_balance < amount:
                click.secho("Недостаточно средств на балансе", fg="red")
                return
            user.balance = current_balance - amount
            click.secho(f"✓ Списано {amount:.2f}. Новый баланс: {Decimal(user.balance):.2f}", fg="green", bold=True)

        await session.commit()


async def _gift_subscription():
    from app.core.database import AsyncSessionFactory
    from app.models.plan import Plan
    from app.models.user import User
    from app.services.vpn_key import VpnKeyService

    async with AsyncSessionFactory() as session:
        user_id = click.prompt("ID пользователя", type=int)
        user = await session.scalar(select(User).where(User.id == user_id))
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return

        click.echo(f"Пользователь: {_user_name(user)}")
        plans = (await session.execute(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.id))).scalars().all()
        if not plans:
            click.secho("Нет активных тарифов", fg="yellow")
            return

        click.echo("\nДоступные тарифы:")
        for plan in plans:
            click.echo(f"  {plan.id}. {plan.name} - {plan.duration_days} дней, {Decimal(plan.price or 0):.2f}")

        plan_id = click.prompt("ID тарифа", type=int)
        plan = next((p for p in plans if p.id == plan_id), None)
        if not plan:
            click.secho("Тариф не найден", fg="red")
            return

        if not click.confirm(f"Подарить подписку '{plan.name}' пользователю {_user_name(user)}?", default=True):
            click.secho("Отменено", fg="yellow")
            return

        key = await VpnKeyService(session).provision(user.id, plan)
        if not key:
            await session.rollback()
            click.secho("Не удалось создать VPN-ключ для подарочной подписки", fg="red")
            return

        key.price = 0
        await session.commit()
        click.secho(f"✓ Подписка '{plan.name}' подарена пользователю {_user_name(user)}", fg="green", bold=True)
        click.echo(f"  ID ключа: {key.id}")
        click.echo(f"  Истекает: {key.expires_at.strftime('%Y-%m-%d %H:%M:%S') if key.expires_at else '-'}")


def list_users(limit=20, offset=0, page=1):
    run_cli_async(_list_users(limit, offset, page))


def search(query=None):
    if query is None:
        query = click.prompt("Поисковый запрос (ID, @username, или имя)")
    run_cli_async(_search_user(query))


def info(user_id=None):
    if user_id is None:
        user_id = click.prompt("ID пользователя", type=int)
    run_cli_async(_user_info(user_id))


def ban(user_id=None):
    if user_id is None:
        user_id = click.prompt("ID пользователя", type=int)
    run_cli_async(_ban_user(user_id))


def unban(user_id=None):
    if user_id is None:
        user_id = click.prompt("ID пользователя", type=int)
    run_cli_async(_unban_user(user_id))


def balance(user_id=None):
    if user_id is None:
        user_id = click.prompt("ID пользователя", type=int)
    run_cli_async(_change_balance(user_id))


def gift():
    run_cli_async(_gift_subscription())
