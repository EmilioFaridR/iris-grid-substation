import json

import pytest
import requests
import responses

from app.scrapers.overpass.client import (
    OverpassClient,
    OverpassQueryError,
    OverpassUnavailableError,
    parse_retry_after,
    parse_status_wait,
)
from tests.conftest import make_test_settings

URL = "https://overpass-api.de/api/interpreter"
STATUS_URL = "https://overpass-api.de/api/status"
OK_BODY = json.dumps({"osm3s": {"timestamp_osm_base": "2026-10-27T18:36:06Z"},
                      "elements": [{"type": "node", "id": 1}]})


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


def make_client(**overrides):
    params = dict(max_retries=3, backoff_base_s=10, backoff_max_s=300, min_request_interval_s=0,
                  check_status_endpoint=False)
    params.update(overrides)
    fake = FakeTime()
    client = OverpassClient(make_test_settings(**params), sleep=fake.sleep, clock=fake.clock)
    return client, fake


@responses.activate
def test_success_first_try():
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client()
    resp = client.query("[out:json];node(1);out;")
    assert resp.attempts == 1 and resp.payload["elements"][0]["id"] == 1
    assert fake.sleeps == []
    assert "iris-grid-substation" in responses.calls[0].request.headers["User-Agent"]


@responses.activate
def test_429_honours_retry_after_then_succeeds():
    responses.add(responses.POST, URL, status=429, headers={"Retry-After": "7"})
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client()
    resp = client.query("q")
    assert resp.attempts == 2
    assert fake.sleeps == [7.0]
    assert client.stats.retries == 1 and client.stats.failures_by_reason == {"http_429": 1}


@responses.activate
def test_504_uses_exponential_backoff():
    # The real Dortmund run hit exactly this: HTTP 504, retry after 10 s, success.
    for _ in range(3):
        responses.add(responses.POST, URL, status=504)
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client()
    resp = client.query("q")
    assert resp.attempts == 4
    assert fake.sleeps == [10, 20, 40]


@responses.activate
def test_backoff_is_capped():
    for _ in range(3):
        responses.add(responses.POST, URL, status=503)
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client(backoff_base_s=100, backoff_max_s=150)
    client.query("q")
    assert fake.sleeps == [100, 150, 150]


@responses.activate
def test_gives_up_after_max_retries():
    for _ in range(4):
        responses.add(responses.POST, URL, status=504)
    client, _fake = make_client(max_retries=3)
    with pytest.raises(OverpassUnavailableError):
        client.query("q")
    assert client.stats.requests == 4


@responses.activate
def test_runtime_error_inside_http_200_is_retried_not_accepted():
    # Overpass answers 200 with *partial* data + a remark when a query times out.
    partial = json.dumps({"elements": [{"type": "node", "id": 1}],
                          "remark": "runtime error: Query timed out in \"query\" at line 3 after 181 seconds."})
    responses.add(responses.POST, URL, body=partial, status=200)
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, _fake = make_client()
    resp = client.query("q")
    assert resp.attempts == 2
    assert client.stats.failures_by_reason == {"runtime_error_in_response": 1}


@responses.activate
def test_invalid_json_is_retried():
    responses.add(responses.POST, URL, body="<html>busy</html>", status=200)
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, _ = make_client()
    assert client.query("q").attempts == 2


@responses.activate
def test_bad_query_is_not_retried():
    responses.add(responses.POST, URL, body="Error: line 1: parse error", status=400)
    client, fake = make_client()
    with pytest.raises(OverpassQueryError):
        client.query("not overpass ql")
    assert client.stats.requests == 1 and fake.sleeps == []


@responses.activate
def test_connection_error_is_retried():
    responses.add(responses.POST, URL, body=requests.ConnectionError("reset"))
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, _ = make_client()
    assert client.query("q").attempts == 2


@responses.activate
def test_min_interval_between_requests():
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client(min_request_interval_s=5)
    client.query("q1")
    client.query("q2")
    assert fake.sleeps == [5]


@responses.activate
def test_waits_for_free_slot_from_status_endpoint():
    responses.add(responses.GET, STATUS_URL, status=200, body=(
        "Connected as: 1\nRate limit: 2\n"
        "Slot available after: 2026-10-27T18:50:12Z, in 13 seconds.\n"
        "Slot available after: 2026-10-27T18:50:40Z, in 41 seconds.\n"))
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client(check_status_endpoint=True)
    client.query("q")
    assert fake.sleeps == [13.0]


@responses.activate
def test_status_endpoint_failure_does_not_block():
    responses.add(responses.GET, STATUS_URL, body=requests.ConnectionError("down"))
    responses.add(responses.POST, URL, body=OK_BODY, status=200)
    client, fake = make_client(check_status_endpoint=True)
    assert client.query("q").attempts == 1


def test_parse_status_wait():
    assert parse_status_wait("Rate limit: 2\n2 slots available now.\n") == 0
    assert parse_status_wait("Slot available after: X, in 5 seconds.\nSlot ... in 3 seconds.") == 3
    assert parse_status_wait("Slot available after: X, in -2 seconds.") == 0
    assert parse_status_wait("garbage") == 0


def test_parse_retry_after():
    from datetime import datetime, timezone

    assert parse_retry_after("12") == 12
    now = datetime(2026, 10, 27, 18, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after("Tue, 27 Oct 2026 18:00:30 GMT", now) == 30
    assert parse_retry_after("nonsense") is None and parse_retry_after(None) is None
