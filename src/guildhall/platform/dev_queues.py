"""Create the job queue and dead-letter queue for local development and CI.

Run after `docker compose up`: `uv run python -m guildhall.platform.dev_queues`.
Safe to run repeatedly. On AWS, Terraform creates the queues with the same
settings instead.
"""

import asyncio
import json

from guildhall.platform.queue import QueueSettings, open_sqs


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


def main() -> None:
    queue_url, dead_letter_url = asyncio.run(ensure_queues(QueueSettings()))
    print(f"job queue:         {queue_url}")
    print(f"dead-letter queue: {dead_letter_url}")


if __name__ == "__main__":
    main()
