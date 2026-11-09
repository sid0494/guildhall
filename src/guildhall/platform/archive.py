"""The platform's side of the raw archive (docs/adr/0031, docs/adr/0032).

The runner uploads a job's declared archives before it commits; `Context.archive`
reads them back. Every object lives at `raw/<plug-in>/<kind>/<id>.json` in one
bucket, and the platform adds the `raw/<plug-in>/` prefix itself, so a plug-in
can only reach its own objects.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from guildhall.contract import SourcePlugin
from guildhall.platform.aws import AwsSettings, open_s3
from guildhall.platform.checks import ContractViolationError, check_archive_key

if TYPE_CHECKING:  # type stubs are a dev dependency, absent from the image
    from types_aiobotocore_s3 import S3Client


class ArchiveSettings(AwsSettings):
    archive_bucket: str = "guildhall-raw"


class ArchiveError(Exception):
    """The archive could not be reached or refused a request; worth retrying."""


def archive_key(plugin: str, kind: str, id: str) -> str:
    """Where an archived response lives in the bucket."""
    return f"raw/{plugin}/{kind}/{id}.json"


class ArchiveStore(Protocol):
    """Whole objects by key; `S3ArchiveStore` provides it."""

    async def put(self, key: str, body: bytes) -> None: ...
    async def get(self, key: str) -> bytes | None: ...


def _describe(error: BotoCoreError | ClientError) -> str:
    """The error code or class, never the request (which carries signatures)."""
    if isinstance(error, ClientError):
        return str(error.response.get("Error", {}).get("Code", "ClientError"))
    return type(error).__name__


class S3ArchiveStore:
    """`ArchiveStore` in one S3 bucket, which must already exist."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, key: str, body: bytes) -> None:
        """Store `body` at `key`, replacing any earlier object there."""
        try:
            await self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as error:
            raise ArchiveError(f"upload failed: {_describe(error)}") from error

    async def get(self, key: str) -> bytes | None:
        """The object at `key`, or None if there is none."""
        try:
            response = await self._client.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                body: bytes = await stream.read()
        except self._client.exceptions.NoSuchKey:
            return None
        except (BotoCoreError, ClientError) as error:
            raise ArchiveError(f"download failed: {_describe(error)}") from error
        return body


@asynccontextmanager
async def open_archive(settings: ArchiveSettings) -> AsyncIterator[S3ArchiveStore]:
    """The archive in the configured bucket; fails at once if it is unreachable."""
    async with open_s3(settings) as client:
        try:
            await client.head_bucket(Bucket=settings.archive_bucket)
        except (BotoCoreError, ClientError) as error:
            raise ArchiveError(
                f"bucket {settings.archive_bucket!r} is unreachable: {_describe(error)}"
            ) from error
        yield S3ArchiveStore(client, settings.archive_bucket)


class PluginArchive:
    """`Context.archive` for one plug-in: reads of its own archived responses."""

    def __init__(self, store: ArchiveStore, plugin: SourcePlugin) -> None:
        self._store = store
        self._plugin = plugin

    async def get(self, kind: str, id: str) -> bytes | None:
        problems = check_archive_key(kind, id)
        if problems:
            raise ContractViolationError("archive read " + "; ".join(problems))
        return await self._store.get(archive_key(self._plugin.name, kind, id))
