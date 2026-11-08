"""The job queue against the SQS emulator."""

import json
from dataclasses import dataclass

from guildhall.platform.dev_queues import ensure_queues
from guildhall.platform.queue import (
    DEAD_LETTER_REASON,
    JobQueue,
    QueueSettings,
    open_sqs,
)


async def test_setup_is_idempotent_and_configures_redrive(
    queue_settings: QueueSettings,
) -> None:
    first = await ensure_queues(queue_settings)
    second = await ensure_queues(queue_settings)
    assert first == second
    async with open_sqs(queue_settings) as client:
        attributes = await client.get_queue_attributes(
            QueueUrl=first[0], AttributeNames=["All"]
        )
    policy = json.loads(attributes["Attributes"]["RedrivePolicy"])
    assert int(policy["maxReceiveCount"]) == queue_settings.max_receive_count
    assert policy["deadLetterTargetArn"].endswith(queue_settings.dead_letter_queue_name)


async def test_sent_message_is_received_once_deleted(job_queue: JobQueue) -> None:
    await job_queue.send("hello")
    (message,) = await job_queue.receive(wait_seconds=1)
    assert (message.body, message.receive_count) == ("hello", 1)
    await job_queue.delete(message)
    assert await job_queue.receive(wait_seconds=0) == []


async def test_redelivery_increments_the_receive_count(job_queue: JobQueue) -> None:
    await job_queue.send("again")
    (first,) = await job_queue.receive(wait_seconds=1)
    await job_queue.retry_later(first, delay_seconds=0)
    (second,) = await job_queue.receive(wait_seconds=1)
    assert second.receive_count == 2


async def test_sqs_dead_letters_after_too_many_receives(
    job_queue: JobQueue, queue_settings: QueueSettings
) -> None:
    await job_queue.send("poison")
    for _ in range(queue_settings.max_receive_count):
        (message,) = await job_queue.receive(wait_seconds=1)
        await job_queue.retry_later(message, delay_seconds=0)
    assert await job_queue.receive(wait_seconds=0) == []
    assert [m.body for m in await dead_letters(queue_settings)] == ["poison"]


async def test_dead_letter_moves_the_message_with_its_reason(
    job_queue: JobQueue, queue_settings: QueueSettings
) -> None:
    await job_queue.send("bad")
    (message,) = await job_queue.receive(wait_seconds=1)
    await job_queue.dead_letter(message, "invalid payload: match_id")
    assert await job_queue.receive(wait_seconds=0) == []
    (dead,) = await dead_letters(queue_settings)
    assert dead.body == "bad"
    assert dead.reason == "invalid payload: match_id"


@dataclass(frozen=True)
class DeadLetter:
    body: str
    reason: str | None


async def dead_letters(settings: QueueSettings) -> list[DeadLetter]:
    async with open_sqs(settings) as client:
        url = await client.get_queue_url(QueueName=settings.dead_letter_queue_name)
        response = await client.receive_message(
            QueueUrl=url["QueueUrl"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
            MessageAttributeNames=["All"],
        )
    dead: list[DeadLetter] = []
    for message in response.get("Messages", []):
        attributes = message.get("MessageAttributes")
        reason = (
            attributes[DEAD_LETTER_REASON].get("StringValue")
            if attributes and DEAD_LETTER_REASON in attributes
            else None
        )
        dead.append(DeadLetter(message["Body"], reason))
    return dead
