"""Unit tests for the composition root and the plug-in catalogue."""

import pytest

from guildhall.bootstrap import load_plugins
from guildhall.platform.registry import RegistryError
from guildhall.platform.settings import Settings
from guildhall.plugins import CATALOGUE
from guildhall.plugins.synthetic import SyntheticPlugin


def test_catalogue_keys_are_plugin_names() -> None:
    assert all(name == plugin().name for name, plugin in CATALOGUE.items())


def test_enabled_plugins_are_loaded_from_settings() -> None:
    plugins = load_plugins(Settings(plugins=("synthetic",)))
    assert isinstance(plugins["synthetic"], SyntheticPlugin)


def test_unknown_plugin_in_settings_stops_loading() -> None:
    with pytest.raises(RegistryError, match="unknown plug-in 'syntetic'"):
        load_plugins(Settings(plugins=("syntetic",)))
