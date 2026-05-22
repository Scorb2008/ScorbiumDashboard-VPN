import click
import subprocess
from rich.console import Console
from rich.table import Table
import shutil

from app.cli import get_repo_root, run_cli_async

console = Console()
REPO_ROOT = get_repo_root()


def _telegram_token_value():
    from app.core.config import config

    telegram = getattr(config, "telegram", None)
    secret = getattr(telegram, "telegram_bot_token", None)
    return secret.get_secret_value() if secret else ""


async def _health_check():
    from app.core.database import AsyncSessionFactory
    from sqlalchemy import text

    click.echo("")
    click.secho("ПРОВЕРКА ЗДОРОВЬЯ СИСТЕМЫ", bold=True, fg="cyan")
    click.echo("=" * 50)
    click.echo("")

    # Check database connection
    click.echo("Проверка подключения к БД...")
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
            click.secho("  ✓ База данных: подключение успешно", fg="green")
    except Exception as e:
        click.secho(f"  ✗ База данных: ошибка подключения - {e}", fg="red")

    # Check bot token
    click.echo("")
    click.echo("Проверка токена бота...")
    try:
        bot_token = _telegram_token_value()
        if bot_token:
            click.secho("  ✓ Токен бота: настроен", fg="green")
        else:
            click.secho("  ✗ Токен бота: НЕ настроен", fg="red")
    except Exception as e:
        click.secho(f"  ✗ Токен бота: ошибка - {e}", fg="red")

    # Check admin IDs
    click.echo("")
    click.echo("Проверка администраторов...")
    try:
        from app.models.admin import Admin
        from sqlalchemy import select, func

        async with AsyncSessionFactory() as session:
            stmt = select(func.count(Admin.id))
            result = await session.execute(stmt)
            admin_count = result.scalar()

            if admin_count > 0:
                click.secho(f"  ✓ Администраторов: {admin_count}", fg="green")
            else:
                click.secho("  ⚠ Администраторов: нет", fg="yellow")
    except Exception as e:
        click.secho(f"  ✗ Администраторы: ошибка - {e}", fg="red")

    click.echo("")


async def _list_admins():
    from app.core.database import AsyncSessionFactory
    from app.models.admin import Admin
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        stmt = select(Admin).order_by(Admin.id)
        result = await session.execute(stmt)
        admins = result.scalars().all()

        if not admins:
            click.secho("Нет администраторов", fg="yellow")
            return

        table = Table(title="Администраторы", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("TG ID")
        table.add_column("Имя")
        table.add_column("Роль")
        table.add_column("Создан")

        for admin in admins:
            table.add_row(
                str(admin.id),
                str(admin.tg_id),
                admin.name or "-",
                admin.role or "admin",
                admin.created_at.strftime("%Y-%m-%d") if admin.created_at else "-",
            )

        console.print(table)


async def _add_admin(tg_id: int, role: str, name: str):
    from app.core.database import AsyncSessionFactory
    from app.models.admin import Admin
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        # Check if already exists
        stmt = select(Admin).where(Admin.tg_id == tg_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            click.secho(f"Администратор с TG ID {tg_id} уже существует", fg="yellow")
            return

        admin = Admin(tg_id=tg_id, role=role, name=name)

        session.add(admin)
        await session.commit()

        click.secho(f"✓ Администратор добавлен (ID: {admin.id})", fg="green", bold=True)
        click.echo(f"  TG ID: {tg_id}")
        click.echo(f"  Имя: {name}")
        click.echo(f"  Роль: {role}")


async def _remove_admin(admin_id: int):
    from app.core.database import AsyncSessionFactory
    from app.models.admin import Admin
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        stmt = select(Admin).where(Admin.id == admin_id)
        result = await session.execute(stmt)
        admin = result.scalar_one_or_none()

        if not admin:
            click.secho(f"Администратор с ID {admin_id} не найден", fg="red")
            return

        click.echo(f"Администратор: {admin.name} (TG ID: {admin.tg_id})")

        if not click.confirm(
            "Вы уверены, что хотите удалить этого администратора?", default=False
        ):
            click.secho("Отменено", fg="yellow")
            return

        await session.delete(admin)
        await session.commit()

        click.secho(f"✓ Администратор {admin_id} удален", fg="green", bold=True)


async def _show_logs(lines: int):
    click.echo(f"Последние {lines} строк логов:")
    click.echo("")

    log_dir = REPO_ROOT / "logs"

    def _print_log_files() -> bool:
        if not log_dir.exists():
            return False

        candidates = sorted(
            [path for path in log_dir.glob("app_*.log*") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return False

        shown = 0
        for path in candidates:
            if path.suffix == ".zip":
                continue

            try:
                with path.open("r", encoding="utf-8", errors="ignore") as f:
                    chunk = f.readlines()
            except OSError:
                continue

            remaining = lines - shown
            if remaining <= 0:
                break

            tail = chunk[-remaining:]
            for line in tail:
                click.echo(line.rstrip())
            shown += len(tail)

            if shown >= lines:
                break

        return shown > 0

    try:
        if shutil.which("docker"):
            result = subprocess.run(
                ["docker", "compose", "logs", "--tail", str(lines), "app"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                click.echo(result.stdout)
                return

        if _print_log_files():
            return
        else:
            click.secho(
                "Логи недоступны (docker недоступен и файлов логов не найдено)",
                fg="yellow",
            )
    except Exception as e:
        click.secho(f"Ошибка при чтении логов: {e}", fg="red")


def health():
    run_cli_async(_health_check())


def admins():
    run_cli_async(_list_admins())


def add_admin(tg_id=None, role="admin", name=None):
    if tg_id is None:
        tg_id = click.prompt("Telegram ID", type=int)
    if role is None:
        role = click.prompt("Роль", default="admin")
    if name is None:
        name = click.prompt("Имя")
    run_cli_async(_add_admin(tg_id, role, name))


def remove_admin(admin_id=None):
    if admin_id is None:
        admin_id = click.prompt("ID администратора", type=int)
    run_cli_async(_remove_admin(admin_id))


def logs(lines=50):
    if lines is None:
        lines = click.prompt("Количество строк", type=int, default=50)
    run_cli_async(_show_logs(lines))
