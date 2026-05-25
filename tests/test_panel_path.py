import pytest

from app.core.panel_path import (
    generate_panel_path,
    is_panel_path,
    normalize_panel_path,
    panel_path,
    panel_prefix,
)


def test_normalize_panel_path_adds_slashes():
    assert normalize_panel_path("x7k/panel") == "/x7k/panel/"


def test_normalize_panel_path_rejects_reserved_root_segment():
    with pytest.raises(ValueError):
        normalize_panel_path("/api/panel/")


def test_panel_prefix_and_panel_path_are_consistent():
    assert panel_prefix("/x7k/panel/") == "/x7k/panel"
    assert panel_path("/x7k/panel/", "login") == "/x7k/panel/login"


def test_is_panel_path_matches_prefix_only():
    assert is_panel_path("/x7k/panel/", "/x7k/panel/")
    assert is_panel_path("/x7k/panel/users", "/x7k/panel/")
    assert not is_panel_path("/x7k/panelized", "/x7k/panel/")


def test_generate_panel_path_uses_hidden_panel_suffix():
    generated = generate_panel_path(4)

    assert generated.endswith("/panel/")
    assert generated.startswith("/")
    assert 1 <= len(generated.strip("/").split("/")[0]) <= 4
