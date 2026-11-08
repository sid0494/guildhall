"""The composition root: the one place that wires plug-ins into the platform.

The platform never imports plug-ins and plug-ins never import the platform;
entry points such as the worker call these functions to join the two.
"""

from collections.abc import Mapping

from guildhall.contract import SourcePlugin
from guildhall.platform.registry import build_registry
from guildhall.platform.settings import Settings
from guildhall.plugins import CATALOGUE


def load_plugins(settings: Settings) -> Mapping[str, SourcePlugin]:
    """The enabled plug-ins, checked, keyed by name."""
    return build_registry(CATALOGUE, settings.plugins)
