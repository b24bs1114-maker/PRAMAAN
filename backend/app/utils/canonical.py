"""Canonical JSON serialisation.

The audit chain hashes structured payloads, so serialisation must be
byte-identical on every machine and every Python build: sorted keys, no
insignificant whitespace, UTF-8, and a deterministic fallback for non-JSON
types. Changing this function invalidates existing chains.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def canonical_json(payload: Any) -> str:
    """Return the canonical JSON text for ``payload``."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
    )


def canonical_bytes(payload: Any) -> bytes:
    return canonical_json(payload).encode("utf-8")
