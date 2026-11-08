"""Unit tests for settings loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from guildhall.platform.settings import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run each test in an empty directory with no GUILDHALL_ variables set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GUILDHALL_PLUGINS", raising=False)
    return tmp_path


def test_plugins_are_read_from_a_comma_separated_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GUILDHALL_PLUGINS", " gank_report , side_quest ,")
    assert Settings().plugins == ("gank_report", "side_quest")


def test_plugins_are_required() -> None:
    with pytest.raises(ValidationError, match="plugins"):
        Settings()


def test_at_least_one_plugin_must_be_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUILDHALL_PLUGINS", " , ")
    with pytest.raises(ValidationError, match="at least 1 item"):
        Settings()


def test_plugins_are_read_from_a_dotenv_file(isolated_environment: Path) -> None:
    (isolated_environment / ".env").write_text("GUILDHALL_PLUGINS=synthetic\n")
    assert Settings().plugins == ("synthetic",)


def test_environment_variables_override_the_dotenv_file(
    isolated_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_environment / ".env").write_text("GUILDHALL_PLUGINS=synthetic\n")
    monkeypatch.setenv("GUILDHALL_PLUGINS", "gank_report")
    assert Settings().plugins == ("gank_report",)


def test_unprefixed_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLUGINS", "synthetic")
    with pytest.raises(ValidationError, match="plugins"):
        Settings()
