# PRAMAAN Local Inference Service

A small, standalone FastAPI wrapper around the existing multi-modal detector
(`pramaan-detector`). It adds **no detection logic** — it routes an uploaded
file to `DetectorService` (which routes to `ImageDetector` / `VideoDetector`
/ `AudioDetector`), loads the existing weights from
`pramaan-detector/weights/`, and returns the `DetectionResult` unchanged.

Runs on your Mac, bound to **127.0.0.1 only**. Not for public exposure.

## Run

```bash
cd scripts/local-inference
/Users/dakshjain/PRAMAAN/.venv/bin/uvicorn service:app --host 127.0.0.1 --port 8432
```

Optional env vars:

| Variable | Default | Purpose |
|---|---|---|
| `PRAMAAN_LOCAL_API_KEY` | *(unset)* | When set, requests must send `X-API-Key: <key>` or `Authorization: Bearer <key>` |
| `PRAMAAN_LOCAL_PORT` | `8432` | Port (used by `python service.py`) |
| `PRAMAAN_LOCAL_MAX_UPLOAD_MB` | `500` | Upload size limit |
| `PRAMAAN_TORCH_THREADS` | `1` | Passed through to detector |
| `PRAMAAN_DETECTOR_HEATMAP` | `1` | Passed through to detector |

## Endpoints

- `GET /health` — per-modality (image/video/audio) checkpoint availability
- `POST /detect` — multipart file; modality auto-detected from extension
- `POST /detect/image` | `/detect/video` | `/detect/audio` — explicit modality

Every detection response carries the real `DetectionResult` fields:
`label`, `manipulation_score` (null when abstained — never a fake 0),
`confidence` (null unless the model itself reported one), `model`,
`model_version`, `weights_hash`, `latency_ms`, `explanation`,
`heatmap_available`, `regions`, `timestamps`, `abstained`, `evidence`.

## Example curl

```bash
curl -s http://127.0.0.1:8432/health

curl -s -X POST http://127.0.0.1:8432/detect \
  -F "file=@pramaan-detector/data/real_samples/image_authentic.jpg"

curl -s -X POST http://127.0.0.1:8432/detect/audio \
  -F "file=@pramaan-detector/data/real_samples/audio_authentic.wav"

curl -s -X POST http://127.0.0.1:8432/detect/video \
  -F "file=@pramaan-detector/data/real_samples/video_deepfake.mp4"
```

## Tests

```bash
cd scripts/local-inference
/Users/dakshjain/PRAMAAN/.venv/bin/python -m pytest test_service.py -v
```

One smoke test per modality plus health, unsupported-media, and API-key
tests. All use real sample files and real weights — no mocks.
