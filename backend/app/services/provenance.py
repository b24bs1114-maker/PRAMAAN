"""C2PA / provenance-manifest inspection.

Two layers, in preference order:

1. **The ``c2pa`` library**, when installed. It performs full manifest parsing
   *and* cryptographic validation of the claim signature against its trust list.
   That is the only path that can honestly report a manifest as *verified*.
2. **A local container scan**, always available. It walks the file for the
   standard C2PA embedding containers -- JUMBF boxes in JPEG ``APP11`` segments,
   the PNG ``caBX`` chunk, the BMFF ``uuid``/``C2PA`` box -- and reports whether
   a manifest is *present*. It cannot validate a signature, so a manifest found
   this way is reported as ``PRESENT_UNVERIFIED`` and never as authentic proof.

Interpretation rules enforced in the output:

* **Absence of C2PA is not evidence of manipulation.** Almost no media in
  circulation carries Content Credentials; absence is the overwhelming norm.
* **An unverified manifest is not proof of authenticity.** Manifest bytes can be
  copied from another asset. Without signature validation the claim is only a
  self-assertion by whoever wrote it.
* A manifest that *self-declares* generative AI (``digitalSourceType`` of
  ``trainedAlgorithmicMedia`` and friends) is a real, reportable indicator --
  labelled as self-declared, because that is what it is.
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger("pramaan.provenance")

INSPECTOR = "pramaan-provenance/1.0 (JUMBF/caBX container scan; c2pa lib when present)"

STATUS_OK = "OK"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_ERROR = "ERROR"

# Manifest states, from strongest to weakest evidentiary standing.
STATE_VERIFIED = "PRESENT_VERIFIED"
STATE_INVALID = "PRESENT_INVALID_SIGNATURE"
STATE_UNVERIFIED = "PRESENT_UNVERIFIED"
STATE_ABSENT = "ABSENT"

INTERPRETATION = (
    "Absence of a C2PA manifest is NOT evidence of manipulation -- the "
    "overwhelming majority of media in circulation carries no Content "
    "Credentials at all. Equally, a manifest that has not been cryptographically "
    "validated is NOT proof of authenticity: manifest bytes can be copied from "
    "another asset, so an unvalidated claim is only an assertion by whoever "
    "wrote it."
)

# IPTC digitalSourceType values that declare synthetic or partly synthetic media.
_GENERATIVE_SOURCE_TYPES = (
    "trainedAlgorithmicMedia",
    "compositeWithTrainedAlgorithmicMedia",
    "algorithmicMedia",
    "algorithmicallyEnhanced",
)

# Byte markers for the standard C2PA embedding containers.
_JUMBF_LABEL = b"c2pa"
_PNG_C2PA_CHUNK = b"caBX"
_BMFF_C2PA_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")

_ACTION_RE = re.compile(rb"c2pa\.(created|edited|placed|opened|converted|published)")
_CLAIM_GENERATOR_RE = re.compile(rb'"claim_generator"\s*:\s*"([^"]{1,200})"')
_ALT_GENERATOR_RE = re.compile(rb'"claim_generator_info"\s*:\s*\[\s*\{[^}]{0,400}?'
                               rb'"name"\s*:\s*"([^"]{1,200})"')

# Read only the head and tail of large files: embedded manifests sit in the
# container header (JPEG APP11, PNG chunk before IDAT, BMFF top-level box).
_SCAN_HEAD_BYTES = 4 * 1024 * 1024
_SCAN_TAIL_BYTES = 512 * 1024


def _c2pa_library() -> Any | None:
    """Return the ``c2pa`` module if this deployment has it installed."""
    try:  # pragma: no cover - exercised only where the library is installed
        import c2pa  # type: ignore

        return c2pa
    except Exception:  # noqa: BLE001
        return None


CONTAINER_SCAN_ONLY_DETAIL = (
    "The c2pa library is not installed in this deployment, so a manifest can be "
    "detected but its signature cannot be validated. Manifests found this way "
    "are reported as PRESENT_UNVERIFIED, never as verified."
)


def validator_status() -> dict[str, Any]:
    """What this deployment can actually do with C2PA, for status reporting.

    The container scan is built in and always available, so provenance
    inspection is never simply "offline". What varies is whether a signature can
    be *validated*, which only the c2pa library can do -- so that is what this
    reports, rather than a single ONLINE/OFFLINE label that would hide the
    difference.
    """
    module = _c2pa_library()
    available = module is not None
    return {
        "inspector": INSPECTOR,
        "container_scan_available": True,
        "c2pa_library_available": available,
        "c2pa_library_version": (
            getattr(module, "__version__", "unknown") if available else None
        ),
        "signature_validation_available": available,
        "state": "SIGNATURE_VALIDATION" if available else "CONTAINER_SCAN_ONLY",
        "detail": (
            "The c2pa library is installed, so claim signatures are "
            "cryptographically validated against its trust list."
            if available
            else CONTAINER_SCAN_ONLY_DETAIL
        ),
    }


def _scan_jpeg_app11(data: bytes) -> bool:
    """True when a JPEG APP11 segment carries a JUMBF box labelled ``c2pa``."""
    if not data.startswith(b"\xff\xd8"):
        return False
    offset = 2
    end = len(data)
    while offset + 4 <= end:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if marker == 0xDA:  # start of scan: no more metadata segments
            return False
        (length,) = struct.unpack(">H", data[offset + 2 : offset + 4])
        segment = data[offset + 4 : offset + 2 + length]
        if marker == 0xEB and _JUMBF_LABEL in segment:  # APP11
            return True
        offset += 2 + max(length, 2)
    return False


def _scan_png_chunks(data: bytes) -> bool:
    """True when the PNG carries a ``caBX`` (C2PA) chunk."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    end = len(data)
    while offset + 8 <= end:
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        if chunk_type == _PNG_C2PA_CHUNK:
            return True
        if chunk_type == b"IEND":
            return False
        offset += 12 + length  # length + type + data + crc
    return False


def _scan_generic(data: bytes) -> bool:
    """Container-agnostic fallback: BMFF C2PA uuid box or a JUMBF ``c2pa`` label."""
    if _BMFF_C2PA_UUID in data:
        return True
    # 'jumb' superbox header immediately followed by the c2pa label.
    return b"jumb" in data and (b"c2pa" in data or b"c2ma" in data)


def _read_window(path: Path) -> bytes:
    """Read the head (and tail, for large files) of the file for scanning."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(_SCAN_HEAD_BYTES)
        if size <= _SCAN_HEAD_BYTES:
            return head
        handle.seek(max(size - _SCAN_TAIL_BYTES, _SCAN_HEAD_BYTES))
        return head + handle.read(_SCAN_TAIL_BYTES)


def _declared_details(data: bytes) -> dict[str, Any]:
    """Pull self-declared generator/action strings out of raw manifest bytes.

    Substring extraction, not a JUMBF parse: enough to surface what the manifest
    claims about itself for an examiner, explicitly unverified.
    """
    generator = _CLAIM_GENERATOR_RE.search(data) or _ALT_GENERATOR_RE.search(data)
    actions = sorted({m.group(0).decode("ascii") for m in _ACTION_RE.finditer(data)})
    generative = sorted(
        {
            source
            for source in _GENERATIVE_SOURCE_TYPES
            if source.encode("ascii") in data
        }
    )
    return {
        "claim_generator": (
            generator.group(1).decode("utf-8", "replace") if generator else None
        ),
        "actions": actions,
        "generative_source_types": generative,
        "declares_generative_ai": bool(generative),
        "extraction": "substring scan of manifest bytes (not a validated parse)",
    }


def _library_inspect(module: Any, path: Path) -> dict[str, Any] | None:
    """Try the c2pa library. Returns None if it cannot be used for this file."""
    try:  # pragma: no cover - requires the optional c2pa library
        reader = getattr(module, "Reader", None)
        if reader is None:
            return None
        with reader(str(path)) as handle:  # type: ignore[misc]
            manifest_json = handle.json()
        return {
            "state": STATE_VERIFIED,
            "manifest_present": True,
            "signature_validated": True,
            "manifest": manifest_json,
            "validator": f"c2pa-python {getattr(module, '__version__', 'unknown')}",
        }
    except Exception as exc:  # noqa: BLE001 - absent/invalid manifest or old API
        message = str(exc)
        if "no claim" in message.lower() or "not found" in message.lower():
            return {
                "state": STATE_ABSENT,
                "manifest_present": False,
                "signature_validated": False,
                "validator": f"c2pa-python {getattr(module, '__version__', 'unknown')}",
                "detail": "The c2pa library found no manifest in this file.",
            }
        logger.info("c2pa library inspection failed: %s", exc.__class__.__name__)
        return None


def inspect(path: str | Path, media_type: str = "image") -> dict[str, Any]:
    """Inspect one file for a C2PA manifest. Never raises."""
    file_path = Path(path)
    payload: dict[str, Any] = {
        "inspector": INSPECTOR,
        "interpretation": INTERPRETATION,
        "media_type": media_type,
        "c2pa_library_available": False,
        "signature_validated": False,
        "warnings": [],
    }

    if not file_path.is_file():
        payload.update(
            status=STATUS_ERROR,
            state=STATE_ABSENT,
            manifest_present=False,
            detail="Evidence file is not readable on this host.",
        )
        return payload

    module = _c2pa_library()
    payload["c2pa_library_available"] = module is not None

    if module is not None:
        result = _library_inspect(module, file_path)
        if result is not None:
            payload.update(result)
            payload["status"] = STATUS_OK
            if result.get("manifest_present"):
                try:
                    payload["declared"] = _declared_details(_read_window(file_path))
                except Exception:  # noqa: BLE001
                    pass
            return payload
        payload["warnings"].append(
            "The installed c2pa library could not process this file; fell back to "
            "the local container scan, which cannot validate signatures."
        )

    try:
        data = _read_window(file_path)
    except OSError as exc:
        payload.update(
            status=STATUS_ERROR,
            state=STATE_ABSENT,
            manifest_present=False,
            detail=f"Could not read file for provenance scan ({type(exc).__name__}).",
        )
        return payload

    present = _scan_jpeg_app11(data) or _scan_png_chunks(data) or _scan_generic(data)
    payload["scan"] = {
        "method": "container scan (JPEG APP11/JUMBF, PNG caBX, BMFF C2PA uuid)",
        "bytes_scanned": len(data),
        "signature_validation": "not performed",
    }

    if not present:
        payload.update(
            status=STATUS_OK,
            state=STATE_ABSENT,
            manifest_present=False,
            detail=(
                "No C2PA manifest container was found. This is the normal, "
                "expected condition for almost all media and is NOT an "
                "indicator of manipulation."
            ),
        )
        return payload

    payload.update(
        status=STATUS_OK,
        state=STATE_UNVERIFIED,
        manifest_present=True,
        declared=_declared_details(data),
        detail=(
            "A C2PA manifest container is present but its signature was NOT "
            "validated: cryptographic validation requires the optional 'c2pa' "
            "library, which is not installed in this deployment. Treat the "
            "manifest contents as an unverified self-assertion."
        ),
    )
    return payload
