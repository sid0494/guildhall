"""The S3 archive against the moto emulator (ADR 0031, 0032)."""

import pytest

from guildhall.platform.archive import (
    ArchiveError,
    ArchiveSettings,
    S3ArchiveStore,
    open_archive,
)
from guildhall.platform.aws import open_s3
from guildhall.platform.dev_aws import ensure_bucket

KEY = "raw/synthetic/work/a.json"


async def test_an_archived_body_reads_back_byte_for_byte(
    archive: S3ArchiveStore,
) -> None:
    # Key order, spacing and number formatting survive: nothing is re-serialised.
    body = b'{"z": 1, "a":  2.50, "name": "\\u00e9"}'
    await archive.put(KEY, body)
    assert await archive.get(KEY) == body


async def test_archiving_again_replaces_the_body(archive: S3ArchiveStore) -> None:
    await archive.put(KEY, b'{"parsed": false}')
    await archive.put(KEY, b'{"parsed": true}')
    assert await archive.get(KEY) == b'{"parsed": true}'


async def test_a_missing_key_reads_as_none(archive: S3ArchiveStore) -> None:
    assert await archive.get("raw/synthetic/work/missing.json") is None


async def test_objects_are_stored_as_json(
    archive: S3ArchiveStore, archive_settings: ArchiveSettings
) -> None:
    await archive.put(KEY, b"{}")
    async with open_s3(archive_settings) as client:
        head = await client.head_object(Bucket=archive_settings.archive_bucket, Key=KEY)
    assert head["ContentType"] == "application/json"


async def test_bucket_setup_is_idempotent(archive_settings: ArchiveSettings) -> None:
    assert await ensure_bucket(archive_settings) == archive_settings.archive_bucket
    assert await ensure_bucket(archive_settings) == archive_settings.archive_bucket


async def test_opening_a_missing_bucket_fails_at_once(
    archive_settings: ArchiveSettings,
) -> None:
    with pytest.raises(ArchiveError, match="is unreachable: 404"):
        async with open_archive(archive_settings):
            pass


async def test_upload_errors_name_the_error_code_only(
    archive_settings: ArchiveSettings, archive: S3ArchiveStore
) -> None:
    async with open_s3(archive_settings) as client:
        gone = S3ArchiveStore(client, archive_settings.archive_bucket + "-gone")
        with pytest.raises(ArchiveError) as raised:
            await gone.put(KEY, b"{}")
    assert str(raised.value) == "upload failed: NoSuchBucket"
