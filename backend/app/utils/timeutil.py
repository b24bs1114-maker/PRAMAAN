"""UTC time helpers.

Every stored timestamp is UTC. Naive datetimes are used in the database (SQLite
has no timezone type) but they are always UTC, and serialised with an explicit
``Z`` suffix so a consumer can never misread them as local time.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (UTC by convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    """Serialise a stored UTC datetime as an ISO-8601 string with ``Z``."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="seconds") + "Z"


def from_timestamp(seconds: float) -> datetime | None:
    """Convert a POSIX timestamp to a naive UTC datetime, ``None`` if invalid."""
    try:
        return datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string (optionally ``Z``-suffixed) to naive UTC."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed
