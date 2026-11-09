"""The SQS job queue and its dead-letter queue.

Queues are created outside the worker: by Terraform on AWS and by
`guildhall.platform.dev_queues` locally, so the worker needs no permission to
create or reconfigure them.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiobotocore.session import get_session
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # type stubs are a dev dependency, absent from the image
    from types_aiobotocore_sqs import SQSClient

DEAD_LETTER_REASON = "dead_letter_reason"
"""Message attribute explaining why the platform dead-lettered a job."""


class QueueSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    """Set locally to the moto endpoint; unset on AWS."""
    aws_access_key_id: SecretStr | None = None
    """Set locally (any value works with moto); on AWS the pod's role is used."""
    aws_secret_access_key: SecretStr | None = None
    jobs_queue_name: str = "guildhall-jobs"
    dead_letter_queue_name: str = "guildhall-jobs-dlq"
    max_receive_count: int = 5
    visibility_timeout_seconds: int = 60


@dataclass(frozen=True)
class ReceivedMessage:
    body: str
    receipt_handle: str
    receive_count: int
    """How many times SQS has delivered this message, this delivery included."""


class JobQueue:
    """Send, receive and settle job messages."""

    def __init__(self, client: SQSClient, queue_url: str, dead_letter_url: str) -> None:
        self._client = client
        self._queue_url = queue_url
        self._dead_letter_url = dead_letter_url

    async def send(self, body: str, delay_seconds: int = 0) -> None:
        await self._client.send_message(
            QueueUrl=self._queue_url, MessageBody=body, DelaySeconds=delay_seconds
        )

    async def receive(
        self, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[ReceivedMessage]:
        """Long-poll for up to `max_messages`, waiting up to `wait_seconds`."""
        response = await self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return [
            ReceivedMessage(
                body=message["Body"],
                receipt_handle=message["ReceiptHandle"],
                receive_count=int(message["Attributes"]["ApproximateReceiveCount"]),
            )
            for message in response.get("Messages", [])
        ]

    async def delete(self, message: ReceivedMessage) -> None:
        """Acknowledge a message so it is never delivered again."""
        await self._client.delete_message(
            QueueUrl=self._queue_url, ReceiptHandle=message.receipt_handle
        )

    async def retry_later(self, message: ReceivedMessage, delay_seconds: int) -> None:
        """Leave a message on the queue, invisible for `delay_seconds`."""
        await self._client.change_message_visibility(
            QueueUrl=self._queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=delay_seconds,
        )

    async def dead_letter(self, message: ReceivedMessage, reason: str) -> None:
        """Move a message to the dead-letter queue now, recording why."""
        await self._client.send_message(
            QueueUrl=self._dead_letter_url,
            MessageBody=message.body,
            MessageAttributes={
                DEAD_LETTER_REASON: {"DataType": "String", "StringValue": reason}
            },
        )
        await self.delete(message)


@asynccontextmanager
async def open_sqs(settings: QueueSettings) -> AsyncIterator[SQSClient]:
    """An SQS client for the configured region, endpoint and credentials."""
    key_id, secret = settings.aws_access_key_id, settings.aws_secret_access_key
    async with get_session().create_client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        aws_access_key_id=key_id.get_secret_value() if key_id else None,
        aws_secret_access_key=secret.get_secret_value() if secret else None,
    ) as client:
        yield client


@asynccontextmanager
async def open_job_queue(settings: QueueSettings) -> AsyncIterator[JobQueue]:
    """A JobQueue for the configured queues, which must already exist."""
    async with open_sqs(settings) as client:
        queue_url = await client.get_queue_url(QueueName=settings.jobs_queue_name)
        dead_letter_url = await client.get_queue_url(
            QueueName=settings.dead_letter_queue_name
        )
        yield JobQueue(client, queue_url["QueueUrl"], dead_letter_url["QueueUrl"])
