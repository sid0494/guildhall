"""Create the queues and the archive bucket for local development and CI.

Run after `docker compose up`: `uv run python -m guildhall.platform.dev_aws`.
Safe to run repeatedly. On AWS, Terraform creates them with the same settings
instead, so the worker needs no permission to create anything (docs/adr/0032).
"""

import asyncio
import json

from guildhall.platform.archive import ArchiveSettings
from guildhall.platform.aws import open_s3, open_sqs
from guildhall.platform.queue import QueueSettings


async def ensure_queues(settings: QueueSettings) -> tuple[str, str]:
    """Create or update both queues; returns (queue URL, dead-letter URL)."""
    async with open_sqs(settings) as client:
        dead_letter = await client.create_queue(
            QueueName=settings.dead_letter_queue_name
        )
        attributes = await client.get_queue_attributes(
            QueueUrl=dead_letter["QueueUrl"], AttributeNames=["QueueArn"]
        )
        redrive_policy = json.dumps(
            {
                "deadLetterTargetArn": attributes["Attributes"]["QueueArn"],
                "maxReceiveCount": str(settings.max_receive_count),
            }
        )
        queue = await client.create_queue(QueueName=settings.jobs_queue_name)
        await client.set_queue_attributes(
            QueueUrl=queue["QueueUrl"],
            Attributes={
                "RedrivePolicy": redrive_policy,
                "VisibilityTimeout": str(settings.visibility_timeout_seconds),
            },
        )
        return queue["QueueUrl"], dead_letter["QueueUrl"]


async def ensure_bucket(settings: ArchiveSettings) -> str:
    """Create the archive bucket unless it exists; returns its name."""
    async with open_s3(settings) as client:
        try:
            if settings.aws_region == "us-east-1":
                # The original region takes no location, and re-creating a
                # bucket you own there succeeds.
                await client.create_bucket(Bucket=settings.archive_bucket)
            else:
                await client.create_bucket(
                    Bucket=settings.archive_bucket,
                    CreateBucketConfiguration={
                        "LocationConstraint": settings.aws_region  # type: ignore[typeddict-item]
                    },
                )
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
    return settings.archive_bucket


async def ensure_all() -> tuple[str, str, str]:
    queue_url, dead_letter_url = await ensure_queues(QueueSettings())
    bucket = await ensure_bucket(ArchiveSettings())
    return queue_url, dead_letter_url, bucket


def main() -> None:
    queue_url, dead_letter_url, bucket = asyncio.run(ensure_all())
    print(f"job queue:         {queue_url}")
    print(f"dead-letter queue: {dead_letter_url}")
    print(f"archive bucket:    {bucket}")


if __name__ == "__main__":
    main()
