"""Declaring linkable accounts: checks, `/link` registration and routing."""

from typing import Any

import pytest

from guildhall.contract import (
    Accepted,
    AccountLink,
    Context,
    ContractModel,
    Job,
    JobType,
    LinkedAccount,
    LinkRequest,
    Outcome,
    RateLimitPolicy,
    Rejected,
    SourcePlugin,
)
from guildhall.platform.checks import check_declarations
from guildhall.platform.command_jobs import command_routes
from guildhall.platform.command_schema import all_commands, link_command
from guildhall.platform.registry import RegistryError, build_registry


async def accept(request: LinkRequest, ctx: Context) -> Accepted | Rejected:
    return Accepted(external_id=request.external_id)


class Other(ContractModel):
    pass


class Linking(SourcePlugin):
    name = "linking"
    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)
    kind = "game"
    on_link: str | None = "backfill"

    def job_types(self) -> list[JobType[Any]]:
        return [
            JobType("backfill", LinkedAccount, self.noop),
            JobType("other", Other, self.noop),
        ]

    async def noop(self, job: Job[Any], ctx: Context) -> Outcome:
        return Outcome()

    def account_link(self) -> AccountLink | None:
        return AccountLink(self.kind, "Your game ID", accept, on_link=self.on_link)


def test_a_well_formed_link_passes() -> None:
    assert check_declarations(Linking()) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"kind": "Game"}, "kind 'Game' must match"),
        ({"on_link": "missing"}, "'missing' is not a declared job type"),
        ({"on_link": "other"}, "must take LinkedAccount as its payload"),
    ],
    ids=["bad-kind", "undeclared-job", "wrong-payload"],
)
def test_malformed_links_are_reported(change: dict[str, Any], expected: str) -> None:
    plugin = Linking()
    for name, value in change.items():
        setattr(plugin, name, value)
    assert any(expected in problem for problem in check_declarations(plugin))


def test_a_link_without_a_follow_up_job_passes() -> None:
    plugin = Linking()
    plugin.on_link = None
    assert check_declarations(plugin) == []


class SameKind(Linking):
    name = "same_kind"


def test_two_plugins_cannot_link_the_same_kind() -> None:
    catalogue: dict[str, type[SourcePlugin]] = {
        "linking": Linking,
        "same_kind": SameKind,
    }
    with pytest.raises(RegistryError) as raised:
        build_registry(catalogue, ["linking", "same_kind"])
    assert any(
        "/link game is declared by more than one plug-in" in p
        for p in raised.value.problems
    )


def test_link_is_registered_with_one_subcommand_per_kind() -> None:
    link = Linking().account_link()
    assert link is not None
    assert link_command([link]) == {
        "type": 1,
        "name": "link",
        "description": "Link your game accounts",
        "options": [
            {
                "type": 1,
                "name": "game",
                "description": "Link your game account",
                "options": [
                    {
                        "type": 3,
                        "name": "account",
                        "description": "Your game ID",
                        "required": True,
                    }
                ],
            }
        ],
    }


def test_without_links_there_is_no_link_command() -> None:
    assert link_command([]) is None


def test_link_joins_the_registered_commands() -> None:
    names = [command["name"] for command in all_commands({"linking": Linking()})]
    assert names == ["link"]


def test_link_routes_to_its_plugin_and_is_always_private() -> None:
    route = command_routes({"linking": Linking()})[("link", "game")]
    assert (route.plugin, route.private, route.command) == ("linking", True, None)
    assert route.link is not None
    assert route.link.kind == "game"
