"""The HTTP response plug-ins receive from `Context.http`."""

from guildhall.contract import HttpResponse


def test_json_parses_the_body() -> None:
    response = HttpResponse(status=200, headers={}, body=b'{"match_id": 42}')
    assert response.json() == {"match_id": 42}


def test_the_body_is_left_out_of_the_repr() -> None:
    # Bodies can be megabytes; logging a response must not print them.
    response = HttpResponse(status=200, headers={}, body=b"x" * 10_000)
    assert "x" * 10 not in repr(response)
