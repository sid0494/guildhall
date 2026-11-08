"""Runtime configuration, read from the environment.

Every variable has the `GUILDHALL_` prefix. Locally, a git-ignored `.env` file
in the working directory supplies them; in the cluster they come from
ConfigMaps and Secrets. Real environment variables take precedence over `.env`.
"""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    plugins: Annotated[tuple[str, ...], NoDecode, Field(min_length=1)]
    """Names of the enabled plug-ins, comma-separated: "gank_report,side_quest"."""

    @field_validator("plugins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(name.strip() for name in value.split(",") if name.strip())
        return value
