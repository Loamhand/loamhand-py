"""Dataclasses for what the client sends and receives."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def rfc3339(moment: datetime) -> str:
    """Serialise a timezone-aware datetime as RFC 3339 in UTC (``2026-10-04T12:00:00Z``)."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    text = moment.astimezone(UTC).isoformat(
        timespec="microseconds" if moment.microsecond else "seconds"
    )
    return text.replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Claimed:
    """What redeeming a claim code returns. The token is shown once; keep it."""

    token: str
    connection_id: str
    garden_id: str


@dataclass(frozen=True, slots=True)
class ChannelDeclaration:
    """One channel of a device.

    Say what it measures in one of two ways: ``metric`` (one of Loamhand's metric keys,
    with ``unit``), or a Home Assistant ``ha_device_class`` plus ``unit`` and let the
    server map it. A channel the server cannot map is kept *unmapped* until the gardener
    says what it is. ``soil`` marks a temperature channel as in the ground, which the
    device class alone does not say. ``key`` is yours and must be unique within the device.
    """

    key: str
    name: str = ""
    metric: str | None = None
    unit: str | None = None
    ha_device_class: str | None = None
    soil: bool = False
    relative: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "name": self.name or self.key}
        if self.metric:
            out["metric"] = self.metric
        if self.unit:
            out["unit"] = self.unit
        if self.ha_device_class:
            out["ha_device_class"] = self.ha_device_class
        if self.soil:
            out["soil"] = True
        if self.relative is not None:
            out["relative"] = self.relative
        return out


@dataclass(frozen=True, slots=True)
class DeclaredChannel:
    """How a channel stands after a declaration."""

    id: str
    key: str
    metric: str | None
    unit: str | None
    unmapped: bool
    ignored: bool


@dataclass(frozen=True, slots=True)
class DeclaredDevice:
    """The answer to :meth:`LoamhandClient.declare_device`."""

    device_id: str
    external_id: str
    support_level: str
    created: bool
    changed: bool
    channels: list[DeclaredChannel] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Reading:
    """One value on one channel. ``at`` must be timezone-aware."""

    channel: str
    value: float
    unit: str
    at: datetime

    def to_payload(self) -> dict[str, Any]:
        if isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError(f"reading for {self.channel!r}: value must be a finite number")
        return {
            "channel": self.channel,
            "value": float(self.value),
            "unit": self.unit,
            "at": rfc3339(self.at),
        }


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What a batch of readings was accounted as. For one batch,
    ``accepted + duplicates + dropped + unmapped`` is its size; across a split call the
    counts are summed."""

    accepted: int = 0
    duplicates: int = 0
    dropped: int = 0
    unmapped: int = 0
    unmapped_channels: tuple[str, ...] = ()
    anomalies: int = 0

    def __add__(self, other: IngestResult) -> IngestResult:
        return IngestResult(
            accepted=self.accepted + other.accepted,
            duplicates=self.duplicates + other.duplicates,
            dropped=self.dropped + other.dropped,
            unmapped=self.unmapped + other.unmapped,
            unmapped_channels=tuple(sorted({*self.unmapped_channels, *other.unmapped_channels})),
            anomalies=self.anomalies + other.anomalies,
        )
