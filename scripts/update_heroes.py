"""Refresh Gank Report's hero table from OpenDota's hero constants.

Run by hand when Valve adds or renames a hero (docs/adr/0070):

    uv run python scripts/update_heroes.py
    git diff src/guildhall/plugins/gank_report/heroes.json

It keeps each hero's name and the path of its icon on Valve's CDN; the icons
themselves are never downloaded or committed.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, RootModel

CONSTANTS_URL: Final = "https://api.opendota.com/api/constants/heroes"
TABLE: Final = (
    Path(__file__).resolve().parents[1]
    / "src/guildhall/plugins/gank_report/heroes.json"
)
USER_AGENT: Final = "guildhall-hero-table/0.1"


class HeroConstant(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    localized_name: str
    icon: str


class HeroConstants(RootModel[dict[str, HeroConstant]]):
    pass


def hero_table(constants: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """`id -> {name, icon}`, sorted by ID; the icon path loses its trailing `?`."""
    heroes = HeroConstants.model_validate(constants).root.values()
    if not heroes:
        raise ValueError("OpenDota returned no heroes")
    return {
        str(hero.id): {"name": hero.localized_name, "icon": hero.icon.rstrip("?")}
        for hero in sorted(heroes, key=lambda hero: hero.id)
    }


def main() -> None:
    response = httpx.get(CONSTANTS_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    table = hero_table(response.json())
    TABLE.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"Wrote {len(table)} heroes to {TABLE}")


if __name__ == "__main__":
    main()
