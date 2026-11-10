"""Reading a Dota account from what someone types into `/link dota`.

People know their account in one of two forms: the Dota account ID (the
number in `opendota.com/players/<id>`, up to 10 digits) or their 17-digit
Steam ID. A Steam ID is the account ID plus a fixed offset, so both lead to
the same account (docs/adr/0067).
"""

from typing import Final

STEAM_ID_OFFSET: Final = 76561197960265728
"""Steam ID = Dota account ID + this, for individual accounts."""
MAX_ACCOUNT_ID: Final = 2**32 - 1
"""Account IDs are 32-bit."""


def dota_account_id(text: str) -> int | None:
    """The Dota account ID in `text`, or None if it holds neither form."""
    text = text.strip()
    # ASCII only: `isdecimal` alone accepts any script's digits (full-width too).
    if not (text.isascii() and text.isdecimal()):
        return None
    number = int(text)
    if STEAM_ID_OFFSET <= number <= STEAM_ID_OFFSET + MAX_ACCOUNT_ID:
        number -= STEAM_ID_OFFSET
    if not 1 <= number <= MAX_ACCOUNT_ID:
        return None
    return number
