import click
from rich.console import Console
from rich.table import Table
from datetime import datetime
from app.cli import run_cli_async

console = Console()


def _telegram_token_value():
    from app.core.config import config

    telegram = getattr(config, "telegram", None)
    secret = getattr(telegram, "telegram_bot_token", None)
    return secret.get_secret_value() if secret else ""


def _telegram_protocol():
    from app.core.config import config

    telegram = getattr(config, "telegram", None)
    return getattr(telegram, "telegram_type_protocol", "unknown")

async def _bot_status():
    from app.core.database import AsyncSessionFactory
    from app.models.admin import Admin
    from app.models.bot_settings import BotSettings
    from app.core.config import config
    from sqlalchemy import select, func
    
    async with AsyncSessionFactory() as session:
        stmt = select(func.count(Admin.id))
        result = await session.execute(stmt)
        admin_count = result.scalar()
        
        stmt = select(func.count(BotSettings.id))
        result = await session.execute(stmt)
        settings_count = result.scalar()
        
        click.echo("")
        click.secho("СТАТУС БОТА", bold=True, fg="cyan")
        click.echo("=" * 50)
        
        bot_token = _telegram_token_value()
        if bot_token:
            click.secho("  ✓ Токен бота: настроен", fg="green")
        else:
            click.secho("  ✗ Токен бота: НЕ настроен", fg="red")
        
        click.echo(f"  Администраторов: {admin_count}")
        click.echo(f"  Настроек: {settings_count}")
        
        protocol = _telegram_protocol()
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
    run_cli_async(_bot_status())

def settings():
    run_cli_async(_bot_settings())

def get(key=None):
    if key is None:
        key = click.prompt("Ключ настройки")
    run_cli_async(_get_setting(key))

def set_setting(key=None, value=None):
    if key is None:
        key = click.prompt("Ключ настройки")
    if value is None:
        value = click.prompt("Значение")
    run_cli_async(_set_setting(key, value))
