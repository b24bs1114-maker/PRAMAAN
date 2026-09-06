# PRAMAAN — Final Demo Checklist

Feature-frozen build. Everything below was verified on this machine on
2026-09-05 against the real backend, the real models and the real frontend.

## Team roles for the demo

- **Daksh** — primary technical/product owner: presents the product, system
  architecture, UI/UX, frontend/backend, evidence pipeline, provenance,
  reports, and overall implementation.
- **Suyash** — fusion and forensic demo validation: explains fusion, checks the
  AI/forensic outputs during rehearsal and the live demo.
- **Dev** — demo QA and reliability: startup/runbook verification, pre-demo
  smoke checks of all three modalities, backup files, report
  generation/download, case-deletion safety, and first-line recovery (see
  "Recovery steps" below).

Full ownership answers: [JUDGE_QA.md](JUDGE_QA.md).

## Startup

```bash
cd /Users/dakshjain/PRAMAAN
source .venv/bin/activate
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd /Users/dakshjain/PRAMAAN/frontend && npm run dev
```

- Frontend: http://localhost:5173
- Backend: http://127.0.0.1:8000
- No login or account is required. The app has no authentication.
- The frontend reaches the backend through Vite's `/api/backend` proxy, so no
  env vars are needed for a local demo.

## Health verification (before the demo)

```bash
curl -s http://127.0.0.1:8000/health                          # {"status":"ok"}
curl -s http://127.0.0.1:8000/api/detector/status | python3 -m json.tool
```

All three modalities must show `"available": true`:

| Modality | Model | Checkpoint SHA-256 (prefix) |
|---|---|---|
| image | OwensLab-CommunityForensics-ViT384 | b89f36275f3bf5e2 |
| audio | AASIST-Audio-Spoof-Detector | 51d2d9cf0738172f |
| video | VideoMAE-DeepFake-Detector | 293c668a5d3289d3 |

## Demo evidence files

Real, verified samples in `pramaan-detector/data/real_samples/`:

- `image_authentic.jpg` — real photo (image detector returns a low score)
- `image_deepfake.jpg` — photo fixture (image detector returns a low score; the
  model is honest about what it measures — see Known limitations)
- `audio_authentic.wav` / `audio_deepfake.wav` — AASIST scores these
- `video_authentic.mp4` / `video_deepfake.mp4` — VideoMAE samples (3 s, 16 frames)

Backup corpus images: `corpus/images/` (labelled transformation lineage) and
`backend/tests/fixtures/demo_images/`.

## Exact demo flow

1. Open http://localhost:5173 — Dashboard shows system status.
2. Click **+ New Case**, drop `image_authentic.jpg` (title: "Complaint photo",
   examiner: your name). The case number appears as PRAMAAN-YYYYMMDD-NNNN.
3. **Analysis**: click Run Analysis. Inspect the verdict band, confidence band
   (a word — LOW/MODERATE, never a percentage), signal coverage, the five
   forensic signals with per-signal explanation, and "Why this verdict" with
   the backend's own fusion arithmetic. Open a signal row for its basis
   (model, weights hash, inference time).
4. **Trace Provenance** (Analysis screen button): the earliest known instance
   wording, variants, propagation graph. The label is always
   "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS" with the caveat
   that earlier copies may exist outside the corpus.
5. **Audit**: verify the chain — it must report VALID with the head hash.
6. **Report**: generate the PDF; the download opens a real reportlab document
   carrying the case number, evidence SHA-256, model record, limitations and
   the unsigned examiner review block.
7. **Cases**: return to the queue, create a second case (upload
   `audio_deepfake.wav`), open it, switch back to the first case — no stale
   state carries over.
8. Delete the second case: type the case number to confirm, verify the removal
   notice lists exactly what the backend removed, and that the first case is
   untouched.

## Recovery steps if a detector fails

- If `image`/`audio` shows `available: false`: the `.env` model path is wrong.
  The correct paths (already set) point at
  `pramaan-detector/weights/image_detector.safetensors`,
  `audio_detector.pth`, `video_detector.safetensors`.
- If the backend will not start: `lsof -ti:8000 | xargs kill -9` (a stale
  uvicorn holding the port), then restart.
- To demo with the detector stage fully off (emergency mode), set
  `PRAMAAN_ENABLE_AI_DETECTOR=false` in `.env` and restart. The pipeline then
  reports the ai_detection signal as UNAVAILABLE and excludes it — all other
  stages (hashing, provenance, audit, report) keep working.
- A detector that runs and cannot decide reports INCONCLUSIVE and is excluded
  from the score (the video samples sit near the 0.5 midpoint and demonstrate
  this honest abstention).

## Known limitations (state these if asked)

- All fusion weights and thresholds are uncalibrated prototype values; every
  score is a model output, not a probability.
- "Earliest known instance" is scoped to PRAMAAN's indexed corpus — never
  presented as real-world origin.
- The image detector (CommunityForensics ViT) detects AI-generated imagery;
  ordinary photos legitimately score low, and heavy recompression can lower
  scores on synthetic images.
- Public web discovery requires Google Cloud Vision credentials; without them
  it reports PUBLIC_WEB_DISCOVERY_UNAVAILABLE (truthfully, in the UI).
- Compression forensics is capped at 0.60 and can never alone produce a
  MANIPULATED verdict.
- VideoMAE abstains on scores within |score-0.5| < 0.15.

## Final test commands

```bash
# Backend (485 passed, 1 skipped at freeze)
cd backend && source ../.venv/bin/activate && python -m pytest

# Detector package (84 passed)
python -m pytest ../pramaan-detector/tests

# Model asset verification (SHA-256 + real load)
python ../scripts/verify_model_assets.py

# Integration recordings + full workflow/deletion verification (104 checks)
python ../scripts/verify_integration.py

# Frontend: typecheck + tests + build + contract replay (149 checks)
cd ../frontend
npm run typecheck && npm run typecheck:tests && npm run build
npm run verify:contract
```
