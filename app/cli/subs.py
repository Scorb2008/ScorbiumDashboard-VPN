import click
import asyncio
from rich.console import Console
from rich.table import Table
from datetime import datetime

console = Console()

STATUS_MAP = {
    "active": "Активна",
    "expired": "Истекла",
    "revoked": "Отозвана"
}

STATUS_STYLE = {
    "active": "green",
    "expired": "yellow",
    "revoked": "red"
}

async def _list_subs(status: str, limit: int):
    from app.core.database import AsyncSessionFactory
    from app.models.vpn_key import VpnKey
    from app.models.user import User
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(VpnKey, User).join(User, VpnKey.user_id == User.id)
        
        if status != "all":
            stmt = stmt.where(VpnKey.status == status)
        
        stmt = stmt.order_by(VpnKey.id.desc()).limit(limit)
        result = await session.execute(stmt)
        rows = result.all()
        
        table = Table(title=f"Подписки ({status})", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Пользователь")
        table.add_column("Тариф")
        table.add_column("Статус")
        table.add_column("Истекает")
        table.add_column("Цена", justify="right")
        
        for vpn_key, user in rows:
            status_text = STATUS_MAP.get(vpn_key.status, vpn_key.status)
            style = STATUS_STYLE.get(vpn_key.status, "white")
            
            table.add_row(
                str(vpn_key.id),
                f"{user.first_name} (@{user.username})" if user.username else user.first_name,
                vpn_key.plan_name or "-",
                f"[{style}]{status_text}[/{style}]",
                vpn_key.expires_at.strftime('%Y-%m-%d') if vpn_key.expires_at else "-",
                f"{vpn_key.price or 0:.2f}"
            )
        
        console.print(table)
        click.echo(f"\nВсего показано: {len(rows)}")

async def _create_sub(user_id: int, plan_id: int = None, days: int = None, name: str = None):
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from app.models.plan import Plan
    from app.models.vpn_key import VpnKey
    from sqlalchemy import select
    from datetime import timedelta
    
    async with AsyncSessionFactory() as session:
        # Verify user exists
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            click.secho(f"Пользователь с ID {user_id} не найден", fg="red")
            return
        
        click.echo(f"Пользователь: {user.first_name} {user.last_name}")
        
        if plan_id:
            # Create from plan
            stmt = select(Plan).where(Plan.id == plan_id)
            result = await session.execute(stmt)
            plan = result.scalar_one_or_none()
            
            if not plan:
                click.secho(f"Тариф с ID {plan_id} не найден", fg="red")
                return
            
            duration_days = plan.duration_days
            plan_name = plan.name
            price = plan.price
        elif days and name:
            duration_days = days
            plan_name = name
            price = 0
        else:
            click.secho("Укажите --plan-id или --days и --name", fg="red")
            return
        
        if not click.confirm(f"Создать подписку '{plan_name}' на {duration_days} дней для {user.first_name}?", default=True):
            click.secho("Отменено", fg="yellow")
            return
        
        expires_at = datetime.utcnow() + timedelta(days=duration_days)
        
        vpn_key = VpnKey(
            user_id=user.id,
            plan_id=plan_id,
            plan_name=plan_name,
            key="manual_creation",
            status="active",
            expires_at=expires_at,
            price=price
        )
        
        session.add(vpn_key)
        await session.commit()
        
        click.secho(f"✓ Подписка создана (ID: {vpn_key.id})", fg="green", bold=True)
        click.echo(f"  Тариф: {plan_name}")
        click.echo(f"  Истекает: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}")

async def _extend_sub(key_id: int, days: int):
    from app.core.database import AsyncSessionFactory
    from app.models.vpn_key import VpnKey
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(VpnKey).where(VpnKey.id == key_id)
        result = await session.execute(stmt)
        vpn_key = result.scalar_one_or_none()
        
        if not vpn_key:
            click.secho(f"Подписка с ID {key_id} не найдена", fg="red")
            return
        
        click.echo(f"Подписка: {vpn_key.plan_name} (ID: {vpn_key.id})")
        click.echo(f"Текущий статус: {STATUS_MAP.get(vpn_key.status, vpn_key.status)}")
        click.echo(f"Истекает: {vpn_key.expires_at.strftime('%Y-%m-%d %H:%M:%S') if vpn_key.expires_at else '-'}")
        
        if not click.confirm(f"Продлить на {days} дней?", default=True):
            click.secho("Отменено", fg="yellow")
            return
        
        if vpn_key.status == "expired":
            vpn_key.status = "active"
        
        from datetime import timedelta
        if vpn_key.expires_at and vpn_key.expires_at > datetime.utcnow():
            vpn_key.expires_at = vpn_key.expires_at + timedelta(days=days)
        else:
            vpn_key.expires_at = datetime.utcnow() + timedelta(days=days)
        
        await session.commit()
        
        click.secho(f"✓ Подписка продлена до {vpn_key.expires_at.strftime('%Y-%m-%d %H:%M:%S')}", fg="green", bold=True)

async def _revoke_sub(key_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.vpn_key import VpnKey
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(VpnKey).where(VpnKey.id == key_id)
        result = await session.execute(stmt)
        vpn_key = result.scalar_one_or_none()
        
        if not vpn_key:
            click.secho(f"Подписка с ID {key_id} не найдена", fg="red")
            return
        
        click.echo(f"Подписка: {vpn_key.plan_name} (ID: {vpn_key.id})")
        click.echo(f"Статус: {STATUS_MAP.get(vpn_key.status, vpn_key.status)}")
        
        if vpn_key.status == "revoked":
            click.secho("Подписка уже отозвана", fg="yellow")
            return
        
        if not click.confirm("Вы уверены, что хотите отозвать эту подписку?", default=False):
            click.secho("Отменено", fg="yellow")
            return
        
        vpn_key.status = "revoked"
        await session.commit()
        
        click.secho(f"✓ Подписка {key_id} отозвана", fg="green", bold=True)

def list_subs(status="active", limit=20):
    import asyncio
    asyncio.run(_list_subs(status, limit))

def create(user_id=None, plan_id=None, days=None, name=None):
    if user_id is None:
        user_id = click.prompt("ID пользователя", type=int)

    if plan_id is None and days is None:
        plan_id = click.prompt("ID тарифа (оставьте пустым для ручного создания)", type=int, default=None)

    if not plan_id:
        if days is None:
            days = click.prompt("Дней", type=int)
        if name is None:
            name = click.prompt("Название")
    else:
        days = None
        name = None

    import asyncio
    asyncio.run(_create_sub(user_id, plan_id, days, name))

def extend(key_id=None, days=None):
    if key_id is None:
        key_id = click.prompt("ID подписки", type=int)
    if days is None:
        days = click.prompt("Дней продления", type=int)
    import asyncio
    asyncio.run(_extend_sub(key_id, days))

def revoke(key_id=None):
    if key_id is None:
        key_id = click.prompt("ID подписки", type=int)
    import asyncio
    asyncio.run(_revoke_sub(key_id))
