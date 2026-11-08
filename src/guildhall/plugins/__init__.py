"""Plug-ins hosted by the platform, and the catalogue of those available.

Each plug-in imports only `guildhall.contract`. Adding a plug-in means adding
one entry to `CATALOGUE`; enabling it is a configuration change.
"""

from collections.abc import Mapping
from types import MappingProxyType

from guildhall.contract import SourcePlugin
from guildhall.plugins.synthetic import SyntheticPlugin

CATALOGUE: Mapping[str, type[SourcePlugin]] = MappingProxyType(
    {
        "synthetic": SyntheticPlugin,
    }
)
