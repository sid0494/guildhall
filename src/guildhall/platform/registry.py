"""Turns the enabled plug-in names into checked plug-in instances.

The registry never imports plug-ins itself: it receives a catalogue of
available plug-in classes from the composition root.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from guildhall.contract import SourcePlugin
from guildhall.platform.checks import check_command_owners, check_declarations


class RegistryError(Exception):
    """The enabled plug-ins cannot be loaded; `problems` lists every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(
            "cannot load plug-ins:\n" + "\n".join(f"- {p}" for p in problems)
        )


def build_registry(
    catalogue: Mapping[str, type[SourcePlugin]], enabled: Sequence[str]
) -> Mapping[str, SourcePlugin]:
    """Instantiate and check each enabled plug-in, keyed by name.

    Raises RegistryError listing every problem: unknown or repeated names, a
    catalogue entry whose plug-in reports a different name, every failed
    declaration check, and a top-level command declared by two plug-ins.
    """
    problems: list[str] = []
    plugins: dict[str, SourcePlugin] = {}
    available = ", ".join(sorted(catalogue)) or "none"

    for name in enabled:
        if name in plugins:
            problems.append(f"plug-in {name!r} is enabled more than once")
            continue
        plugin_class = catalogue.get(name)
        if plugin_class is None:
            problems.append(f"unknown plug-in {name!r}; available: {available}")
            continue
        plugin = plugin_class()
        if plugin.name != name:
            problems.append(
                f"catalogue entry {name!r} holds a plug-in named {plugin.name!r}"
            )
        problems.extend(f"{name}: {problem}" for problem in check_declarations(plugin))
        plugins[name] = plugin

    problems.extend(
        check_command_owners(
            {name: plugin.commands() for name, plugin in plugins.items()}
        )
    )
    if problems:
        raise RegistryError(problems)
    return MappingProxyType(plugins)
