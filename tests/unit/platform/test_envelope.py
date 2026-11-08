"""Unit tests for the job message envelope."""

import pytest
from pydantic import ValidationError

from guildhall.contract import ContractModel, JobSpec
from guildhall.platform.envelope import JobEnvelope


class FetchMatch(ContractModel):
    match_id: int


def test_envelope_from_spec_carries_the_job_and_raw_payload() -> None:
    spec = JobSpec(
        type="fetch_match",
        payload=FetchMatch(match_id=42),
        dedupe_key="example:match:42",
    )
    envelope = JobEnvelope.from_spec("example", spec)
    assert envelope == JobEnvelope(
        plugin="example",
        type="fetch_match",
        dedupe_key="example:match:42",
        attempt=1,
        payload={"match_id": 42},
    )


def test_envelope_survives_a_json_round_trip() -> None:
    envelope = JobEnvelope(
        plugin="example", type="t", dedupe_key="example:1", attempt=3, payload={"a": 1}
    )
    assert JobEnvelope.model_validate_json(envelope.model_dump_json()) == envelope


def test_unknown_envelope_version_is_rejected() -> None:
    raw = '{"v": 2, "plugin": "p", "type": "t", "dedupe_key": "p:1", "payload": {}}'
    with pytest.raises(ValidationError, match="v"):
        JobEnvelope.model_validate_json(raw)


def test_payload_must_be_a_json_object() -> None:
    raw = '{"plugin": "p", "type": "t", "dedupe_key": "p:1", "payload": [1, 2]}'
    with pytest.raises(ValidationError, match="payload"):
        JobEnvelope.model_validate_json(raw)
