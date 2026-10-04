from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from loamhand import (
    MAX_BATCH_READINGS,
    USER_AGENT,
    ChannelDeclaration,
    LoamhandApiError,
    LoamhandAuthError,
    LoamhandClient,
    LoamhandConflictError,
    LoamhandConnectionError,
    LoamhandDeviceRetiredError,
    LoamhandInvalidCodeError,
    LoamhandPlanError,
    LoamhandRateLimitError,
    LoamhandServerError,
    LoamhandUnknownDeviceError,
    LoamhandValidationError,
    Reading,
    __version__,
)

BASE = "https://api.test"
T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def mock() -> Any:
    with aioresponses() as m:
        yield m


def client(session: aiohttp.ClientSession, token: str | None = "lhd_tok") -> LoamhandClient:
    return LoamhandClient(session, base_url=BASE, token=token)


def sent(mock: aioresponses, method: str, url: str, index: int = 0) -> Any:
    calls = mock.requests[(method, __import__("yarl").URL(url))]
    return calls[index]


def test_user_agent_matches_version() -> None:
    assert f"loamhand-py/{__version__}" == USER_AGENT


async def test_claim_sends_code_and_keeps_token(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(
        f"{BASE}/api/v1/ingest/claim",
        status=201,
        payload={"token": "lhd_abc", "connection_id": "c-1", "garden_id": "g-1"},
    )
    c = client(session, token=None)
    got = await c.claim("LOAM-7K2X", name="Home Assistant")
    assert (got.token, got.connection_id, got.garden_id) == ("lhd_abc", "c-1", "g-1")
    assert c.token == "lhd_abc"
    call = sent(mock, "POST", f"{BASE}/api/v1/ingest/claim")
    assert call.kwargs["json"] == {"code": "LOAM-7K2X", "name": "Home Assistant"}
    assert "Authorization" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["User-Agent"] == USER_AGENT


async def test_claim_without_name_omits_it(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(
        f"{BASE}/api/v1/ingest/claim",
        status=201,
        payload={"token": "t", "connection_id": "c", "garden_id": "g"},
    )
    await client(session, token=None).claim("X")
    assert sent(mock, "POST", f"{BASE}/api/v1/ingest/claim").kwargs["json"] == {"code": "X"}


async def test_claim_bad_code(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(
        f"{BASE}/api/v1/ingest/claim",
        status=400,
        payload={"error": "that code isn't valid, or it has expired", "code": "invalid_code"},
    )
    with pytest.raises(LoamhandInvalidCodeError) as err:
        await client(session, token=None).claim("nope")
    assert err.value.status == 400
    assert "expired" in err.value.message


async def test_declare_device_payload_and_response(
    session: aiohttp.ClientSession, mock: Any
) -> None:
    mock.put(
        f"{BASE}/api/v1/ingest/devices/ha-dev-1",
        status=201,
        payload={
            "device_id": "d-1",
            "external_id": "ha-dev-1",
            "support_level": "home_assistant",
            "created": True,
            "changed": True,
            "channels": [
                {
                    "id": "ch-1",
                    "key": "sensor.moist",
                    "metric": "soil_moisture",
                    "unit": "percent",
                    "unmapped": False,
                    "ignored": False,
                },
                {"id": "ch-2", "key": "sensor.x", "metric": None, "unit": None, "unmapped": True},
            ],
        },
    )
    got = await client(session).declare_device(
        "ha-dev-1",
        "Mi Flora",
        "HHCCJCY01",
        "Xiaomi",
        [
            ChannelDeclaration(
                "sensor.moist", name="Moisture", ha_device_class="moisture", unit="%"
            ),
            ChannelDeclaration("sensor.t", metric="soil_temperature", unit="°C", relative=False),
            ChannelDeclaration("sensor.s", ha_device_class="temperature", unit="°C", soil=True),
        ],
    )
    assert got.created and got.changed and got.support_level == "home_assistant"
    assert got.channels[0].metric == "soil_moisture"
    assert got.channels[1].unmapped
    call = sent(mock, "PUT", f"{BASE}/api/v1/ingest/devices/ha-dev-1")
    assert call.kwargs["headers"]["Authorization"] == "Bearer lhd_tok"
    assert call.kwargs["json"] == {
        "name": "Mi Flora",
        "model": "HHCCJCY01",
        "manufacturer": "Xiaomi",
        "channels": [
            {"key": "sensor.moist", "name": "Moisture", "ha_device_class": "moisture", "unit": "%"},
            {
                "key": "sensor.t",
                "name": "sensor.t",
                "metric": "soil_temperature",
                "unit": "°C",
                "relative": False,
            },
            {
                "key": "sensor.s",
                "name": "sensor.s",
                "ha_device_class": "temperature",
                "unit": "°C",
                "soil": True,
            },
        ],
    }


async def test_declare_device_escapes_the_id(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.put(
        f"{BASE}/api/v1/ingest/devices/a%2Fb%20c",
        payload={
            "device_id": "d",
            "external_id": "a/b c",
            "support_level": "tested",
            "created": False,
            "changed": False,
            "channels": [],
        },
    )
    got = await client(session).declare_device("a/b c", "n", "m", "x", [])
    assert not got.created


async def test_post_readings_payload(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(
        f"{BASE}/api/v1/ingest/readings",
        payload={
            "accepted": 1,
            "duplicates": 0,
            "dropped": 0,
            "unmapped": 1,
            "unmapped_channels": ["sensor.x"],
            "anomalies": 0,
        },
    )
    tz = timezone(timedelta(hours=-5))
    got = await client(session).post_readings(
        "dev",
        [
            Reading("sensor.a", 31.5, "%", T0),
            Reading("sensor.b", 20, "°C", datetime(2026, 10, 4, 7, 0, 30, tzinfo=tz)),
        ],
    )
    assert got.accepted == 1 and got.unmapped == 1 and got.unmapped_channels == ("sensor.x",)
    body = sent(mock, "POST", f"{BASE}/api/v1/ingest/readings").kwargs["json"]
    assert body == {
        "schema_version": "1",
        "device": "dev",
        "readings": [
            {"channel": "sensor.a", "value": 31.5, "unit": "%", "at": "2026-10-04T12:00:00Z"},
            {"channel": "sensor.b", "value": 20.0, "unit": "°C", "at": "2026-10-04T12:00:30Z"},
        ],
    }


async def test_post_readings_empty_sends_nothing(session: aiohttp.ClientSession, mock: Any) -> None:
    got = await client(session).post_readings("dev", [])
    assert got.accepted == 0
    assert not mock.requests


def test_naive_timestamp_and_nonfinite_values_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Reading("c", 1.0, "%", datetime(2026, 10, 4, 12, 0)).to_payload()
    with pytest.raises(ValueError, match="finite"):
        Reading("c", float("nan"), "%", T0).to_payload()
    with pytest.raises(ValueError, match="finite"):
        Reading("c", float("inf"), "%", T0).to_payload()


async def test_post_readings_splits_to_server_maximum(
    session: aiohttp.ClientSession, mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    n = MAX_BATCH_READINGS * 2 + 5
    readings = [Reading("c", float(i), "%", T0 - timedelta(minutes=i)) for i in range(n)]
    url = f"{BASE}/api/v1/ingest/readings"
    mock.post(url, payload={"accepted": MAX_BATCH_READINGS})
    # The second batch meets the per-device interval once, then goes through.
    mock.post(url, status=429, headers={"Retry-After": "42"}, payload={"code": "rate_limited"})
    mock.post(url, payload={"accepted": MAX_BATCH_READINGS})
    mock.post(url, payload={"accepted": 5})
    got = await client(session).post_readings("dev", readings)
    assert got.accepted == MAX_BATCH_READINGS * 2 + 5
    calls = mock.requests[("POST", __import__("yarl").URL(url))]
    sizes = [len(c.kwargs["json"]["readings"]) for c in calls]
    assert sizes == [MAX_BATCH_READINGS, MAX_BATCH_READINGS, MAX_BATCH_READINGS, 5]
    assert waits == [42.0]


async def test_first_batch_rate_limit_is_raised(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(
        f"{BASE}/api/v1/ingest/readings",
        status=429,
        headers={"Retry-After": "17"},
        payload={"error": "Too many requests.", "code": "rate_limited"},
    )
    with pytest.raises(LoamhandRateLimitError) as err:
        await client(session).post_readings("dev", [Reading("c", 1.0, "%", T0)])
    assert err.value.retry_after == 17.0
    assert err.value.status == 429


@pytest.mark.parametrize(
    ("status", "payload", "headers", "exc", "code"),
    [
        (
            401,
            {"error": "unauthorized", "code": "unauthorized"},
            None,
            LoamhandAuthError,
            "unauthorized",
        ),
        (
            409,
            {
                "error": "Recording readings needs the Sensors add-on.",
                "code": "plan_limit",
                "details": {"kind": "entitlement", "feature": "sensors"},
            },
            None,
            LoamhandPlanError,
            "plan_limit",
        ),
        (
            409,
            {"error": "retired", "code": "device_retired"},
            None,
            LoamhandDeviceRetiredError,
            "device_retired",
        ),
        (409, {"error": "again", "code": "try_again"}, None, LoamhandConflictError, "try_again"),
        (
            404,
            {"error": "unknown", "code": "unknown_device"},
            None,
            LoamhandUnknownDeviceError,
            "unknown_device",
        ),
        (404, {"error": "not found", "code": "not_found"}, None, LoamhandApiError, "not_found"),
        (
            422,
            {"error": "readings[0] (c): unit is required", "code": "unit_required"},
            None,
            LoamhandValidationError,
            "unit_required",
        ),
        (
            400,
            {"error": "invalid JSON", "code": "invalid_request"},
            None,
            LoamhandValidationError,
            "invalid_request",
        ),
        (
            429,
            {"error": "slow down", "code": "rate_limited"},
            {"Retry-After": "3"},
            LoamhandRateLimitError,
            "rate_limited",
        ),
        (
            500,
            {"error": "internal error", "code": "server_error"},
            None,
            LoamhandServerError,
            "server_error",
        ),
    ],
)
async def test_error_mapping(
    session: aiohttp.ClientSession,
    mock: Any,
    status: int,
    payload: dict[str, Any],
    headers: dict[str, str] | None,
    exc: type[LoamhandApiError],
    code: str,
) -> None:
    mock.post(f"{BASE}/api/v1/ingest/readings", status=status, payload=payload, headers=headers)
    with pytest.raises(exc) as err:
        await client(session).post_readings("dev", [Reading("c", 1.0, "%", T0)])
    assert type(err.value) is exc
    assert err.value.status == status
    assert err.value.code == code
    assert err.value.message == payload["error"]
    assert err.value.details == payload.get("details")


async def test_plan_error_carries_details(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(
        f"{BASE}/api/v1/ingest/readings",
        status=409,
        payload={"error": "x", "code": "plan_limit", "details": {"feature": "sensors"}},
    )
    with pytest.raises(LoamhandPlanError) as err:
        await client(session).post_readings("dev", [Reading("c", 1.0, "%", T0)])
    assert err.value.details == {"feature": "sensors"}


async def test_non_json_error_body(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(f"{BASE}/api/v1/ingest/readings", status=502, body="<html>bad gateway</html>")
    with pytest.raises(LoamhandServerError) as err:
        await client(session).post_readings("dev", [Reading("c", 1.0, "%", T0)])
    assert err.value.code is None


async def test_unreadable_success_body(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(f"{BASE}/api/v1/ingest/readings", status=200, body="nope")
    with pytest.raises(LoamhandServerError):
        await client(session).post_readings("dev", [Reading("c", 1.0, "%", T0)])


async def test_network_failure(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.post(f"{BASE}/api/v1/ingest/readings", exception=aiohttp.ClientConnectionError("down"))
    with pytest.raises(LoamhandConnectionError):
        await client(session).post_readings("dev", [Reading("c", 1.0, "%", T0)])


async def test_timeout(session: aiohttp.ClientSession, mock: Any) -> None:
    mock.put(f"{BASE}/api/v1/ingest/devices/d", exception=TimeoutError())
    with pytest.raises(LoamhandConnectionError):
        await client(session).declare_device("d", "n", "m", "x", [])


async def test_no_token_is_an_auth_error(session: aiohttp.ClientSession, mock: Any) -> None:
    with pytest.raises(LoamhandAuthError):
        await client(session, token=None).post_readings("dev", [Reading("c", 1.0, "%", T0)])
    assert not mock.requests


async def test_base_url_trailing_slash_and_default(session: aiohttp.ClientSession) -> None:
    assert LoamhandClient(session, base_url="https://x.test/").base_url == "https://x.test"
    assert LoamhandClient(session).base_url == "https://api.loamhand.com"


def test_payload_is_json_serialisable() -> None:
    json.dumps(Reading("c", 1.5, "%", T0).to_payload())
