import click
from rich.console import Console
from rich.table import Table
from datetime import datetime, timedelta
from decimal import Decimal
from app.cli import run_cli_async

console = Console()

STATUS_MAP = {
    "succeeded": "Успешно",
    "pending": "Ожидание",
    "failed": "Ошибка",
    "cancelled": "Отменен",
    "refunded": "Возврат",
}

STATUS_STYLE = {"success": "green", "pending": "yellow", "failed": "red"}

PROVIDER_MAP = {
    "yookassa": "ЮKassa",
    "yookassa_sbp": "ЮKassa SBP",
    "cryptobot": "CryptoBot",
    "telegram_stars": "Telegram Stars",
    "freekassa": "FreeKassa",
    "aikassa": "AiKassa",
    "platega": "Platega",
    "paypalych": "PayPalych",
    "balance": "Баланс",
    "topup": "Пополнение",
}


def _user_name(user) -> str:
    name = (getattr(user, "full_name", "") or "").strip()
    return (
        f"{name} (@{user.username})" if user.username else (name or f"User #{user.id}")
    )


async def _list_payments(limit: int):
    from app.core.database import AsyncSessionFactory
    from app.models.payment import Payment
    from app.models.user import User
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        stmt = (
            select(Payment, User)
            .join(User, Payment.user_id == User.id)
            .order_by(Payment.id.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()

        if not rows:
            click.secho("Нет платежей", fg="yellow")
            return

        table = Table(title=f"Платежи (последние {len(rows)})", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Пользователь")
        table.add_column("Провайдер")
        table.add_column("Сумма", justify="right")
        table.add_column("Статус")
        table.add_column("Дата")

        for payment, user in rows:
            status_text = STATUS_MAP.get(payment.status, payment.status)
            status_style = STATUS_STYLE.get(payment.status, "white")
            provider_text = PROVIDER_MAP.get(payment.provider, payment.provider)

            table.add_row(
                str(payment.id),
                _user_name(user),
                provider_text,
                f"{Decimal(payment.amount or 0):.2f}",
                f"[{status_style}]{status_text}[/{status_style}]",
                payment.created_at.strftime("%Y-%m-%d %H:%M")
                if payment.created_at
                else "-",
            )

        console.print(table)


async def _payment_stats():
    from app.core.database import AsyncSessionFactory
    from app.models.payment import Payment
    from sqlalchemy import select, func, and_

    async with AsyncSessionFactory() as session:
        # Total revenue
        stmt = select(func.sum(Payment.amount)).where(Payment.status == "succeeded")
        result = await session.execute(stmt)
        total_revenue = result.scalar() or 0

        # Today's revenue
        today = datetime.utcnow().date()
        stmt = select(func.sum(Payment.amount)).where(
            and_(Payment.status == "succeeded", func.date(Payment.created_at) == today)
        )
        result = await session.execute(stmt)
        today_revenue = result.scalar() or 0

        # This week's revenue
        week_start = datetime.utcnow() - timedelta(days=7)
        stmt = select(func.sum(Payment.amount)).where(
            and_(Payment.status == "succeeded", Payment.created_at >= week_start)
        )
        result = await session.execute(stmt)
        week_revenue = result.scalar() or 0

        # This month's revenue
        month_start = datetime.utcnow().replace(day=1)
        stmt = select(func.sum(Payment.amount)).where(
            and_(Payment.status == "succeeded", Payment.created_at >= month_start)
        )
        result = await session.execute(stmt)
        month_revenue = result.scalar() or 0

        # By provider
        stmt = (
            select(Payment.provider, func.sum(Payment.amount), func.count(Payment.id))
            .where(Payment.status == "succeeded")
            .group_by(Payment.provider)
        )
        result = await session.execute(stmt)
        by_provider = result.all()

        # Total payments count
        stmt = select(func.count(Payment.id))
        result = await session.execute(stmt)
        total_count = result.scalar()

        # Success rate
        stmt = select(func.count(Payment.id)).where(Payment.status == "succeeded")
        result = await session.execute(stmt)
        success_count = result.scalar()

        click.echo("")
        click.secho("СТАТИСТИКА ПЛАТЕЖЕЙ", bold=True, fg="cyan")
        click.echo("=" * 50)
        click.echo(f"Всего платежей: {total_count}")
        click.echo(
            f"Успешных: {success_count} ({success_count / total_count * 100:.1f}%)"
            if total_count > 0
            else "Успешных: 0"
        )
        click.echo("")
        click.echo(f"Общая выручка: {total_revenue:.2f}")
        click.echo(f"Сегодня: {today_revenue:.2f}")
        click.echo(f"За неделю: {week_revenue:.2f}")
        click.echo(f"За месяц: {month_revenue:.2f}")

        if by_provider:
            click.echo("")
            click.secho("По провайдерам:", bold=True)
            for provider, amount, count in by_provider:
                provider_text = PROVIDER_MAP.get(provider, provider)
                click.echo(f"  {provider_text}: {amount:.2f} ({count} платежей)")


def list_payments(limit=20):
    run_cli_async(_list_payments(limit))


def stats():
    run_cli_async(_payment_stats())
