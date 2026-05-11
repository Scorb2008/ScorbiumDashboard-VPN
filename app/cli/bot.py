import click
import asyncio
from rich.console import Console
from rich.table import Table
from datetime import datetime

console = Console()

async def _bot_status():
    from app.core.database import AsyncSessionFactory
    from app.models.admin import Admin
    from app.models.bot_settings import BotSettings
    from app.core.config import config
    from sqlalchemy import select, func
    
    async with AsyncSessionFactory() as session:
        # Admin count
        stmt = select(func.count(Admin.id))
        result = await session.execute(stmt)
        admin_count = result.scalar()
        
        # Settings count
        stmt = select(func.count(BotSettings.id))
        result = await session.execute(stmt)
        settings_count = result.scalar()
        
        click.echo("")
        click.secho("СТАТУС БОТА", bold=True, fg="cyan")
        click.echo("=" * 50)
        
        bot_token = config.telegram.bot_token if hasattr(config, 'telegram') else None
        if bot_token:
            click.secho("  ✓ Токен бота: настроен", fg="green")
        else:
            click.secho("  ✗ Токен бота: НЕ настроен", fg="red")
        
        click.echo(f"  Администраторов: {admin_count}")
        click.echo(f"  Настроек: {settings_count}")
        
        protocol = config.telegram.type_protocol if hasattr(config, 'telegram') else "unknown"
        click.echo(f"  Протокол: {protocol}")
        
        click.echo("")
        click.secho("Статус: Работает", fg="green", bold=True)

async def _bot_settings():
    from app.core.database import AsyncSessionFactory
    from app.models.bot_settings import BotSettings
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(BotSettings).order_by(BotSettings.key)
        result = await session.execute(stmt)
        settings = result.scalars().all()
        
        if not settings:
            click.secho("Настройки бота не найдены", fg="yellow")
            return
        
        table = Table(title="Настройки бота", border_style="cyan")
        table.add_column("Ключ")
        table.add_column("Значение")
        table.add_column("Описание")
        
        for setting in settings:
            value_display = setting.value[:50] + "..." if len(setting.value) > 50 else setting.value
            table.add_row(
                setting.key,
                value_display,
                setting.description or "-"
            )
        
        console.print(table)

async def _get_setting(key: str):
    from app.core.database import AsyncSessionFactory
    from app.models.bot_settings import BotSettings
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(BotSettings).where(BotSettings.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        
        if not setting:
            click.secho(f"Настройка '{key}' не найдена", fg="red")
            return
        
        click.echo("")
        click.secho(f"НАСТРОЙКА: {setting.key}", bold=True, fg="cyan")
        click.echo("=" * 50)
        click.echo(f"Значение: {setting.value}")
        if setting.description:
            click.echo(f"Описание: {setting.description}")
        click.echo(f"Обновлено: {setting.updated_at.strftime('%Y-%m-%d %H:%M:%S') if setting.updated_at else '-'}")

async def _set_setting(key: str, value: str):
    from app.core.database import AsyncSessionFactory
    from app.models.bot_settings import BotSettings
    from sqlalchemy import select
    
    async with AsyncSessionFactory() as session:
        stmt = select(BotSettings).where(BotSettings.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        
        if setting:
            old_value = setting.value
            setting.value = value
            click.secho(f"✓ Настройка '{key}' обновлена", fg="green", bold=True)
            click.echo(f"  Старое значение: {old_value}")
            click.echo(f"  Новое значение: {value}")
        else:
            setting = BotSettings(key=key, value=value)
            session.add(setting)
            click.secho(f"✓ Настройка '{key}' создана", fg="green", bold=True)
            click.echo(f"  Значение: {value}")
        
        await session.commit()

def status():
    import asyncio
    asyncio.run(_bot_status())

def settings():
    import asyncio
    asyncio.run(_bot_settings())

def get():
    key = click.prompt("Ключ настройки")
    import asyncio
    asyncio.run(_get_setting(key))

def set_setting():
    key = click.prompt("Ключ настройки")
    value = click.prompt("Значение")
    import asyncio
    asyncio.run(_set_setting(key, value))
