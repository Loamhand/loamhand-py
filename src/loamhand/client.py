"""The async client for Loamhand's device ingest API (``/api/v1/ingest``)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import aiohttp

from ._version import __version__
from .exceptions import (
    LoamhandApiError,
    LoamhandAuthError,
    LoamhandConflictError,
    LoamhandConnectionError,
    LoamhandDeviceRetiredError,
    LoamhandInvalidCodeError,
    LoamhandPlanError,
    LoamhandRateLimitError,
    LoamhandServerError,
    LoamhandUnknownDeviceError,
    LoamhandValidationError,
)
from .models import (
    ChannelDeclaration,
    Claimed,
    DeclaredChannel,
    DeclaredDevice,
    IngestResult,
    Reading,
)

DEFAULT_BASE_URL = "https://api.loamhand.com"
USER_AGENT = f"loamhand-py/{__version__}"

#: The most readings the server takes in one batch (``MaxBatchReadings``).
MAX_BATCH_READINGS = 2000
#: How far back a reading's timestamp may be (the server's backfill window), in days.
MAX_BACKFILL_DAYS = 7
#: The server accepts one batch per device per this many seconds.
BATCH_INTERVAL_SECONDS = 60

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SCHEMA_VERSION = "1"
_CHUNK_RETRIES = 3
_MAX_WAIT_SECONDS = 120.0


class LoamhandClient:
    """Talks to Loamhand as a device (a *connection*) holding an ``lhd_`` token.

    The client never creates its own :class:`aiohttp.ClientSession`; pass one in (Home
    Assistant passes ``async_get_clientsession(hass)``) and close it yourself.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
    ) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self.token = token

    # -- pairing ----------------------------------------------------------------

    async def claim(self, code: str, name: str | None = None) -> Claimed:
        """Redeem a one-time claim code (made in Loamhand's Sensors page) for a token.

        The token is stored on this client, so it can be used straight away. Raises
        :class:`LoamhandInvalidCodeError` for a wrong, used or expired code.
        """
        body: dict[str, Any] = {"code": code}
        if name:
            body["name"] = name
        data = await self._request("POST", "/api/v1/ingest/claim", json_body=body, auth=False)
        claimed = Claimed(
            token=str(data["token"]),
            connection_id=str(data["connection_id"]),
            garden_id=str(data["garden_id"]),
        )
        self.token = claimed.token
        return claimed

    # -- devices ----------------------------------------------------------------

    async def declare_device(
        self,
        external_id: str,
        name: str,
        model: str,
        manufacturer: str,
        channels: Sequence[ChannelDeclaration],
    ) -> DeclaredDevice:
        """Create or refresh a device and its channels. Idempotent.

        ``external_id`` is yours and unique within the connection (at most 200
        characters). Channels a later declaration leaves out are left alone, and a
        channel the gardener has mapped by hand is never overwritten.
        """
        body = {
            "name": name,
            "model": model,
            "manufacturer": manufacturer,
            "channels": [c.to_payload() for c in channels],
        }
        data = await self._request(
            "PUT", f"/api/v1/ingest/devices/{quote(external_id, safe='')}", json_body=body
        )
        return DeclaredDevice(
            device_id=str(data["device_id"]),
            external_id=str(data["external_id"]),
            support_level=str(data["support_level"]),
            created=bool(data["created"]),
            changed=bool(data["changed"]),
            channels=[
                DeclaredChannel(
                    id=str(c["id"]),
                    key=str(c["key"]),
                    metric=c.get("metric"),
                    unit=c.get("unit"),
                    unmapped=bool(c.get("unmapped")),
                    ignored=bool(c.get("ignored")),
                )
                for c in data.get("channels") or []
            ],
        )

    # -- readings ---------------------------------------------------------------

    async def post_readings(self, external_id: str, readings: Sequence[Reading]) -> IngestResult:
        """Send readings for one declared device.

        More than :data:`MAX_BATCH_READINGS` are split into several batches. The server
        takes one batch per device per minute, so if a *later* batch is refused with a
        429 this waits the server's ``Retry-After`` and tries again (three times), and the
        results are summed. A 429 on the first batch is raised for the caller to back
        off, because nothing has been sent yet. Raises on the first failure otherwise.
        """
        if not readings:
            return IngestResult()
        total = IngestResult()
        for start in range(0, len(readings), MAX_BATCH_READINGS):
            chunk = readings[start : start + MAX_BATCH_READINGS]
            body = {
                "schema_version": _SCHEMA_VERSION,
                "device": external_id,
                "readings": [r.to_payload() for r in chunk],
            }
            attempts = 0
            while True:
                try:
                    data = await self._request("POST", "/api/v1/ingest/readings", json_body=body)
                except LoamhandRateLimitError as err:
                    attempts += 1
                    if start == 0 or attempts > _CHUNK_RETRIES:
                        raise
                    wait = err.retry_after or float(BATCH_INTERVAL_SECONDS)
                    await asyncio.sleep(min(wait, _MAX_WAIT_SECONDS))
                    continue
                break
            total += IngestResult(
                accepted=int(data.get("accepted", 0)),
                duplicates=int(data.get("duplicates", 0)),
                dropped=int(data.get("dropped", 0)),
                unmapped=int(data.get("unmapped", 0)),
                unmapped_channels=tuple(data.get("unmapped_channels") or ()),
                anomalies=int(data.get("anomalies", 0)),
            )
        return total

    # -- plumbing ---------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if auth:
            if not self.token:
                raise LoamhandAuthError(
                    "no token: claim a code first", status=401, code="unauthorized"
                )
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with self._session.request(
                method,
                self.base_url + path,
                json=json_body,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                raw = await resp.read()
                status = resp.status
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        except (TimeoutError, aiohttp.ClientError) as err:
            raise LoamhandConnectionError(f"could not reach Loamhand: {err}") from err

        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
        if 200 <= status < 300:
            if not isinstance(payload, dict):
                raise LoamhandServerError(
                    "Loamhand answered with something unreadable", status=status
                )
            return payload
        raise _error_for(status, payload, retry_after)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _error_for(status: int, payload: Any, retry_after: float | None) -> LoamhandApiError:
    body = payload if isinstance(payload, dict) else {}
    message = str(body.get("error") or f"Loamhand answered {status}")
    code = body.get("code")
    code = str(code) if code else None
    details = body.get("details") if isinstance(body.get("details"), dict) else None
    kw: dict[str, Any] = {"status": status, "code": code, "details": details}
    if status == 401:
        return LoamhandAuthError(message, **kw)
    if status == 409:
        if code == "plan_limit":
            return LoamhandPlanError(message, **kw)
        if code == "device_retired":
            return LoamhandDeviceRetiredError(message, **kw)
        return LoamhandConflictError(message, **kw)
    if status == 429:
        return LoamhandRateLimitError(message, retry_after=retry_after, **kw)
    if status == 422:
        return LoamhandValidationError(message, **kw)
    if status == 400:
        if code == "invalid_code":
            return LoamhandInvalidCodeError(message, **kw)
        return LoamhandValidationError(message, **kw)
    if status == 404 and code == "unknown_device":
        return LoamhandUnknownDeviceError(message, **kw)
    if status >= 500:
        return LoamhandServerError(message, **kw)
    return LoamhandApiError(message, **kw)
