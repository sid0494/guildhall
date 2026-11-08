"""Connection settings and engine for the platform's Postgres database.

Database settings are separate from the application settings so tools such as
migrations need only database variables, not the plug-in configuration.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Deterministic constraint names, so migrations can refer to them by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_POSTGRES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = 5432
    user: str = "guildhall"
    db: str = "guildhall"
    password: SecretStr

    def url(self) -> URL:
        """The SQLAlchemy URL, using the psycopg 3 driver for sync and async."""
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.db,
        )


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """An async engine with a connection pool; dispose of it on shutdown."""
    return create_async_engine(settings.url(), pool_pre_ping=True)
