import click
from rich.console import Console
from rich.table import Table
from app.cli import run_cli_async

console = Console()


async def _list_plans():
    from app.core.database import AsyncSessionFactory
    from app.models.plan import Plan
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        stmt = select(Plan).order_by(Plan.id)
        result = await session.execute(stmt)
        plans = result.scalars().all()

        if not plans:
            click.secho("Нет доступных тарифов", fg="yellow")
            return

        table = Table(title="Тарифы", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Название")
        table.add_column("Длительность")
        table.add_column("Цена", justify="right")
        table.add_column("Статус")

        for plan in plans:
            status = (
                "[green]Активен[/green]" if plan.is_active else "[red]Неактивен[/red]"
            )
            table.add_row(
                str(plan.id),
                plan.name,
                f"{plan.duration_days} дн.",
                f"{plan.price:.2f}",
                status,
            )

        console.print(table)


async def _create_plan(name: str, duration_days: int, price: float, is_active: bool):
    from app.core.database import AsyncSessionFactory
    from app.models.plan import Plan

    async with AsyncSessionFactory() as session:
        plan = Plan(
            name=name, duration_days=duration_days, price=price, is_active=is_active
        )

        session.add(plan)
        await session.commit()

        click.secho(f"✓ Тариф '{name}' создан (ID: {plan.id})", fg="green", bold=True)
        click.echo(f"  Длительность: {duration_days} дней")
        click.echo(f"  Цена: {price:.2f}")
        click.echo(f"  Статус: {'Активен' if is_active else 'Неактивен'}")


async def _edit_plan(plan_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.plan import Plan
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        stmt = select(Plan).where(Plan.id == plan_id)
        result = await session.execute(stmt)
        plan = result.scalar_one_or_none()

        if not plan:
            click.secho(f"Тариф с ID {plan_id} не найден", fg="red")
            return

        click.echo(f"Редактирование тарифа: {plan.name} (ID: {plan.id})")
        click.echo("")
        click.echo(f"1. Название: {plan.name}")
        click.echo(f"2. Длительность: {plan.duration_days} дней")
        click.echo(f"3. Цена: {plan.price:.2f}")
        click.echo(f"4. Статус: {'Активен' if plan.is_active else 'Неактивен'}")
        click.echo("")

        field = click.prompt("Что изменить? (1-4, или 0 для отмены)", type=int)

        if field == 0:
            click.secho("Отменено", fg="yellow")
            return
        elif field == 1:
            new_name = click.prompt("Новое название", default=plan.name)
            plan.name = new_name
        elif field == 2:
            new_duration = click.prompt(
                "Новая длительность (дней)", type=int, default=plan.duration_days
            )
            plan.duration_days = new_duration
        elif field == 3:
            new_price = click.prompt("Новая цена", type=float, default=plan.price)
            plan.price = new_price
        elif field == 4:
            new_active = click.confirm("Сделать активным?", default=plan.is_active)
            plan.is_active = new_active
        else:
            click.secho("Неверный выбор", fg="red")
            return

        await session.commit()
        click.secho(f"✓ Тариф {plan_id} обновлен", fg="green", bold=True)


def list_plans():
    run_cli_async(_list_plans())


def create(name=None, duration=None, price=None, active=None):
    if name is None:
        name = click.prompt("Название тарифа")
    if duration is None:
        duration = click.prompt("Длительность (дней)", type=int)
    if price is None:
        price = click.prompt("Цена", type=float)
    if active is None:
        active = click.confirm("Активен?", default=True)

    run_cli_async(_create_plan(name, duration, price, active))


def edit(plan_id=None):
    if plan_id is None:
        plan_id = click.prompt("ID тарифа", type=int)
    run_cli_async(_edit_plan(plan_id))
