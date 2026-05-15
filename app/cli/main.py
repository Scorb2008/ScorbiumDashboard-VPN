import click
import asyncio
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from click import Context
from app.cli import bootstrap_cli_environment

bootstrap_cli_environment()

console = Console()

BANNER = """
[bold cyan]╔══════════════════════════════════════════════════════════════╗[/bold cyan]
[bold cyan]║[/bold cyan]              [bold white]Scorbium VPN Dashboard CLI[/bold white]              [bold cyan]║[/bold cyan]
[bold cyan]║[/bold cyan]                [dim]Панель управления VPN[/dim]                 [bold cyan]║[/bold cyan]
[bold cyan]╚══════════════════════════════════════════════════════════════╝[/bold cyan]
"""

def show_banner():
    console.print(BANNER)

def print_success(msg: str):
    click.secho(f"✓ {msg}", fg="green", bold=True)

def print_error(msg: str):
    click.secho(f"✗ {msg}", fg="red", bold=True)

def print_warning(msg: str):
    click.secho(f"⚠ {msg}", fg="yellow", bold=True)

def print_info(msg: str):
    click.secho(f"ℹ {msg}", fg="cyan")

def create_table(title: str, columns: list) -> Table:
    table = Table(title=title, title_style="bold cyan", border_style="dim")
    for col in columns:
        if isinstance(col, tuple):
            table.add_column(col[0], style=col[1] if len(col) > 1 else "white")
        else:
            table.add_column(col)
    return table


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: Context):
    """Scorbium VPN Dashboard — CLI управление"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(menu)


@cli.command()
def menu():
    """Интерактивное главное меню"""
    show_banner()

    while True:
        click.echo("")
        click.secho("Выберите раздел:", bold=True)
        click.echo("  1. 👥 Пользователи")
        click.echo("  2. 🔑 Подписки")
        click.echo("  3. 📦 Тарифы")
        click.echo("  4. 💳 Платежи")
        click.echo("  5. 🗄️  База данных")
        click.echo("  6. 🤖 Бот")
        click.echo("  7. ⚙️  Система")
        click.echo("  0. Выход")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0", "1", "2", "3", "4", "5", "6", "7"]), show_choices=False)

        if choice == "0":
            print_info("До свидания!")
            break
        elif choice == "1":
            _menu_users()
        elif choice == "2":
            _menu_subs()
        elif choice == "3":
            _menu_plans()
        elif choice == "4":
            _menu_payments()
        elif choice == "5":
            _menu_db()
        elif choice == "6":
            _menu_bot()
        elif choice == "7":
            _menu_system()


def _menu_users():
    while True:
        click.echo("")
        click.secho("👥 ПОЛЬЗОВАТЕЛИ", bold=True, fg="cyan")
        click.echo("  1. Список пользователей")
        click.echo("  2. Поиск пользователя")
        click.echo("  3. Информация о пользователе")
        click.echo("  4. Забанить пользователя")
        click.echo("  5. Разбанить пользователя")
        click.echo("  6. Изменить баланс")
        click.echo("  7. Подарить подписку")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2","3","4","5","6","7"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.users import list_users
            list_users()
        elif choice == "2":
            from app.cli.users import search
            search()
        elif choice == "3":
            from app.cli.users import info
            info()
        elif choice == "4":
            from app.cli.users import ban
            ban()
        elif choice == "5":
            from app.cli.users import unban
            unban()
        elif choice == "6":
            from app.cli.users import balance
            balance()
        elif choice == "7":
            from app.cli.users import gift
            gift()


def _menu_subs():
    while True:
        click.echo("")
        click.secho("🔑 ПОДПИСКИ", bold=True, fg="cyan")
        click.echo("  1. Список подписок")
        click.echo("  2. Создать подписку")
        click.echo("  3. Продлить подписку")
        click.echo("  4. Отозвать подписку")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2","3","4"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.subs import list_subs
            list_subs()
        elif choice == "2":
            from app.cli.subs import create
            create()
        elif choice == "3":
            from app.cli.subs import extend
            extend()
        elif choice == "4":
            from app.cli.subs import revoke
            revoke()


def _menu_plans():
    while True:
        click.echo("")
        click.secho("📦 ТАРИФЫ", bold=True, fg="cyan")
        click.echo("  1. Список тарифов")
        click.echo("  2. Создать тариф")
        click.echo("  3. Редактировать тариф")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2","3"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.plans import list_plans
            list_plans()
        elif choice == "2":
            from app.cli.plans import create
            create()
        elif choice == "3":
            from app.cli.plans import edit
            edit()


def _menu_payments():
    while True:
        click.echo("")
        click.secho("💳 ПЛАТЕЖИ", bold=True, fg="cyan")
        click.echo("  1. Список платежей")
        click.echo("  2. Статистика платежей")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.payments import list_payments
            list_payments()
        elif choice == "2":
            from app.cli.payments import stats
            stats()


def _menu_db():
    while True:
        click.echo("")
        click.secho("🗄️  БАЗА ДАННЫХ", bold=True, fg="cyan")
        click.echo("  1. Статистика БД")
        click.echo("  2. Очистить данные пользователей")
        click.echo("  3. Запустить миграции")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2","3"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.db import stats
            stats()
        elif choice == "2":
            from app.cli.db import clear
            clear()
        elif choice == "3":
            from app.cli.db import migrate
            migrate()


def _menu_bot():
    while True:
        click.echo("")
        click.secho("🤖 БОТ", bold=True, fg="cyan")
        click.echo("  1. Статус бота")
        click.echo("  2. Все настройки")
        click.echo("  3. Получить настройку")
        click.echo("  4. Установить настройку")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2","3","4"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.bot import status
            status()
        elif choice == "2":
            from app.cli.bot import settings
            settings()
        elif choice == "3":
            from app.cli.bot import get
            get()
        elif choice == "4":
            from app.cli.bot import set_setting
            set_setting()


def _menu_system():
    while True:
        click.echo("")
        click.secho("⚙️  СИСТЕМА", bold=True, fg="cyan")
        click.echo("  1. Проверка здоровья")
        click.echo("  2. Список администраторов")
        click.echo("  3. Добавить администратора")
        click.echo("  4. Удалить администратора")
        click.echo("  5. Показать логи")
        click.echo("  0. Назад")
        click.echo("")

        choice = click.prompt("Ваш выбор", type=click.Choice(["0","1","2","3","4","5"]), show_choices=False)

        if choice == "0":
            break
        elif choice == "1":
            from app.cli.system import health
            health()
        elif choice == "2":
            from app.cli.system import admins
            admins()
        elif choice == "3":
            from app.cli.system import add_admin
            add_admin()
        elif choice == "4":
            from app.cli.system import remove_admin
            remove_admin()
        elif choice == "5":
            from app.cli.system import logs
            logs()


# ── CLI subcommand groups ─────────────────────────────────────────────────────

@click.group()
def users():
    """Управление пользователями"""
    pass

@users.command("list")
@click.option("--limit", default=20, help="Лимит записей")
@click.option("--page", default=1, type=int, help="Номер страницы")
def users_list(limit, page):
    """Список пользователей"""
    from app.cli.users import list_users
    offset = (page - 1) * limit
    list_users(limit=limit, offset=offset, page=page)

@users.command()
@click.argument("query")
def search(query):
    """Поиск пользователя (ID, @username, или имя)"""
    from app.cli.users import search as run_search
    run_search(query)

@users.command()
@click.argument("user_id", type=int)
def info(user_id):
    """Информация о пользователе"""
    from app.cli.users import info as run_info
    run_info(user_id)

@users.command()
@click.argument("user_id", type=int)
def ban(user_id):
    """Забанить пользователя"""
    from app.cli.users import ban as run_ban
    run_ban(user_id)

@users.command()
@click.argument("user_id", type=int)
def unban(user_id):
    """Разбанить пользователя"""
    from app.cli.users import unban as run_unban
    run_unban(user_id)

@users.command()
@click.argument("user_id", type=int)
def balance(user_id):
    """Изменить баланс пользователя"""
    from app.cli.users import balance as run_balance
    run_balance(user_id)

@users.command()
def gift():
    """Подарить подписку пользователю"""
    from app.cli.users import gift
    gift()


@click.group()
def subs():
    """Управление подписками"""
    pass

@subs.command("list")
@click.option("--status", type=click.Choice(["active", "expired", "revoked", "all"]), default="active")
@click.option("--limit", default=20)
def subs_list(status, limit):
    """Список подписок"""
    from app.cli.subs import list_subs
    list_subs(status=status, limit=limit)

@subs.command("create")
@click.option("--user-id", type=int, required=True)
@click.option("--plan-id", type=int)
@click.option("--days", type=int)
@click.option("--name")
def cmd_create(user_id, plan_id, days, name):
    """Создать подписку (по тарифу или по дням)"""
    from app.cli.subs import create as run_create
    run_create(user_id=user_id, plan_id=plan_id, days=days, name=name)

@subs.command()
@click.argument("key_id", type=int)
@click.argument("days", type=int)
def extend(key_id, days):
    """Продлить подписку"""
    from app.cli.subs import extend as run_extend
    run_extend(key_id=key_id, days=days)

@subs.command()
@click.argument("key_id", type=int)
def revoke(key_id):
    """Отозвать подписку"""
    from app.cli.subs import revoke as run_revoke
    run_revoke(key_id=key_id)


@click.group()
def plans():
    """Управление тарифами"""
    pass

@plans.command("list")
def plans_list():
    """Список тарифов"""
    from app.cli.plans import list_plans
    list_plans()

@plans.command()
@click.option("--name", prompt=True)
@click.option("--duration", type=int, prompt="Дней")
@click.option("--price", type=float, prompt="Цена (₽)")
@click.option("--active/--inactive", default=True)
def create(name, duration, price, active):
    """Создать тариф"""
    from app.cli.plans import create as run_create
    run_create(name=name, duration=duration, price=price, active=active)

@plans.command()
@click.argument("plan_id", type=int)
def edit(plan_id):
    """Редактировать тариф"""
    from app.cli.plans import edit as run_edit
    run_edit(plan_id)


@click.group()
def payments():
    """Управление платежами"""
    pass

@payments.command("list")
@click.option("--limit", default=20)
def payments_list(limit):
    """Список платежей"""
    from app.cli.payments import list_payments
    list_payments(limit=limit)

@payments.command("stats")
def get_stats():
    """Статистика платежей"""
    from app.cli.payments import stats
    stats()


@click.group()
def db():
    """Управление базой данных"""
    pass

@db.command()
def stats():
    """Статистика базы данных"""
    from app.cli.db import stats
    stats()

@db.command()
def clear():
    """Очистить данные пользователей"""
    from app.cli.db import clear
    clear()

@db.command()
def migrate():
    """Запустить миграции"""
    from app.cli.db import migrate
    migrate()


@click.group()
def bot():
    """Управление ботом"""
    pass

@bot.command()
def status():
    """Статус бота"""
    from app.cli.bot import status
    status()

@bot.command()
def settings():
    """Все настройки бота"""
    from app.cli.bot import settings as run_settings
    run_settings()

@bot.command("get")
@click.argument("key")
def bot_get(key):
    """Получить настройку бота"""
    from app.cli.bot import get as run_get
    run_get(key)

@bot.command("set")
@click.argument("key")
@click.argument("value")
def bot_set(key, value):
    """Установить настройку бота"""
    from app.cli.bot import set_setting as run_set
    run_set(key, value)

@click.group()
def system():
    """Системные команды"""
    pass

@system.command()
def health():
    """Проверка здоровья системы"""
    from app.cli.system import health
    health()

@system.command()
def admins():
    """Список администраторов"""
    from app.cli.system import admins
    admins()

@system.command("add-admin")
@click.option("--tg-id", type=int, prompt=True)
@click.option("--role", default="admin", prompt=True)
@click.option("--name", prompt=True)
def system_add_admin(tg_id, role, name):
    """Добавить администратора"""
    from app.cli.system import add_admin as run_add_admin
    run_add_admin(tg_id=tg_id, role=role, name=name)

@system.command("remove-admin")
@click.argument("admin_id", type=int)
def system_remove_admin(admin_id):
    """Удалить администратора"""
    from app.cli.system import remove_admin as run_remove_admin
    run_remove_admin(admin_id)

@system.command()
@click.option("--lines", default=50)
def logs(lines):
    """Показать логи"""
    from app.cli.system import logs as run_logs
    run_logs(lines)


# Register groups
cli.add_command(users)
cli.add_command(subs)
cli.add_command(plans)
cli.add_command(payments)
cli.add_command(db)
cli.add_command(bot)
cli.add_command(system)

if __name__ == "__main__":
    cli()
