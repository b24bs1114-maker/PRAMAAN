"""Secure local evidence storage.

Rules enforced here, because ingestion is the one place untrusted input touches
the filesystem:

* **Client filenames are never used as paths.** Files are stored under a
  server-generated UUID, so ``../../etc/passwd`` and friends cannot escape --
  the original name is kept as metadata only.
* Every resolved path is re-checked to be inside the evidence root before use.
* Type is decided by **magic bytes**, not the client's ``Content-Type``.
* Uploads are streamed to a temp file with a hard size cap, then hashed *from
  the stored bytes* and only then moved into place.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.services.hashing import sha256_file

logger = logging.getLogger("pramaan.storage")

MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_AUDIO = "audio"

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
_MAX_FILENAME = 180

# (offset, signature, mime, media_type, canonical extension)
_MAGIC: tuple[tuple[int, bytes, str, str, str], ...] = (
    (0, b"\xff\xd8\xff", "image/jpeg", MEDIA_IMAGE, "jpg"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png", MEDIA_IMAGE, "png"),
    (0, b"GIF87a", "image/gif", MEDIA_IMAGE, "gif"),
    (0, b"GIF89a", "image/gif", MEDIA_IMAGE, "gif"),
    (0, b"BM", "image/bmp", MEDIA_IMAGE, "bmp"),
    (0, b"II*\x00", "image/tiff", MEDIA_IMAGE, "tif"),
    (0, b"MM\x00*", "image/tiff", MEDIA_IMAGE, "tif"),
    (0, b"\x1a\x45\xdf\xa3", "video/x-matroska", MEDIA_VIDEO, "mkv"),
    (0, b"fLaC", "audio/flac", MEDIA_AUDIO, "flac"),
    (0, b"OggS", "audio/ogg", MEDIA_AUDIO, "ogg"),
    (0, b"ID3", "audio/mpeg", MEDIA_AUDIO, "mp3"),
)

#: ISO-BMFF (``ftyp``) brands that identify an audio-only container. Everything
#: else with an ``ftyp`` box stays MP4 video, which is what this module did
#: before audio existed -- an unrecognised brand must not become audio.
_AUDIO_FTYP_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "})

#: MPEG audio / ADTS frame sync. The second byte carries the MPEG version and
#: layer, which is the only thing separating a bare MP3 frame from ADTS AAC.
_MP3_FRAME_SYNC = frozenset({b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2"})
_AAC_FRAME_SYNC = frozenset({b"\xff\xf1", b"\xff\xf9"})



class StorageError(Exception):
    """Ingestion rejected the input. Message is safe to return to a client."""


class PayloadTooLargeError(StorageError):
    """The upload exceeded ``max_upload_bytes``.

    Separate from the other rejections so the API layer can answer 413 rather
    than 400. A client needs to distinguish "this file is too big for the
    configured limit" -- which is fixed by changing the file or the limit --
    from "this file is malformed or of an unsupported type".
    """


@dataclass
class StagedUpload:
    """A validated file sitting in the staging area, not yet committed."""

    temp_path: Path
    filename: str
    media_type: str
    mime_type: str
    extension: str
    size_bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None
    image_format: str | None = None
    warnings: list[str] = field(default_factory=list)

    def discard(self) -> None:
        self.temp_path.unlink(missing_ok=True)


def sanitize_filename(raw: str | None) -> str:
    """Reduce a client-supplied name to a safe, display-only basename."""
    if not raw:
        return "unnamed"
    # Strip any directory component, whichever separator the client used.
    name = str(raw).replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    name = re.sub(r"_{3,}", "__", name)
    if not name:
        return "unnamed"
    return name[:_MAX_FILENAME]


def _sniff(header: bytes) -> tuple[str, str, str] | None:
    """Identify (mime, media_type, extension) from magic bytes."""
    for offset, signature, mime, media, ext in _MAGIC:
        if header[offset : offset + len(signature)] == signature:
            return mime, media, ext
    if header[4:8] == b"ftyp":
        # One container, two media types: the brand decides. Anything that is not
        # a known audio brand remains MP4 video, as before.
        if header[8:12] in _AUDIO_FTYP_BRANDS:
            return "audio/mp4", MEDIA_AUDIO, "m4a"
        return "video/mp4", MEDIA_VIDEO, "mp4"
    if header[:4] == b"RIFF" and len(header) >= 12:
        if header[8:12] == b"WEBP":
            return "image/webp", MEDIA_IMAGE, "webp"
        if header[8:12] == b"AVI ":
            return "video/x-msvideo", MEDIA_VIDEO, "avi"
        if header[8:12] == b"WAVE":
            return "audio/wav", MEDIA_AUDIO, "wav"
    if header[:2] in _MP3_FRAME_SYNC:
        return "audio/mpeg", MEDIA_AUDIO, "mp3"
    if header[:2] in _AAC_FRAME_SYNC:
        return "audio/aac", MEDIA_AUDIO, "aac"
    return None


def resolve_within(root: Path, *parts: str) -> Path:
    """Join ``parts`` under ``root``, refusing anything that escapes it."""
    root = root.resolve()
    candidate = (root / Path(*parts)).resolve()
    if candidate != root and root not in candidate.parents:
        raise StorageError("Resolved path escapes the storage root.")
    return candidate


def stage_upload(
    stream: BinaryIO,
    *,
    filename: str | None,
    settings: Settings,
    declared_mime: str | None = None,
) -> StagedUpload:
    """Stream, validate, hash and describe an upload without committing it.

    Raises ``StorageError`` with a client-safe message on any rejection.
    """
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(filename)
    temp_path = settings.temp_dir / f"staging-{uuid.uuid4().hex}"

    size = 0
    try:
        with open(temp_path, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise PayloadTooLargeError(
                        "File exceeds the maximum upload size of "
                        f"{settings.max_upload_bytes} bytes."
                    )
                out.write(chunk)
    except StorageError:
        temp_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        logger.error("Failed to stage upload: %s", exc.__class__.__name__)
        raise StorageError("Could not write the uploaded file to storage.") from exc

    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise StorageError("Uploaded file is empty.")

    with open(temp_path, "rb") as handle:
        header = handle.read(32)

    sniffed = _sniff(header)
    if sniffed is None:
        temp_path.unlink(missing_ok=True)
        raise StorageError(
            "Unsupported or unrecognised file type. Supported: JPEG, PNG, WEBP, "
            "TIFF, BMP, GIF, MP4, MOV, MKV, AVI, WAV, MP3, M4A, AAC, FLAC, OGG."
        )
    mime, media_type, extension = sniffed

    allowed = {
        MEDIA_IMAGE: settings.image_extensions,
        MEDIA_VIDEO: settings.video_extensions,
        MEDIA_AUDIO: settings.audio_extensions,
    }.get(media_type, set())
    if extension not in allowed:
        temp_path.unlink(missing_ok=True)
        raise StorageError(f"File type '{extension}' is not permitted.")

    warnings: list[str] = []
    if declared_mime and declared_mime.split(";")[0].strip() != mime:
        # Recorded, not fatal: the sniffed type always wins.
        warnings.append(
            f"Declared content type '{declared_mime}' does not match detected "
            f"type '{mime}'; detected type used."
        )

    width = height = None
    image_format = None
    if media_type == MEDIA_IMAGE:
        try:
            # verify() detects truncation/corruption but consumes the file object.
            with Image.open(temp_path) as probe:
                probe.verify()
            with Image.open(temp_path) as image:
                image.load()
                width, height = image.size
                image_format = image.format
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            temp_path.unlink(missing_ok=True)
            logger.info("Rejected undecodable image: %s", exc.__class__.__name__)
            raise StorageError(
                "File could not be decoded as a valid image; it may be corrupted "
                "or truncated."
            ) from exc

    # Hash the bytes as stored on disk -- this is the evidentiary digest.
    digest = sha256_file(temp_path)

    return StagedUpload(
        temp_path=temp_path,
        filename=safe_name,
        media_type=media_type,
        mime_type=mime,
        extension=extension,
        size_bytes=size,
        sha256=digest,
        width=width,
        height=height,
        image_format=image_format,
        warnings=warnings,
    )


def commit_upload(
    staged: StagedUpload,
    *,
    evidence_id: str,
    settings: Settings,
    bucket: str = "cases",
    bucket_key: str = "unassigned",
) -> str:
    """Move a staged file to its permanent location.

    Returns the path relative to ``data_dir`` (portable across deployments).
    The stored name is derived from the server-generated evidence id only.
    """
    target_dir = resolve_within(settings.evidence_dir, bucket, bucket_key)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = resolve_within(
        settings.evidence_dir, bucket, bucket_key, f"{evidence_id}.{staged.extension}"
    )
    shutil.move(str(staged.temp_path), target)
    os.chmod(target, 0o640)
    # data_dir is resolved too: on macOS /tmp is a symlink to /private/tmp, so an
    # unresolved root would not be a parent of the resolved target.
    return str(target.relative_to(settings.data_dir.resolve()))


def absolute_path(stored_path: str, settings: Settings) -> Path:
    """Resolve a stored relative path back to an absolute one, safely."""
    return resolve_within(settings.data_dir, stored_path)


def store_local_file(
    source: Path,
    *,
    evidence_id: str,
    settings: Settings,
    bucket: str = "corpus",
    bucket_key: str = "default",
) -> str:
    """Copy a file that is already on disk (corpus ingestion) into storage."""
    target_dir = resolve_within(settings.evidence_dir, bucket, bucket_key)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = resolve_within(
        settings.evidence_dir, bucket, bucket_key, f"{evidence_id}{source.suffix.lower()}"
    )
    shutil.copy2(source, target)
    return str(target.relative_to(settings.data_dir.resolve()))
