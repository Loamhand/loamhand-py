# loamhand

Async Python client for [Loamhand](https://loamhand.com)'s sensor ingest API. It is the
library behind Loamhand's Home Assistant integration, and it is just as usable from a
script, a Raspberry Pi or any other source of garden readings.

```
pip install loamhand
```

Python 3.12 or newer. The only dependency is `aiohttp`.

## Usage

```python
import asyncio
from datetime import UTC, datetime

import aiohttp
from loamhand import ChannelDeclaration, LoamhandClient, Reading


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = LoamhandClient(session)  # https://api.loamhand.com

        # 1. Pair once. The claim code comes from the Sensors page of a garden in
        #    Loamhand and works once, within ten minutes. Keep the token it returns.
        claimed = await client.claim("LOAM-7K2X", name="Greenhouse Pi")

        # 2. Say what the device is and what it measures. Idempotent.
        await client.declare_device(
            external_id="greenhouse-1",
            name="Greenhouse probe",
            model="DIY",
            manufacturer="Me",
            channels=[
                ChannelDeclaration("moisture", metric="soil_moisture", unit="%"),
                ChannelDeclaration("temp", ha_device_class="temperature", unit="°C", soil=True),
            ],
        )

        # 3. Send readings: at most one batch per device per minute.
        result = await client.post_readings(
            "greenhouse-1",
            [
                Reading("moisture", 31.5, "%", datetime.now(UTC)),
                Reading("temp", 18.2, "°C", datetime.now(UTC)),
            ],
        )
        print(result.accepted, "accepted")


asyncio.run(main())
```

The client never creates its own `aiohttp.ClientSession`; you pass one in and close it.
(Home Assistant passes `async_get_clientsession(hass)`.)

## Rules the server keeps

- One accepted batch per device per 60 seconds (`LoamhandRateLimitError`, with
  `retry_after`). Batches over 2000 readings are split for you.
- A reading may be up to 7 days old and up to 5 minutes in the future. Timestamps must be
  timezone-aware; they are sent as RFC 3339 in UTC.
- Every reading carries its unit; the server converts to its own canonical unit.
- A duplicate (channel, time) is ignored, so resending is safe.
- Posting readings needs the Sensors add-on on the account that paired the connection;
  without it the server answers `409 plan_limit` (`LoamhandPlanError`) and nothing
  already recorded is touched.

## Errors

All derive from `LoamhandError`.

| Exception | When |
| --- | --- |
| `LoamhandAuthError` | 401: token invalid or revoked |
| `LoamhandPlanError` | 409 `plan_limit`: the add-on has ended |
| `LoamhandRateLimitError` | 429, with `retry_after` seconds when sent |
| `LoamhandValidationError` | 422: the server's own message says what is wrong |
| `LoamhandInvalidCodeError` | 400 `invalid_code`: claim code wrong, used or expired |
| `LoamhandUnknownDeviceError` | 404 `unknown_device`: declare the device first |
| `LoamhandDeviceRetiredError` | 409 `device_retired` |
| `LoamhandConflictError` | other 409 (for example `try_again`) |
| `LoamhandServerError` | 5xx |
| `LoamhandConnectionError` | network failure or timeout |

## Links

- Loamhand: <https://loamhand.com>
- Help: <https://loamhand.com/help>
- Source and issues: <https://github.com/Loamhand/loamhand-py>

MIT licensed.
