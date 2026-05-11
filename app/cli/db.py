import click
import asyncio
import subprocess
from rich.console import Console
from rich.table import Table

console = Console()

async def _db_stats():
    from app.core.database import AsyncSessionFactory
    from app.models.user import User
    from app.models.vpn_key import VpnKey
    from app.models.payment import Payment
    from app.models.plan import Plan
    from app.models.admin import Admin
    from app.models.bot_settings import BotSettings
    from sqlalchemy import select, func
    
    async with AsyncSessionFactory() as session:
        # User count
        stmt = select(func.count(User.id))
        result = await session.execute(stmt)
        user_count = result.scalar()
        
        # Active users (with active subs)
        stmt = select(func.count(func.distinct(VpnKey.user_id))).where(VpnKey.status == "active")
        result = await session.execute(stmt)
        active_users = result.scalar()
        
        # Active subscriptions
        stmt = select(func.count(VpnKey.id)).where(VpnKey.status == "active")
        result = await session.execute(stmt)
        active_subs = result.scalar()
        
        # Total subscriptions
        stmt = select(func.count(VpnKey.id))
        result = await session.execute(stmt)
        total_subs = result.scalar()
        
        # Payments stats
        stmt = select(func.count(Payment.id)).where(Payment.status == "success")
        result = await session.execute(stmt)
        success_payments = result.scalar()
        
        stmt = select(func.sum(Payment.amount)).where(Payment.status == "success")
        result = await session.execute(stmt)
        total_revenue = result.scalar() or 0
        
        # Plans count
        stmt = select(func.count(Plan.id)).where(Plan.is_active == True)
        result = await session.execute(stmt)
        active_plans = result.scalar()
        
        # Admins count
        stmt = select(func.count(Admin.id))
        result = await session.execute(stmt)
        admin_count = result.scalar()
        
        # Bot settings count
        stmt = select(func.count(BotSettings.id))
        result = await session.execute(stmt)
        settings_count = result.scalar()
        
        click.echo("")
        click.secho("СТАТИСТИКА БАЗЫ ДАННЫХ", bold=True, fg="cyan")
        click.echo("=" * 50)
        click.echo("")
        click.secho("ПОЛЬЗОВАТЕЛИ:", bold=True)
        click.echo(f"  Всего: {user_count}")
        click.echo(f"  С активными подписками: {active_users}")
        click.echo("")
        click.secho("ПОДПИСКИ:", bold=True)
        click.echo(f"  Активных: {active_subs}")
        click.echo(f"  Всего: {total_subs}")
        click.echo("")
        click.secho("ПЛАТЕЖИ:", bold=True)
        click.echo(f"  Успешных: {success_payments}")
        click.echo(f"  Общая выручка: {total_revenue:.2f}")
        click.echo("")
        click.secho("ПРОЧЕЕ:", bold=True)
        click.echo(f"  Активных тарифов: {active_plans}")
        click.echo(f"  Администраторов: {admin_count}")
        click.echo(f"  Настроек бота: {settings_count}")

async def _clear_data():
    from app.core.database import AsyncSessionFactory
    from sqlalchemy import text
    
    click.secho("⚠️  ВНИМАНИЕ: ОЧИСТКА ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ", fg="yellow", bold=True)
    click.echo("")
    click.echo("Будут удалены:")
    click.echo("  • Пользователи (users)")
    click.echo("  • VPN ключи (vpn_keys)")
    click.echo("  • Платежи (payments)")
    click.echo("  • Тикеты поддержки (support_tickets)")
    click.echo("  • Сообщения тикетов (ticket_messages)")
    click.echo("  • Рефералы (referrals)")
    click.echo("")
    click.echo("Будут сохранены:")
    click.echo("  • Администраторы (admins)")
    click.echo("  • Настройки бота (bot_settings)")
    click.echo("  • Тарифы (plans)")
    click.echo("  • Промокоды (promo_codes)")
    click.echo("  • Рассылки (broadcasts)")
    click.echo("")
    
    if not click.confirm("Вы уверены, что хотите продолжить?", default=False):
        click.secho("Отменено", fg="yellow")
        return
    
    if not click.confirm("Это действие необратимо! Вы действительно хотите удалить данные?", default=False):
        click.secho("Отменено", fg="yellow")
        return
    
    async with AsyncSessionFactory() as session:
        try:
            # Delete in correct order to respect foreign keys
            await session.execute(text("DELETE FROM referrals"))
            click.echo("  ✓ Удалены рефералы")
            
            await session.execute(text("DELETE FROM ticket_messages"))
            click.echo("  ✓ Удалены сообщения тикетов")
            
            await session.execute(text("DELETE FROM support_tickets"))
            click.echo("  ✓ Удалены тикеты поддержки")
            
            await session.execute(text("DELETE FROM payments"))
            click.echo("  ✓ Удалены платежи")
            
            await session.execute(text("DELETE FROM vpn_keys"))
            click.echo("  ✓ Удалены VPN ключи")
            
            await session.execute(text("DELETE FROM users"))
            click.echo("  ✓ Удалены пользователи")
            
            await session.commit()
            
            click.secho("✓ Данные пользователей успешно очищены", fg="green", bold=True)
        except Exception as e:
            await session.rollback()
            click.secho(f"Ошибка при очистке данных: {e}", fg="red")
            raise

async def _migrate():
    click.echo("Запуск миграций...")
    click.echo("")
    
    click.echo("1. Запуск fix_alembic.py...")
    try:
        result = subprocess.run(
            ["uv", "run", "python", "fix_alembic.py"],
            cwd="/Users/itsskramb/ScorbiumDashboard",
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            click.secho("  ✓ fix_alembic.py выполнен успешно", fg="green")
        else:
            click.secho(f"  ✗ Ошибка: {result.stderr}", fg="red")
            return
    except Exception as e:
        click.secho(f"  ✗ Ошибка: {e}", fg="red")
        return
    
    click.echo("")
    click.echo("2. Запуск alembic upgrade head...")
    try:
        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd="/Users/itsskramb/ScorbiumDashboard",
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            click.secho("  ✓ Миграции применены успешно", fg="green", bold=True)
        else:
            click.secho(f"  ✗ Ошибка: {result.stderr}", fg="red")
    except Exception as e:
        click.secho(f"  ✗ Ошибка: {e}", fg="red")

def stats():
    import asyncio
    asyncio.run(_db_stats())

def clear():
    import asyncio
    asyncio.run(_clear_data())

def migrate():
    import asyncio
    asyncio.run(_migrate())
