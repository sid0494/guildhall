"""The parts of OpenDota's responses that Gank Report reads (docs/adr/0047).

Each model names only the fields the plug-in uses and ignores the rest. They
encode the shapes recorded in tests/fixtures/opendota/: an unparsed match has
no `version` (so parsing is judged by `od_data.has_parsed`), and in an
unparsed match a player who hides their match data has no `account_id`.
"""

from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, RootModel, ValidationError

from guildhall.contract import HttpResponse, PermanentError

OPENDOTA_URL: Final = "https://api.opendota.com/api"


def match_url(match_id: int) -> str:
    return f"{OPENDOTA_URL}/matches/{match_id}"


def parse_request_url(match_id: int) -> str:
    return f"{OPENDOTA_URL}/request/{match_id}"


def player_matches_url(account_id: int) -> str:
    return f"{OPENDOTA_URL}/players/{account_id}/matches"


class OpenDotaModel(BaseModel):
    """Base for response models: immutable, unknown fields ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Player(OpenDotaModel):
    account_id: int | None = None
    player_slot: int
    hero_id: int
    win: bool


class ListedMatch(OpenDotaModel):
    """One entry of a player's match list."""

    match_id: int
    game_mode: int
    lobby_type: int
    duration: int


class MatchList(RootModel[list[ListedMatch]]):
    """A player's match list, newest first."""

    model_config = ConfigDict(frozen=True)


class OdData(OpenDotaModel):
    has_parsed: bool


class Match(OpenDotaModel):
    match_id: int
    start_time: int
    """Unix seconds."""
    duration: int
    """Seconds."""
    game_mode: int
    lobby_type: int
    od_data: OdData
    players: list[Player]

    @property
    def parsed(self) -> bool:
        return self.od_data.has_parsed

    @property
    def played_at(self) -> datetime:
        return datetime.fromtimestamp(self.start_time, UTC)

    def player(self, account_id: int) -> Player | None:
        """The player with this account, if they are in the match and visible."""
        return next((p for p in self.players if p.account_id == account_id), None)


def parse[M: BaseModel](model: type[M], body: bytes) -> M:
    """Validate an OpenDota response body, or fail permanently.

    The same body fails the same way every time, so retrying cannot help. The
    message names fields, never values, which may be personal IDs.
    """
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        # `from None`: the validation error's own text includes the input.
        raise PermanentError(
            f"unexpected OpenDota response: {describe(error)}"
        ) from None


def check_status(response: HttpResponse, what: str) -> None:
    """Fail permanently on a status the plug-in cannot act on.

    The platform has already handled 429 and 5xx (docs/adr/0033, 0035), so
    anything else that is not a success means the request itself is wrong.
    """
    if response.status == 404:
        raise PermanentError(f"{what} not found")
    if not 200 <= response.status < 300:
        raise PermanentError(f"OpenDota answered {response.status} for {what}")


def describe(error: ValidationError) -> str:
    """Field locations and messages only, never the offending input values."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
        for item in error.errors()
    )
