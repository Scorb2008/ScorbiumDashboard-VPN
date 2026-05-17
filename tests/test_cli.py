from click.testing import CliRunner

from app.cli.main import cli


def test_cli_without_subcommand_opens_menu() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, input="0\n")

    assert result.exit_code == 0
    assert "Выберите раздел:" in result.output
    assert "До свидания!" in result.output


def test_bot_help_lists_management_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["bot", "--help"])

    assert result.exit_code == 0
    assert "settings" in result.output
    assert "get" in result.output
    assert "set" in result.output


def test_users_info_passes_user_id(monkeypatch) -> None:
    captured = {}

    def fake_info(user_id=None):
        captured["user_id"] = user_id

    monkeypatch.setattr("app.cli.users.info", fake_info)
    runner = CliRunner()

    result = runner.invoke(cli, ["users", "info", "42"])

    assert result.exit_code == 0
    assert captured == {"user_id": 42}


def test_subs_create_passes_options(monkeypatch) -> None:
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.cli.subs.create", fake_create)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["subs", "create", "--user-id", "7", "--days", "30", "--name", "Promo Plan"],
    )

    assert result.exit_code == 0
    assert captured == {
        "user_id": 7,
        "plan_id": None,
        "days": 30,
        "name": "Promo Plan",
    }
