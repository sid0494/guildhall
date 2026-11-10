"""Record OpenDota responses as test fixtures: trimmed, with fake IDs.

Run by hand, never by tests (docs/development.md, "Recorded fixtures"):

    uv run python scripts/record_opendota.py

It records the configured account's match list and its most recent unparsed
match, before and after asking OpenDota to parse it, so tests can replay the
parse-retry path on one match (docs/adr/0039, 0040).

Each response is trimmed to the fields its shape names, so a field OpenDota
adds later is dropped, not recorded. The match and account IDs that remain
are replaced with fakes from fixed ranges (docs/adr/0041), which
tests/unit/test_recorded_fixtures.py checks (docs/adr/0042).
"""

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENDOTA_URL: Final = "https://api.opendota.com/api"
FIXTURES_DIR: Final = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "opendota"
)

KEEP: Final = None
"""In a shape, keep the field's value as it is (a scalar or a list of scalars)."""

type Shape = Mapping[str, Shape | None]
"""The fields to keep: each maps to KEEP or to the shape of a nested object.

A shape applied to a list applies to each of its items.
"""

MATCH_LIST_SHAPE: Final[Shape] = {
    "match_id": KEEP,
    "player_slot": KEEP,
    "radiant_win": KEEP,
    "hero_id": KEEP,
    "start_time": KEEP,
    "duration": KEEP,
    "game_mode": KEEP,
    "lobby_type": KEEP,
    "version": KEEP,
}

PLAYER_SHAPE: Final[Shape] = {
    "account_id": KEEP,
    "player_slot": KEEP,
    "hero_id": KEEP,
    "win": KEEP,
    "kills": KEEP,
    "deaths": KEEP,
    "assists": KEEP,
    "gold_per_min": KEEP,
    "xp_per_min": KEEP,
    "hero_damage": KEEP,
    "last_hits": KEEP,
    "lh_t": KEEP,
    "obs_placed": KEEP,
    "duration": KEEP,
}

MATCH_SHAPE: Final[Shape] = {
    "match_id": KEEP,
    "start_time": KEEP,
    "duration": KEEP,
    "radiant_win": KEEP,
    "game_mode": KEEP,
    "lobby_type": KEEP,
    "version": KEEP,
    "od_data": {"has_parsed": KEEP},
    "players": PLAYER_SHAPE,
}

PARSE_REQUEST_SHAPE: Final[Shape] = {"job": {"jobId": KEEP}}

FIXTURE_SHAPES: Final[Mapping[str, Shape]] = {
    "match_list.json": MATCH_LIST_SHAPE,
    "match_unparsed.json": MATCH_SHAPE,
    "parse_request.json": PARSE_REQUEST_SHAPE,
    "match_parsed.json": MATCH_SHAPE,
}
"""Every fixture file, in the order the IDs in them are first faked."""

FAKE_ACCOUNT_IDS: Final = range(1001, 1100)
FAKE_MATCH_IDS: Final = range(5001, 5100)
OWNER_FAKE_ACCOUNT_ID: Final = FAKE_ACCOUNT_IDS[0]
"""The recorded account's fake ID: tests find "my player" by it."""

MATCH_LIST_LIMIT: Final = 20
PARSE_POLL_SECONDS: Final = 30
PARSE_MAX_POLLS: Final = 30
USER_AGENT: Final = "guildhall-fixture-recorder/0.1"


class ShapeError(ValueError):
    """A response does not fit what the recorder expects.

    Messages name fields, never values, which may be personal.
    """


class RecordingError(RuntimeError):
    """The account's matches cannot be recorded right now."""


def trim(value: Any, shape: Shape) -> Any:
    """Keep only the fields `shape` names, and never add one.

    None stays None and a list is trimmed item by item. A kept value must be a
    scalar or a list of scalars: a nested object is kept only through its own
    shape, so a new field inside it is dropped too.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [trim(item, shape) for item in value]
    if not isinstance(value, dict):
        raise ShapeError(f"expected an object, got {type(value).__name__}")
    trimmed: dict[str, Any] = {}
    for key, inner in shape.items():
        if key not in value:
            continue
        if inner is KEEP:
            if not _is_plain(value[key]):
                raise ShapeError(f"{key!r} holds an object; give it a shape to keep it")
            trimmed[key] = value[key]
        else:
            trimmed[key] = trim(value[key], inner)
    return trimmed


def _is_plain(value: Any) -> bool:
    if isinstance(value, list):
        return all(_is_scalar(item) for item in value)
    return _is_scalar(value)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


class _FakeIds:
    """Hands out fakes from a range: the same fake for the same real ID."""

    def __init__(self, fakes: range) -> None:
        self._fakes = iter(fakes)
        self._given: dict[int, int] = {}

    def __call__(self, real: Any) -> int | None:
        if real is None:
            return None
        if isinstance(real, bool) or not isinstance(real, int):
            raise ShapeError(f"expected an integer ID, got {type(real).__name__}")
        if real not in self._given:
            fake = next(self._fakes, None)
            if fake is None:
                raise ShapeError("ran out of fake IDs")
            self._given[real] = fake
        return self._given[real]


class Pseudonyms:
    """Replaces every `account_id` and `match_id`, at any depth, with a fake.

    The recorded account always becomes OWNER_FAKE_ACCOUNT_ID. Use one
    instance for all of a recording's files, so they stay consistent.
    """

    def __init__(self, owner_account_id: int) -> None:
        self._fields: dict[str, _FakeIds] = {
            "account_id": _FakeIds(FAKE_ACCOUNT_IDS),
            "match_id": _FakeIds(FAKE_MATCH_IDS),
        }
        self._fields["account_id"](owner_account_id)

    def apply(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._fields[key](item)
                if key in self._fields
                else self.apply(item)
                for key, item in value.items()
            }
        return value


def scrub(responses: Mapping[str, Any], owner_account_id: int) -> dict[str, Any]:
    """Trim and pseudonymise every recorded response, keyed by file name."""
    pseudonyms = Pseudonyms(owner_account_id)
    return {
        name: pseudonyms.apply(trim(responses[name], shape))
        for name, shape in FIXTURE_SHAPES.items()
    }


def record(
    client: httpx.Client,
    account_id: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Fetch the raw responses, keyed by fixture file name.

    Picks the account's most recent unparsed match, records it, asks OpenDota
    to parse it, then polls until the parsed version arrives.
    """
    matches = _get(
        client, f"/players/{account_id}/matches", {"limit": MATCH_LIST_LIMIT}
    )
    if not matches:
        raise RecordingError(
            "no matches are visible for this account; check that Expose Public "
            "Match Data is on in Dota's settings"
        )
    # The list sends `"version": null` for an unparsed match, and the match
    # itself leaves the field out; `.get` treats both alike.
    unparsed_ids = [
        match["match_id"] for match in matches if match.get("version") is None
    ]
    if not unparsed_ids:
        raise RecordingError(
            f"the {len(matches)} most recent matches are all parsed already; "
            "play another match and run again"
        )
    match_id = unparsed_ids[0]
    progress(f"Recording the most recent unparsed match ({len(matches)} in the list)")

    unparsed = _get(client, f"/matches/{match_id}")
    if unparsed.get("version") is not None:
        raise RecordingError("the match was parsed in the meantime; run again")
    parse_request = _request(client, "POST", f"/request/{match_id}")
    progress("Parse requested; polling every 30 s, for up to 15 minutes")

    for poll in range(1, PARSE_MAX_POLLS + 1):
        sleep(PARSE_POLL_SECONDS)
        parsed = _get(client, f"/matches/{match_id}")
        if parsed.get("version") is not None:
            progress(f"Parsed after {poll} polls")
            return {
                "match_list.json": matches,
                "match_unparsed.json": unparsed,
                "parse_request.json": parse_request,
                "match_parsed.json": parsed,
            }
    raise RecordingError(
        "the match was not parsed within 15 minutes (some never are); "
        "try again later or after another match"
    )


def _get(
    client: httpx.Client, path: str, params: Mapping[str, int] | None = None
) -> Any:
    return _request(client, "GET", path, params)


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    params: Mapping[str, int] | None = None,
) -> Any:
    response = client.request(method, path, params=params)
    if response.is_error:
        # The path holds the account or match ID, so only the status is shown.
        raise RecordingError(f"OpenDota answered {method} with {response.status_code}")
    return response.json()


class RecorderSettings(BaseSettings):
    """The account to record, from the environment or .env."""

    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fixture_account_id: int


def write_fixtures(fixtures: Mapping[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in fixtures.items():
        (directory / name).write_text(
            json.dumps(body, indent=2) + "\n", encoding="utf-8"
        )


def main() -> None:
    settings = RecorderSettings()
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(base_url=OPENDOTA_URL, headers=headers, timeout=30) as client:
        responses = record(client, settings.fixture_account_id)
    fixtures = scrub(responses, settings.fixture_account_id)
    write_fixtures(fixtures, FIXTURES_DIR)
    print(f"Wrote {len(fixtures)} fixtures to {FIXTURES_DIR}")
    print("Review the diff before committing: git diff tests/fixtures")


if __name__ == "__main__":
    main()
