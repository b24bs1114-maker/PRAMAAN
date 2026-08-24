"""Local metadata and provenance-adjacent extraction.

Everything here runs offline against the bytes on disk: EXIF/TIFF tags, image
geometry and encoder details via Pillow, and a minimal ISO-BMFF (MP4/MOV) box
walk for video containers. No external tools, no network.

**Interpretation rule, enforced in the output itself:** absence of metadata is
*not* evidence of manipulation. Messaging platforms, social networks and
screenshot tools strip EXIF as a matter of routine, so a stripped file is the
norm for redistributed media. Every payload therefore carries an explicit
``interpretation`` note, and the metadata fusion signal reports
``INCONCLUSIVE`` (and is excluded from the weighted score) when there is
nothing to read rather than pushing the verdict toward "manipulated".
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

from app.utils.timeutil import from_timestamp, iso, parse_iso

logger = logging.getLogger("pramaan.metadata")

EXTRACTOR = "pramaan-metadata/1.0 (Pillow EXIF/TIFF + native ISO-BMFF walker)"

INTERPRETATION_NOTE = (
    "Absence of metadata is NOT evidence of manipulation. Messaging platforms, "
    "social networks and screenshot tools routinely strip EXIF during "
    "redistribution, so missing metadata is the expected condition for shared "
    "media. Presence of editing software in metadata indicates that a file was "
    "processed by that software, which is not the same as deceptive alteration."
)

# Software strings worth surfacing to an examiner. Matching one is a lead, not a
# finding: exporting, resizing or converting a file also writes these tags.
_EDITOR_HINTS = (
    "photoshop",
    "lightroom",
    "gimp",
    "affinity",
    "paint.net",
    "pixlr",
    "snapseed",
    "picsart",
    "canva",
    "facetune",
    "capture one",
    "luminar",
    "topaz",
    "krita",
    "inkscape",
    "imagemagick",
    "ffmpeg",
)
_GENERATIVE_HINTS = (
    "midjourney",
    "stable diffusion",
    "stablediffusion",
    "dall-e",
    "dalle",
    "firefly",
    "openai",
    "leonardo.ai",
    "flux",
    "comfyui",
    "automatic1111",
    "invokeai",
    "gemini",
    "imagen",
)

# Standard JPEG luminance quantisation table (Annex K) used for quality estimation.
_ANNEX_K_LUMA = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
)


# --------------------------------------------------------------------------- #
# Value normalisation
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> Any:
    """Make an EXIF value JSON-serialisable and bounded in size."""
    from fractions import Fraction

    if isinstance(value, bytes):
        if len(value) > 64:
            return f"<{len(value)} bytes>"
        try:
            text = value.decode("utf-8", errors="strict").strip("\x00").strip()
            return text if text.isprintable() else value.hex()
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, Fraction):
        return float(value) if value.denominator else None
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            return float(value) if value.denominator else None
        except (ZeroDivisionError, TypeError):
            return None
    if isinstance(value, tuple | list):
        cleaned = [_clean(item) for item in value]
        return cleaned[:32]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in list(value.items())[:64]}
    if isinstance(value, str):
        text = value.strip("\x00").strip()
        return text[:512]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:512]


def _parse_exif_datetime(raw: Any) -> str | None:
    """EXIF datetimes are ``YYYY:MM:DD HH:MM:SS`` with no timezone."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().strip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return iso(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return parse_iso(text) and iso(parse_iso(text))


def _match_hint(software: str | None, hints: tuple[str, ...]) -> str | None:
    if not software:
        return None
    lowered = software.lower()
    for hint in hints:
        if hint in lowered:
            return hint
    return None


def _estimate_jpeg_quality(quantization: dict[int, Any] | None) -> float | None:
    """Approximate the JPEG quality setting from the luminance table.

    Ratio of the table against the Annex K reference, inverted through the IJG
    scaling formula. Approximate by construction -- encoders use custom tables --
    so it is reported as an estimate and never as a finding on its own.
    """
    if not quantization:
        return None
    table = quantization.get(0)
    if table is None:
        return None
    values = list(table)[:64]
    if len(values) < 64:
        return None
    ratios = [
        v / ref for v, ref in zip(values, _ANNEX_K_LUMA, strict=False) if ref and v
    ]
    if not ratios:
        return None
    scale = sum(ratios) / len(ratios) * 100.0
    quality = (200.0 - scale) / 2.0 if scale > 100.0 else 5000.0 / max(scale, 1e-6)
    return round(min(100.0, max(1.0, quality)), 1)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
def extract_image_metadata(path: str | Path) -> dict[str, Any]:
    """Extract container, EXIF, camera, timestamp and encoder metadata."""
    payload: dict[str, Any] = {
        "media_type": "image",
        "extractor": EXTRACTOR,
        "warnings": [],
    }

    with Image.open(path) as image:
        image.load()
        payload["container"] = {
            "format": image.format,
            "format_description": Image.MIME.get(image.format or "", None),
            "mode": image.mode,
            "width": image.width,
            "height": image.height,
            "megapixels": round(image.width * image.height / 1_000_000, 3),
            "aspect_ratio": (
                round(image.width / image.height, 4) if image.height else None
            ),
            "n_frames": getattr(image, "n_frames", 1),
            "animated": getattr(image, "is_animated", False),
        }

        icc = image.info.get("icc_profile")
        payload["icc_profile"] = {
            "present": bool(icc),
            "size_bytes": len(icc) if icc else 0,
        }
        xmp = image.info.get("XML:com.adobe.xmp") or image.info.get("xmp")
        payload["xmp"] = {
            "present": bool(xmp),
            "size_bytes": len(xmp) if xmp else 0,
        }

        jpeg_info: dict[str, Any] = {}
        if image.format == "JPEG":
            quantization = getattr(image, "quantization", None)
            jpeg_info = {
                "quantization_tables": len(quantization) if quantization else 0,
                "estimated_quality": _estimate_jpeg_quality(quantization),
                "progressive": bool(image.info.get("progressive")),
                "subsampling": image.info.get("subsampling"),
                "jfif_version": _clean(image.info.get("jfif_version")),
                "comment": _clean(image.info.get("comment")),
            }
        payload["jpeg"] = jpeg_info or {"quantization_tables": 0}

        # --- EXIF / TIFF tags ---
        tags: dict[str, Any] = {}
        gps_raw: dict[str, Any] = {}
        try:
            exif = image.getexif()
        except Exception as exc:  # noqa: BLE001 - malformed EXIF must not abort
            exif = None
            payload["warnings"].append(f"EXIF parse failed ({exc.__class__.__name__}).")

        if exif:
            for tag_id, value in exif.items():
                name = ExifTags.TAGS.get(tag_id, f"Tag{tag_id}")
                tags[name] = _clean(value)
            for ifd_name, ifd_enum in (
                ("Exif", ExifTags.IFD.Exif),
                ("Interop", ExifTags.IFD.Interop),
            ):
                try:
                    sub = exif.get_ifd(ifd_enum)
                except Exception:  # noqa: BLE001
                    continue
                for tag_id, value in (sub or {}).items():
                    name = ExifTags.TAGS.get(tag_id, f"{ifd_name}Tag{tag_id}")
                    tags.setdefault(name, _clean(value))
            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
                gps_raw = {
                    ExifTags.GPSTAGS.get(k, f"GPSTag{k}"): _clean(v)
                    for k, v in gps_ifd.items()
                }
            except Exception:  # noqa: BLE001
                gps_raw = {}

    payload["exif"] = {
        "present": bool(tags),
        "tag_count": len(tags),
        "tags": tags,
    }

    software = tags.get("Software") or tags.get("ProcessingSoftware")
    software = software if isinstance(software, str) and software else None
    payload["software"] = {
        "present": bool(software),
        "value": software,
        "editor_hint": _match_hint(software, _EDITOR_HINTS),
        "generative_hint": _match_hint(software, _GENERATIVE_HINTS),
    }

    make = tags.get("Make") if isinstance(tags.get("Make"), str) else None
    model = tags.get("Model") if isinstance(tags.get("Model"), str) else None
    payload["camera"] = {
        "present": bool(make or model),
        "make": make,
        "model": model,
        "lens": tags.get("LensModel") or tags.get("LensMake"),
        "body_serial": tags.get("BodySerialNumber"),
        "exposure_time": tags.get("ExposureTime"),
        "f_number": tags.get("FNumber"),
        "iso": tags.get("ISOSpeedRatings") or tags.get("PhotographicSensitivity"),
        "focal_length": tags.get("FocalLength"),
        "orientation": tags.get("Orientation"),
    }

    original = _parse_exif_datetime(tags.get("DateTimeOriginal"))
    digitized = _parse_exif_datetime(tags.get("DateTimeDigitized"))
    modified = _parse_exif_datetime(tags.get("DateTime"))
    payload["timestamps"] = {
        "present": any((original, digitized, modified)),
        "exif_datetime_original": original,
        "exif_datetime_digitized": digitized,
        "exif_datetime_modified": modified,
        "filesystem_modified_at": iso(
            from_timestamp(Path(path).stat().st_mtime)
        ),
        "filesystem_note": (
            "Filesystem timestamps reflect this copy on this host, not capture "
            "time; they change on copy and carry little evidentiary weight."
        ),
    }

    payload["gps"] = {
        "present": bool(gps_raw),
        "tags": gps_raw or None,
    }

    payload["presence_summary"] = _presence_summary(payload)
    payload["interpretation"] = INTERPRETATION_NOTE
    return payload


def _presence_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Which metadata classes are present vs absent, with no value judgement."""
    checks = {
        "exif": bool(payload.get("exif", {}).get("present")),
        "camera_information": bool(payload.get("camera", {}).get("present")),
        "capture_timestamp": bool(
            payload.get("timestamps", {}).get("exif_datetime_original")
            or payload.get("timestamps", {}).get("exif_datetime_digitized")
        ),
        "software": bool(payload.get("software", {}).get("present")),
        "gps": bool(payload.get("gps", {}).get("present")),
        "icc_profile": bool(payload.get("icc_profile", {}).get("present")),
        "xmp": bool(payload.get("xmp", {}).get("present")),
    }
    present = sorted(k for k, v in checks.items() if v)
    missing = sorted(k for k, v in checks.items() if not v)
    return {
        "fields_present": present,
        "fields_missing": missing,
        "present_count": len(present),
        "checked_count": len(checks),
        "completeness": round(len(present) / len(checks), 3) if checks else 0.0,
        "stripped_likely": not checks["exif"],
        "note": (
            "'stripped_likely' means no EXIF block was found. That is the normal "
            "state for platform-redistributed media and is not an indicator of "
            "manipulation."
        ),
    }


# --------------------------------------------------------------------------- #
# Video (ISO-BMFF / MP4 / MOV container walk)
# --------------------------------------------------------------------------- #
# ISO-BMFF epoch is 1904-01-01; Unix epoch is 1970-01-01.
_BMFF_EPOCH_OFFSET = 2_082_844_800


def _iter_boxes(data: bytes, start: int, end: int):
    """Yield (type, payload_start, payload_end) for boxes in a byte range."""
    offset = start
    while offset + 8 <= end:
        (size,) = struct.unpack(">I", data[offset : offset + 4])
        box_type = data[offset + 4 : offset + 8].decode("latin-1", "replace")
        header = 8
        if size == 1:  # 64-bit extended size
            if offset + 16 > end:
                return
            (size,) = struct.unpack(">Q", data[offset + 8 : offset + 16])
            header = 16
        elif size == 0:  # extends to end of container
            size = end - offset
        if size < header or offset + size > end:
            return
        yield box_type, offset + header, offset + size
        offset += size


def _bmff_time(seconds: int) -> str | None:
    if not seconds:
        return None
    return iso(from_timestamp(seconds - _BMFF_EPOCH_OFFSET))


def extract_video_metadata(path: str | Path) -> dict[str, Any]:
    """Basic container metadata for MP4/MOV; graceful for other containers.

    Deliberately minimal: brand, duration, creation/modification time and track
    geometry. Deeper codec analysis would need ffprobe, which is not available
    offline here -- that limitation is stated in the payload rather than guessed
    around.
    """
    path = Path(path)
    payload: dict[str, Any] = {
        "media_type": "video",
        "extractor": EXTRACTOR,
        "warnings": [],
        "container": {},
        "timestamps": {},
        "tracks": [],
        "exif": {"present": False, "tag_count": 0, "tags": {}},
        "camera": {"present": False},
        "software": {"present": False, "value": None},
        "gps": {"present": False, "tags": None},
        "icc_profile": {"present": False, "size_bytes": 0},
        "xmp": {"present": False, "size_bytes": 0},
        "jpeg": {"quantization_tables": 0},
    }

    # 8 MiB is enough for ftyp + moov in practice; moov can trail in some files.
    with open(path, "rb") as handle:
        head = handle.read(8 * 1024 * 1024)

    brands: list[str] = []
    found_moov = False
    for box_type, begin, end in _iter_boxes(head, 0, len(head)):
        if box_type == "ftyp":
            major = head[begin : begin + 4].decode("latin-1", "replace")
            brands = [major] + [
                head[i : i + 4].decode("latin-1", "replace")
                for i in range(begin + 8, end, 4)
                if i + 4 <= end
            ]
            payload["container"]["major_brand"] = major
            payload["container"]["compatible_brands"] = [
                b for b in brands if b.strip()
            ][:8]
        elif box_type == "moov":
            found_moov = True
            for inner, ibegin, iend in _iter_boxes(head, begin, end):
                if inner == "mvhd":
                    version = head[ibegin]
                    if version == 1 and iend - ibegin >= 32:
                        created, modified, timescale, duration = struct.unpack(
                            ">QQIQ", head[ibegin + 4 : ibegin + 32]
                        )
                    elif iend - ibegin >= 20:
                        created, modified, timescale, duration = struct.unpack(
                            ">IIII", head[ibegin + 4 : ibegin + 20]
                        )
                    else:
                        continue
                    payload["timestamps"]["container_created_at"] = _bmff_time(created)
                    payload["timestamps"]["container_modified_at"] = _bmff_time(modified)
                    payload["container"]["timescale"] = timescale
                    payload["container"]["duration_seconds"] = (
                        round(duration / timescale, 3) if timescale else None
                    )
                elif inner == "trak":
                    for tbox, tbegin, tend in _iter_boxes(head, ibegin, iend):
                        if tbox == "tkhd" and tend - tbegin >= 84:
                            version = head[tbegin]
                            geom_offset = tbegin + (84 if version == 1 else 76)
                            if geom_offset + 8 <= tend:
                                width, height = struct.unpack(
                                    ">II", head[geom_offset : geom_offset + 8]
                                )
                                # 16.16 fixed point
                                payload["tracks"].append(
                                    {
                                        "width": width >> 16,
                                        "height": height >> 16,
                                    }
                                )

    if not found_moov:
        payload["warnings"].append(
            "No 'moov' box found in the first 8 MiB; container metadata may be "
            "absent, trailing, or the file may not be ISO-BMFF (MP4/MOV)."
        )
    visual = [t for t in payload["tracks"] if t.get("width") and t.get("height")]
    if visual:
        payload["container"]["width"] = visual[0]["width"]
        payload["container"]["height"] = visual[0]["height"]

    payload["timestamps"]["filesystem_modified_at"] = iso(
        from_timestamp(path.stat().st_mtime)
    )
    payload["timestamps"]["present"] = bool(
        payload["timestamps"].get("container_created_at")
    )
    payload["limitations"] = (
        "Video analysis is container-level only in this build: no codec, frame or "
        "per-frame perceptual analysis is performed. Perceptual hashing, "
        "near-duplicate retrieval and propagation apply to images."
    )
    payload["presence_summary"] = _presence_summary(payload)
    payload["interpretation"] = INTERPRETATION_NOTE
    return payload


def extract_metadata(path: str | Path, media_type: str) -> dict[str, Any]:
    """Extract metadata for either media type, never raising for bad input."""
    try:
        if media_type == "image":
            return extract_image_metadata(path)
        if media_type == "video":
            return extract_video_metadata(path)
        return {
            "media_type": media_type,
            "extractor": EXTRACTOR,
            "status": "UNSUPPORTED",
            "warnings": [f"No extractor for media type '{media_type}'."],
            "presence_summary": _presence_summary({}),
            "interpretation": INTERPRETATION_NOTE,
        }
    except Exception as exc:  # noqa: BLE001 - extraction must never break a case
        logger.warning(
            "Metadata extraction failed for %s: %s", path, exc.__class__.__name__
        )
        return {
            "media_type": media_type,
            "extractor": EXTRACTOR,
            "status": "ERROR",
            "error": exc.__class__.__name__,
            "warnings": ["Metadata extraction failed; see server logs."],
            "presence_summary": _presence_summary({}),
            "interpretation": INTERPRETATION_NOTE,
        }
