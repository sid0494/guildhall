"""Messages as plug-ins describe them, and as Discord receives them."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from guildhall.contract import Embed, EmbedAuthor, EmbedField, Message
from guildhall.platform.discord import discord_message


def test_a_message_needs_content_or_an_embed() -> None:
    with pytest.raises(ValidationError, match="needs content or an embed"):
        Message()
    assert Message(embeds=(Embed(title="x"),)).content == ""


@pytest.mark.parametrize(
    "build",
    [
        lambda: Message(content="x" * 2001),
        lambda: Embed(colour=0x1000000),
        lambda: Embed(fields=tuple(EmbedField(name="n", value="v") for _ in range(26))),
        lambda: Message(embeds=tuple(Embed(title="t") for _ in range(11))),
        lambda: EmbedField(name="", value="v"),
    ],
    ids=["long-content", "colour", "too-many-fields", "too-many-embeds", "empty-name"],
)
def test_discords_limits_are_checked_where_messages_are_built(build: object) -> None:
    with pytest.raises(ValidationError):
        build()  # type: ignore[operator]


def test_text_notifies_nobody() -> None:
    assert discord_message(Message(content="hi @everyone")) == {
        "content": "hi @everyone",
        "allowed_mentions": {"parse": [], "users": []},
    }


def test_an_embed_becomes_discords_shape_and_notifies_only_listed_users() -> None:
    played = datetime(2026, 11, 10, 18, 30, tzinfo=UTC)
    message = Message(
        content="<@1234>",
        mentions=("1234",),
        embeds=(
            Embed(
                title="Wraith King · Loss",
                colour=0xE74C3C,
                fields=(EmbedField(name="GPM", value="499 ▲", inline=True),),
                timestamp=played,
            ),
        ),
    )
    assert discord_message(message) == {
        "content": "<@1234>",
        "allowed_mentions": {"parse": [], "users": ["1234"]},
        "embeds": [
            {
                "title": "Wraith King · Loss",
                "color": 0xE74C3C,
                "fields": [{"name": "GPM", "value": "499 ▲", "inline": True}],
                "timestamp": "2026-11-10T18:30:00+00:00",
            }
        ],
    }


def test_an_embeds_author_line_carries_its_icon() -> None:
    icon = "https://cdn.example/icons/x.png"
    message = Message(
        embeds=(Embed(author=EmbedAuthor(name="Wraith King", icon_url=icon)),)
    )
    [embed] = discord_message(message)["embeds"]
    assert embed == {"author": {"name": "Wraith King", "icon_url": icon}}


def test_an_author_icon_must_be_https() -> None:
    with pytest.raises(ValidationError):
        EmbedAuthor(name="x", icon_url="http://insecure.example/x.png")
