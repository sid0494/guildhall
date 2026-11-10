"""Hero names and icons, from the table `scripts/update_heroes.py` writes
(docs/adr/0070).

Loaded once, at import, so rendering a post is a lookup and never I/O. A hero
newer than the table still renders, by number and without an icon.
"""

import json
from importlib import resources
from typing import Final

ICON_CDN: Final = "https://cdn.cloudflare.steamstatic.com"
"""Valve's CDN; the table holds each icon's path on it."""

_TABLE: Final[dict[str, dict[str, str]]] = json.loads(
    resources.files(__package__).joinpath("heroes.json").read_text("utf-8")
)


def hero_name(hero_id: int) -> str:
    hero = _TABLE.get(str(hero_id))
    return hero["name"] if hero else f"Hero {hero_id}"


def hero_icon_url(hero_id: int) -> str | None:
    hero = _TABLE.get(str(hero_id))
    return ICON_CDN + hero["icon"] if hero else None
