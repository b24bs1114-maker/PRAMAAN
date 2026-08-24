"""Compression forensics: recompression residual and JPEG-grid analysis.

An additional, independent signal that looks at the *encoding history* of the
pixels rather than at their content. Everything here is computed locally with
Pillow and numpy -- no model, no network.

**1. Texture-normalised recompression residual (ELA family).** The image is
re-encoded at a fixed JPEG quality and differenced against itself. The raw
residual is dominated by content: edges and texture always move more than flat
regions, so a raw residual statistic mostly measures how busy the picture is.
This module therefore fits per-tile residual against per-tile gradient energy and
reports how many tiles deviate from that relationship. Tiles whose residual is
not explained by their own texture are the ones worth looking at.

**2. JPEG block-grid alignment.** Block-boundary energy is measured at each of
the 8 possible phases. Clean 8-pixel periodicity at phase 0 is the signature of a
single grid-aligned JPEG encode; a dominant peak at a non-zero phase suggests the
pixels were shifted after compression -- a crop, or a paste from another JPEG.

**Calibration status: NONE, and the score band is deliberately narrow.**
Measured on the synthetic corpus, this metric tracks *prior JPEG quality* more
closely than it tracks manipulation: heavily recompressed copies score highest
and pristine synthetic originals overlap with mildly transformed ones. That is
what the measurement genuinely detects, so the score is clamped to
``[0.15, 0.60]`` -- below the manipulation threshold -- and can never on its own
produce a MANIPULATED verdict. It is a pointer for examiner review, nothing more.
Resizing, screenshots, platform re-encoding and low-texture images all raise
these numbers with no manipulation involved.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger("pramaan.forensics")

ANALYSER = "pramaan-compression-forensics/1.0 (recompression residual + JPEG grid)"

STATUS_OK = "OK"
STATUS_UNSUPPORTED = "UNSUPPORTED_MEDIA"
STATUS_ERROR = "ERROR"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"

CALIBRATION_NOTE = (
    "PROTOTYPE HEURISTIC, NOT CALIBRATED. This ramp was chosen by measuring the "
    "spread across the synthetic demo corpus, NOT by validation against a "
    "forensic reference dataset. On that corpus the metric tracked prior JPEG "
    "quality more strongly than manipulation, and pristine originals overlapped "
    "with transformed copies. The score is therefore capped at 0.60 -- below the "
    "manipulation threshold -- so this signal can never on its own produce a "
    "MANIPULATED verdict."
)

INTERPRETATION = (
    "Compression analysis describes the ENCODING HISTORY of the pixels, not the "
    "truthfulness of the image. An elevated score means some regions recompress "
    "differently than their own texture predicts, which merits examiner review. "
    "It is not a finding of manipulation, and a low score is not a finding of "
    "authenticity."
)

# Analysis parameters.
RECOMPRESS_QUALITY = 90
BLOCK = 8                      # JPEG minimum coded unit
TILE = 32                      # analysis tile size, in pixels
MAX_EDGE = 1024                # analyse at most this many pixels on the long edge
MIN_EDGE = 64                  # below this, measurements are not meaningful
OUTLIER_SIGMA = 3.0            # robust z-threshold for an unexplained tile

# Uncalibrated ramp over the fraction of texture-unexplained tiles.
OUTLIER_RAMP = (
    (0.05, 0.15),   # at or below: residual is well explained by texture
    (0.15, 0.30),
    (0.30, 0.45),
    (0.45, 0.60),
)
GRID_PHASE_BONUS = 0.05        # dominant off-grid phase, added then clamped
SCORE_CEILING = 0.60           # hard cap: see CALIBRATION_NOTE


def _ramp(value: float, points: tuple[tuple[float, float], ...], ceiling: float) -> float:
    """Piecewise-linear interpolation through ``points``, flat past the last one."""
    previous_x, previous_y = 0.0, points[0][1]
    for x, y in points:
        if value <= x:
            if x <= previous_x:
                return y
            fraction = (value - previous_x) / (x - previous_x)
            return previous_y + fraction * (y - previous_y)
        previous_x, previous_y = x, y
    return ceiling


def _load_luma(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Load the first frame as a float32 luminance array, downscaled if large."""
    with Image.open(path) as image:
        image.load()
        info = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "frames_analysed": 1,
            "animated": bool(getattr(image, "is_animated", False)),
        }
        frame = image.convert("L")
        scale = max(frame.width, frame.height) / MAX_EDGE
        if scale > 1.0:
            frame = frame.resize(
                (max(int(frame.width / scale), 1), max(int(frame.height / scale), 1)),
                Image.Resampling.LANCZOS,
            )
            info["downscaled_to"] = [frame.width, frame.height]
            info["downscale_note"] = (
                "Analysed at reduced resolution for speed. Downscaling resamples "
                "the JPEG grid, so grid-phase results on downscaled images carry "
                "less weight."
            )
        return np.asarray(frame, dtype=np.float32), info


def _tile_means(array: np.ndarray, tiles_y: int, tiles_x: int) -> np.ndarray:
    """Mean value per TILE x TILE tile, flattened."""
    cropped = array[: tiles_y * TILE, : tiles_x * TILE]
    return cropped.reshape(tiles_y, TILE, tiles_x, TILE).mean(axis=(1, 3)).ravel()


def _recompression_residual(luma: np.ndarray) -> dict[str, Any]:
    """Difference the image against its own JPEG re-encode, normalised by texture."""
    buffer = io.BytesIO()
    Image.fromarray(luma.astype(np.uint8), mode="L").save(
        buffer, format="JPEG", quality=RECOMPRESS_QUALITY, subsampling=0
    )
    buffer.seek(0)
    with Image.open(buffer) as reopened:
        recompressed = np.asarray(reopened.convert("L"), dtype=np.float32)

    residual = np.abs(luma - recompressed)
    gradient = (
        np.abs(np.diff(luma, axis=1, append=luma[:, -1:]))
        + np.abs(np.diff(luma, axis=0, append=luma[-1:, :]))
    ) / 2.0

    height, width = residual.shape
    tiles_y, tiles_x = height // TILE, width // TILE
    if tiles_y < 2 or tiles_x < 2:
        return {
            "status": STATUS_INSUFFICIENT,
            "mean_residual": round(float(residual.mean()), 4),
            "detail": "Image is too small to measure spatial variation in residual.",
        }

    tile_residual = _tile_means(residual, tiles_y, tiles_x)
    tile_gradient = _tile_means(gradient, tiles_y, tiles_x)

    # Least-squares fit of residual against texture, then robust outlier count.
    design = np.vstack([tile_gradient, np.ones_like(tile_gradient)]).T
    coefficients, *_ = np.linalg.lstsq(design, tile_residual, rcond=None)
    predicted = design @ coefficients
    fit_error = tile_residual - predicted

    total_variance = float(((tile_residual - tile_residual.mean()) ** 2).sum())
    r_squared = (
        1.0 - float((fit_error**2).sum()) / total_variance
        if total_variance > 1e-9
        else 0.0
    )
    # MAD-based sigma: a few spliced tiles must not inflate their own threshold.
    mad = float(np.median(np.abs(fit_error - np.median(fit_error))))
    sigma = 1.4826 * mad if mad > 1e-9 else float(fit_error.std())
    z_scores = np.abs(fit_error) / max(sigma, 1e-6)
    outliers = z_scores > OUTLIER_SIGMA

    mean_residual = float(tile_residual.mean())
    hottest = int(np.argmax(z_scores))
    hot_y, hot_x = divmod(hottest, tiles_x)
    return {
        "status": STATUS_OK,
        "quality_used": RECOMPRESS_QUALITY,
        "tile_size_px": TILE,
        "tiles": int(tile_residual.size),
        "mean_residual": round(mean_residual, 4),
        "p99_residual": round(float(np.percentile(residual, 99)), 4),
        "heterogeneity": round(
            float(tile_residual.std()) / mean_residual if mean_residual > 1e-6 else 0.0,
            4,
        ),
        "texture_fit_r_squared": round(float(r_squared), 4),
        "outlier_sigma": OUTLIER_SIGMA,
        "outlier_tiles": int(outliers.sum()),
        "outlier_fraction": round(float(outliers.mean()), 4),
        "metric_definition": (
            "Per-tile mean absolute recompression residual is fitted against "
            "per-tile gradient energy; 'outlier_fraction' is the share of tiles "
            f"deviating more than {OUTLIER_SIGMA} robust sigma from that fit, i.e. "
            "tiles whose recompression behaviour is not explained by their own "
            "texture. 'heterogeneity' (std/mean of raw tile residual) is reported "
            "for reference only -- it is content-dominated and is not scored."
        ),
        "hottest_tile": {
            "tile_x": hot_x,
            "tile_y": hot_y,
            "z_score": round(float(z_scores[hottest]), 3),
            "pixel_box": [
                hot_x * TILE,
                hot_y * TILE,
                hot_x * TILE + TILE,
                hot_y * TILE + TILE,
            ],
            "note": (
                "Most texture-unexplained region. A pointer for manual review "
                "only -- it is not a localisation of tampering."
            ),
        },
    }


def _grid_alignment(luma: np.ndarray) -> dict[str, Any]:
    """Measure block-boundary energy at each of the 8 possible JPEG phases."""
    if min(luma.shape) < MIN_EDGE:
        return {
            "status": STATUS_INSUFFICIENT,
            "detail": "Image is too small for block-grid analysis.",
        }

    column_diff = np.abs(np.diff(luma, axis=1)).mean(axis=0)
    row_diff = np.abs(np.diff(luma, axis=0)).mean(axis=1)

    phases: list[float] = []
    for phase in range(BLOCK):
        # Boundaries between blocks fall at columns/rows == phase-1 (mod 8).
        col_idx = np.arange((phase - 1) % BLOCK, len(column_diff), BLOCK)
        row_idx = np.arange((phase - 1) % BLOCK, len(row_diff), BLOCK)
        boundary = np.concatenate((column_diff[col_idx], row_diff[row_idx]))
        phases.append(float(boundary.mean()) if boundary.size else 0.0)

    overall = float((column_diff.mean() + row_diff.mean()) / 2.0)
    best_phase = int(np.argmax(phases))
    best = phases[best_phase]
    others = [p for i, p in enumerate(phases) if i != best_phase]
    peak_ratio = best / (sum(others) / len(others)) if others and best else 0.0

    return {
        "status": STATUS_OK,
        "block_size_px": BLOCK,
        "phase_energies": [round(p, 4) for p in phases],
        "dominant_phase": best_phase,
        "peak_ratio": round(peak_ratio, 4),
        "mean_gradient": round(overall, 4),
        "grid_detected": bool(peak_ratio >= 1.05),
        "off_grid": bool(peak_ratio >= 1.05 and best_phase != 0),
        "phase_definition": (
            "Phase 0 is alignment with the standard 8x8 JPEG grid. A dominant "
            "non-zero phase suggests the pixels were shifted after compression "
            "(crop or paste); benign resizing produces the same effect."
        ),
    }


def _score(residual: dict[str, Any], grid: dict[str, Any]) -> tuple[float | None, str]:
    """Map the measurements onto an uncalibrated, capped 0-0.60 concern score."""
    if residual.get("status") != STATUS_OK:
        return None, (
            "No score: the recompression residual could not be measured "
            f"({residual.get('detail', 'insufficient data')})."
        )

    fraction = float(residual["outlier_fraction"])
    score = _ramp(fraction, OUTLIER_RAMP, SCORE_CEILING)
    reasons = [
        f"{residual['outlier_tiles']} of {residual['tiles']} tiles "
        f"({fraction:.1%}) recompress differently than their own texture "
        f"predicts, mapping to {score:.2f} on the prototype ramp"
    ]
    if grid.get("off_grid"):
        score = min(score + GRID_PHASE_BONUS, SCORE_CEILING)
        reasons.append(
            f"block-grid energy peaks at phase {grid['dominant_phase']} rather "
            f"than 0 (peak ratio {grid['peak_ratio']}), adding "
            f"{GRID_PHASE_BONUS:.2f}"
        )
    else:
        reasons.append("block-grid energy shows no dominant off-grid phase")
    reasons.append(f"capped at {SCORE_CEILING:.2f} because the ramp is uncalibrated")
    return round(min(score, SCORE_CEILING), 4), "; ".join(reasons) + "."


def analyse(path: str | Path, media_type: str = "image") -> dict[str, Any]:
    """Run compression forensics on one file. Never raises."""
    payload: dict[str, Any] = {
        "analyser": ANALYSER,
        "media_type": media_type,
        "interpretation": INTERPRETATION,
        "calibration": CALIBRATION_NOTE,
    }

    if media_type != "image":
        payload.update(
            status=STATUS_UNSUPPORTED,
            score=None,
            detail=(
                "Compression forensics is implemented for still images only; "
                f"media type is '{media_type}'. No score is produced."
            ),
        )
        return payload

    file_path = Path(path)
    try:
        luma, info = _load_luma(file_path)
    except Exception as exc:  # noqa: BLE001 - unreadable/corrupt file must not abort
        payload.update(
            status=STATUS_ERROR,
            score=None,
            detail=f"Could not decode image for analysis ({type(exc).__name__}).",
        )
        return payload

    payload["image"] = info
    if min(luma.shape) < MIN_EDGE:
        payload.update(
            status=STATUS_INSUFFICIENT,
            score=None,
            detail=(
                f"Image is smaller than the {MIN_EDGE}px minimum edge for "
                "compression analysis; no score is produced."
            ),
        )
        return payload

    try:
        residual = _recompression_residual(luma)
        grid = _grid_alignment(luma)
    except Exception as exc:  # noqa: BLE001
        logger.info("Compression forensics failed: %s", exc.__class__.__name__)
        payload.update(
            status=STATUS_ERROR,
            score=None,
            detail=f"Compression analysis failed ({type(exc).__name__}).",
        )
        return payload

    score, explanation = _score(residual, grid)
    payload.update(
        status=STATUS_OK if score is not None else STATUS_INSUFFICIENT,
        score=score,
        recompression=residual,
        block_grid=grid,
        explanation=explanation,
        thresholds={
            "outlier_ramp": [list(point) for point in OUTLIER_RAMP],
            "outlier_sigma": OUTLIER_SIGMA,
            "off_grid_bonus": GRID_PHASE_BONUS,
            "score_ceiling": SCORE_CEILING,
            "basis": CALIBRATION_NOTE,
        },
    )
    return payload
