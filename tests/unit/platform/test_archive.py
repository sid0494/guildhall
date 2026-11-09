"""`Context.archive` and the key layout, with an in-memory store (ADR 0031, 0032)."""

from dataclasses import dataclass, field

import pytest

from guildhall.platform.archive import PluginArchive, archive_key
from guildhall.platform.checks import ContractViolationError
from guildhall.plugins.synthetic import SyntheticPlugin


@dataclass
class MemoryStore:
    objects: dict[str, bytes] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    async def put(self, key: str, body: bytes) -> None:
        self.objects[key] = body

    async def get(self, key: str) -> bytes | None:
        self.reads.append(key)
        return self.objects.get(key)


def test_keys_are_grouped_by_plugin_then_kind() -> None:
    assert (
        archive_key("gank_report", "match", "8123") == "raw/gank_report/match/8123.json"
    )


async def test_reads_come_from_the_plugins_own_folder() -> None:
    store = MemoryStore(
        {"raw/synthetic/work/a": b"wrong", "raw/synthetic/work/a.json": b"{}"}
    )
    assert await PluginArchive(store, SyntheticPlugin()).get("work", "a") == b"{}"


async def test_a_missing_response_reads_as_none() -> None:
    assert (
        await PluginArchive(MemoryStore(), SyntheticPlugin()).get("work", "a") is None
    )


async def test_reading_outside_the_plugins_folder_is_a_contract_violation() -> None:
    store = MemoryStore()
    with pytest.raises(ContractViolationError, match="archive read archive id"):
        await PluginArchive(store, SyntheticPlugin()).get("work", "../../gank_report/x")
    assert store.reads == []
