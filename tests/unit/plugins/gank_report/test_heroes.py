"""The hero table: names and icons for every hero we might report on."""

import json

import pytest
from record_opendota import FIXTURES_DIR
from update_heroes import hero_table

from guildhall.plugins.gank_report.heroes import ICON_CDN, hero_icon_url, hero_name


def recorded_hero_ids() -> set[int]:
    listed = json.loads((FIXTURES_DIR / "match_list.json").read_bytes())
    ids = {match["hero_id"] for match in listed}
    for name in ("match_parsed.json", "match_unparsed.json"):
        match = json.loads((FIXTURES_DIR / name).read_bytes())
        ids |= {player["hero_id"] for player in match["players"]}
    return ids


@pytest.mark.parametrize("hero_id", sorted(recorded_hero_ids()))
def test_every_recorded_hero_has_a_name_and_an_icon(hero_id: int) -> None:
    assert hero_name(hero_id) != f"Hero {hero_id}"
    icon = hero_icon_url(hero_id)
    assert icon is not None
    assert icon.startswith(ICON_CDN + "/")
    assert icon.endswith(".png")


def test_wraith_kings_icon_keeps_his_old_internal_name() -> None:
    assert hero_name(42) == "Wraith King"
    assert hero_icon_url(42) == (
        ICON_CDN + "/apps/dota2/images/dota_react/heroes/icons/skeleton_king.png"
    )


def test_a_hero_newer_than_the_table_still_renders() -> None:
    assert (hero_name(9999), hero_icon_url(9999)) == ("Hero 9999", None)


def test_the_table_keeps_only_names_and_icon_paths_sorted_by_id() -> None:
    constants = {
        "42": {
            "id": 42,
            "localized_name": "Wraith King",
            "icon": "/heroes/icons/skeleton_king.png?",
            "img": "/heroes/skeleton_king.png?",
            "base_armor": 0,
        },
        "1": {
            "id": 1,
            "localized_name": "Anti-Mage",
            "icon": "/heroes/icons/antimage.png?",
        },
    }
    table = hero_table(constants)
    assert list(table) == ["1", "42"]
    assert table["42"] == {
        "name": "Wraith King",
        "icon": "/heroes/icons/skeleton_king.png",
    }


def test_an_empty_answer_is_not_written() -> None:
    with pytest.raises(ValueError, match="no heroes"):
        hero_table({})
