"""Unit tests for the retry backoff and validation error summaries."""

import pytest
from pydantic import ValidationError

from guildhall.contract import ContractModel
from guildhall.platform.runner import backoff_seconds, describe


@pytest.mark.parametrize(
    ("receive_count", "low", "high"),
    [(1, 10, 20), (2, 20, 40), (3, 40, 80), (4, 80, 160)],
)
def test_backoff_doubles_with_at_least_half_guaranteed(
    receive_count: int, low: int, high: int
) -> None:
    delays = {backoff_seconds(receive_count, base=20, cap=900) for _ in range(200)}
    assert low <= min(delays)
    assert max(delays) <= high


def test_backoff_is_capped() -> None:
    delays = {backoff_seconds(20, base=20, cap=900) for _ in range(200)}
    assert min(delays) >= 450
    assert max(delays) <= 900


def test_four_retries_outlast_a_two_minute_outage() -> None:
    worst_case = sum(
        min(backoff_seconds(n, base=20, cap=900) for _ in range(200))
        for n in (1, 2, 3, 4)
    )
    assert worst_case >= 150


class Payload(ContractModel):
    match_id: int


def test_validation_summary_names_fields_but_not_values() -> None:
    with pytest.raises(ValidationError) as error:
        Payload.model_validate({"match_id": "secret-looking-value"})
    summary = describe(error.value)
    assert summary.startswith("match_id: ")
    assert "secret-looking-value" not in summary
