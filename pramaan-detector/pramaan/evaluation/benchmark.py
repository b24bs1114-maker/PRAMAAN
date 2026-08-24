"""
Benchmark runner — evaluates DetectorService against a labelled file list.
Splits metrics separately by modality (image / video / audio) as required.

CSV format (no header):
    /path/to/file.jpg,1      # 1 = manipulated
    /path/to/file.wav,0      # 0 = authentic
"""
from __future__ import annotations
import csv, json, time
import numpy as np
from pathlib import Path
from collections import defaultdict

from pramaan.service import DetectorService
from pramaan.evaluation.metrics import compute_metrics

_EXT_TO_MODALITY = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".mp4": "video", ".mov": "video",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".aac": "audio",
}


def run_benchmark(
    csv_path: str,
    image_weights: str = None,
    video_weights: str = None,
    audio_weights: str = None,
    device: str = "cpu",
    output_json: str = None,
) -> dict:
    """
    Run detection on every file in csv_path and compute aggregate + per-modality metrics.
    Returns a dict with per-file results, overall metrics, and per-modality metrics.
    """
    service = DetectorService(
        image_weights=image_weights,
        video_weights=video_weights,
        audio_weights=audio_weights,
        device=device,
    )

    rows = []
    with open(csv_path, newline="") as f:
        for file_path, label in csv.reader(f):
            rows.append((file_path.strip(), int(label.strip())))

    # per-modality buckets
    buckets: dict[str, dict] = defaultdict(lambda: {
        "labels": [], "scores": [], "abstained": [], "latencies_ms": []
    })
    all_labels, all_scores, all_abstained, all_latencies = [], [], [], []
    results = []

    for file_path, label in rows:
        result   = service.detect(file_path)
        score    = result.manipulation_score if result.manipulation_score is not None else 0.5
        modality = _EXT_TO_MODALITY.get(Path(file_path).suffix.lower(), "unknown")

        all_labels.append(label)
        all_scores.append(score)
        all_abstained.append(result.abstained)
        all_latencies.append(result.latency_ms)

        buckets[modality]["labels"].append(label)
        buckets[modality]["scores"].append(score)
        buckets[modality]["abstained"].append(result.abstained)
        buckets[modality]["latencies_ms"].append(result.latency_ms)

        row = {
            "file":            file_path,
            "modality":        modality,
            "true_label":      label,
            "predicted_label": result.label,
            "score":           score,
            "abstained":       result.abstained,
            "latency_ms":      round(result.latency_ms, 2),
        }
        # video-specific extras
        if result.media_type == "video" and result.evidence:
            row["temporal_score"]   = result.evidence.get("temporal_score")
            row["frames_analysed"]  = result.evidence.get("frames_analysed")
        results.append(row)

    def _safe_metrics(b: dict) -> dict:
        lats = b.get("latencies_ms", [])
        lat_stats = {
            "latency_ms_mean": round(float(np.mean(lats)), 2) if lats else 0.0,
            "latency_ms_p95":  round(float(np.percentile(lats, 95)), 2) if lats else 0.0,
        }
        if len(set(b["labels"])) < 2:
            return {"note": "insufficient label diversity for full metrics",
                    "abstention_rate": float(sum(b["abstained"]) / max(len(b["abstained"]), 1)),
                    "n_samples": len(b["labels"]), **lat_stats}
        return {**compute_metrics(b["labels"], b["scores"], b["abstained"]), **lat_stats}

    overall = _safe_metrics({"labels": all_labels, "scores": all_scores,
                              "abstained": all_abstained, "latencies_ms": all_latencies})
    per_modality = {mod: _safe_metrics(b) for mod, b in buckets.items()}

    output = {
        "overall_metrics":      overall,
        "per_modality_metrics": per_modality,
        "results":              results,
    }

    if output_json:
        Path(output_json).write_text(json.dumps(output, indent=2))

    return output
