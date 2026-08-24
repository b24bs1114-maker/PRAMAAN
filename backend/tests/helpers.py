"""Deterministic media builders for tests.

Everything is generated in-process: no fixture binaries in the repo, no network.
"""

from __future__ import annotations

import io
import struct
import wave

from PIL import ExifTags, Image, ImageDraw


def make_image(
    width: int = 320,
    height: int = 240,
    seed: int = 7,
    text: str | None = None,
) -> Image.Image:
    """A deterministic, structured (non-uniform) test image.

    Perceptual hashes need actual structure -- a flat colour field produces a
    degenerate hash, so this draws gradients and shapes derived from ``seed``.
    """
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = int(255 * (y / max(1, height - 1)))
        draw.line(
            [(0, y), (width, y)],
            fill=((shade + seed * 13) % 256, (shade * 2 + seed * 7) % 256, shade),
        )
    for i in range(6):
        offset = (seed * (i + 3)) % max(1, width // 3)
        box = (
            offset + i * 12,
            offset + i * 9,
            offset + i * 12 + width // 4,
            offset + i * 9 + height // 5,
        )
        draw.rectangle(box, fill=((seed * 29 + i * 40) % 256, (i * 47) % 256, 90 + i * 20))
    draw.ellipse(
        (width // 4, height // 4, width // 4 + width // 3, height // 4 + height // 3),
        outline=(255, 255, 0),
        width=4,
    )
    if text:
        draw.text((10, 10), text, fill=(255, 255, 255))
    return image


def encode(image: Image.Image, fmt: str = "JPEG", quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    params = {"quality": quality} if fmt.upper() == "JPEG" else {}
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def jpeg_bytes(seed: int = 7, size: tuple[int, int] = (320, 240), quality: int = 92) -> bytes:
    return encode(make_image(size[0], size[1], seed=seed), "JPEG", quality)


def png_bytes(seed: int = 11, size: tuple[int, int] = (200, 160)) -> bytes:
    return encode(make_image(size[0], size[1], seed=seed), "PNG")


def jpeg_with_exif_bytes(
    seed: int = 17,
    size: tuple[int, int] = (320, 240),
    *,
    make: str = "PRAMAAN",
    model: str = "TestCam 1",
    software: str = "Adobe Photoshop 25.0",
    captured_at: str = "2026:01:15 09:30:00",
) -> bytes:
    """A JPEG carrying EXIF make/model/software/DateTimeOriginal tags."""
    image = make_image(size[0], size[1], seed=seed)
    exif = Image.Exif()
    exif[0x010F] = make            # Make
    exif[0x0110] = model           # Model
    exif[0x0131] = software        # Software
    exif[0x0132] = captured_at     # DateTime
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    exif_ifd[0x9003] = captured_at  # DateTimeOriginal
    exif_ifd[0x9004] = captured_at  # DateTimeDigitized
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, exif=exif)
    return buffer.getvalue()


def mp4_bytes(seed: int = 0, *, width: int = 640, height: int = 360, duration_s: int = 5) -> bytes:
    """A minimal, structurally valid ISO-BMFF file: ftyp + moov(mvhd + trak/tkhd).

    Not playable -- there is no media data. It exists so the container metadata
    walker can be tested without shipping a binary fixture.
    """

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    timescale = 1000
    duration = duration_s * timescale
    # ISO-BMFF epoch is 1904-01-01; this resolves to 2026-01-15T09:30:00Z.
    created = 2_082_844_800 + 1_768_469_400
    unity_matrix = struct.pack(
        ">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000
    )

    mvhd = box(
        b"mvhd",
        b"\x00\x00\x00\x00"                                  # version 0 + flags
        + struct.pack(">IIII", created, created, timescale, duration)
        + struct.pack(">IH", 0x00010000, 0x0100)             # rate, volume
        + b"\x00" * 10                                       # reserved
        + unity_matrix
        + b"\x00" * 24                                       # pre_defined
        + struct.pack(">I", 2),                              # next_track_ID
    )
    tkhd = box(
        b"tkhd",
        b"\x00\x00\x00\x03"                                  # version 0, enabled
        + struct.pack(">IIIII", created, created, 1, 0, duration)
        + b"\x00" * 8                                        # reserved
        + struct.pack(">hhhh", 0, 0, 0, 0)                   # layer/group/volume
        + unity_matrix
        + struct.pack(">II", width << 16, height << 16),     # 16.16 fixed point
    )
    moov = box(b"moov", mvhd + box(b"trak", tkhd))
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"iso2mp41")
    return ftyp + moov + (struct.pack(">I", seed) if seed else b"")


def wav_bytes(seed: int = 3, *, duration_ms: int = 120, rate: int = 8000) -> bytes:
    """A real, decodable 16-bit mono PCM WAV carrying a deterministic tone.

    Written with the stdlib ``wave`` module so the RIFF/WAVE structure is
    authentic rather than a hand-assembled header.
    """
    frames = max(1, int(rate * duration_ms / 1000))
    period = 40 + (seed % 20)
    samples = bytearray()
    for n in range(frames):
        # A cheap triangle wave: deterministic, non-silent, no dependencies.
        phase = n % period
        value = int(8000 * (2 * phase / period - 1)) + seed * 11
        samples += struct.pack("<h", max(-32768, min(32767, value)))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(samples))
    return buffer.getvalue()


def m4a_bytes(seed: int = 0) -> bytes:
    """An ISO-BMFF container whose ``ftyp`` brand marks it audio-only.

    Structurally the same box layout as :func:`mp4_bytes`; only the brand differs,
    which is exactly what the sniffer has to key on.
    """
    video = mp4_bytes(seed=seed)
    # Replace the 4-byte major brand at offset 8 ("isom" -> "M4A ").
    return video[:8] + b"M4A " + video[12:]


def mp3_bytes(seed: int = 0) -> bytes:
    """An ID3v2-tagged MPEG audio stub (header only -- not decodable audio)."""
    tag_body = b"\x00" * 32
    header = b"ID3\x03\x00\x00" + bytes(
        (0, 0, 0, len(tag_body))  # synchsafe size, small enough to need one byte
    )
    frame = b"\xff\xfb\x90\x00" + bytes([seed % 256]) * 60
    return header + tag_body + frame

