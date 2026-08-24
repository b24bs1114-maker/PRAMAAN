# Plugging a detector into PRAMAAN

PRAMAAN's AI-manipulation detector is a replaceable component. This document is
the contract: what the backend calls, what it expects back, and what happens to
the answer. Nothing in `app/services/` changes when a model arrives — a model is
installed by configuration or by one registration call.

Interface version: **2.0** (`detector.INTERFACE_VERSION`). It is reported on
every result, every status payload and every audit row, so a stored analysis
always names the contract it was produced under.

The live version of everything below is served by the running backend:

```bash
curl -s localhost:8000/api/system/status | python -m json.tool
```

`detector_contract` in that response is generated from the code, not from this
file. If the two ever disagree, the endpoint is right.

---

## 1. What the backend calls

One entrypoint, for all three modalities:

```python
result = get_detector(settings).analyse(path, media_type="image" | "video" | "audio")
result.to_dict()
```

`media_type` is resolved by PRAMAAN before the call — from the stored media type
for registered evidence, or by sniffing the file's bytes at ingestion. A modality
the deployment has no model for is reported `UNAVAILABLE`; a modality the adapter
cannot handle at all is `UNSUPPORTED_MEDIA`. **An unconfigured video model is the
former, not the latter** — video becomes measurable the moment a video detector is
plugged in, so it must not be reported as an unsupportable media type.

## 2. The two sockets

Both are additive. Neither requires editing `detector.py`.

### Socket 1 — a model file

For a plain ONNX or TorchScript image classifier, a path is all that is needed:

```bash
PRAMAAN_IMAGE_MODEL_PATH=/models/image-detector.onnx
```

Preprocessing is described by an optional JSON sidecar next to the model —
`image-detector.onnx.json` or `image-detector.json`:

```json
{
  "input_size": [224, 224],
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "layout": "NCHW",
  "positive_index": 1,
  "output_activation": "softmax",
  "model_name": "rahul-image-v1",
  "model_version": "1.0.0"
}
```

Missing keys fall back to `detector.DEFAULT_SPEC`. An unreadable sidecar is
logged and ignored rather than fatal.

### Socket 2 — inference code

For any video or audio engine, and for an image engine that is not a plain
classifier (multi-crop, temporal aggregation, custom decoding, an ensemble):

```bash
PRAMAAN_VIDEO_MODEL_PATH=/models/video-detector.pt          # optional
PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT=rahul_engine.video:analyse # module:callable
```

The callable receives whichever of these keyword arguments it declares — PRAMAAN
inspects the signature, so an unused argument may simply be omitted:

| argument | meaning |
| --- | --- |
| `path` (first positional, or `path=` / `file_path=`) | absolute path to the media file |
| `media_type` | `"image"`, `"video"` or `"audio"` |
| `model_path` | the configured `*_MODEL_PATH`, or `None` |
| `spec` | the sidecar spec merged over the defaults |

The smallest valid implementation:

```python
def analyse(path, media_type=None, model_path=None, spec=None):
    return 0.87  # 0.0 = no indication, 1.0 = strong indication
```

The full form — every key optional except `score`:

```python
def analyse(path, media_type=None, model_path=None, spec=None):
    return {
        "score": 0.87,                 # None means "I cannot say" -> honest abstention
        "confidence": 0.91,            # the MODEL's own confidence; omit if it has none
        "model": "rahul-video-detector",
        "model_version": "1.2.0",
        "weights_hash": "…",           # omit: PRAMAAN hashes the model file itself
        "explanation": "Temporal inconsistency across frames 120-184.",
        "heatmap_available": True,
        "regions": [                   # image/video, spatial
            {"x": 0.31, "y": 0.44, "w": 0.12, "h": 0.09, "score": 0.93}
        ],
        "timestamps": [                # video/audio, temporal
            {"start_s": 4.0, "end_s": 6.13, "score": 0.88}
        ],
    }
```

`(score, extras)` and `(score, confidence, extras)` tuples are accepted too. Any
key PRAMAAN does not recognise is carried through untouched in `extras`, stored
with the analysis, and available to the UI — so a model can return more than this
list without a backend change.

### In-process registration

For an engine that is imported rather than configured (and for tests):

```python
from app.services import detector

detector.register_inference(
    "audio", run_audio, model_name="rahul-audio", model_version="0.9.0"
)
```

Registration replaces any entrypoint for that modality. Nothing else changes.

## 3. What comes back, and where it goes

`analyse()` always returns a `DetectorResult`. Its `to_dict()` is stored verbatim
as the `detector` analysis stage, which means every field below reaches the
Forensic Analysis and Multimodal AI Analysis pages (`GET
/api/evidence/{id}/analysis`) and the PDF report:

| field | notes |
| --- | --- |
| `media_type` | which modality actually ran |
| `manipulation_score` | `null` or 0..1. Also mirrored as `score` |
| `confidence` | the model's own, or `null`. Never derived from the score |
| `abstained` | `true` whenever no score was produced |
| `model`, `model_version` | model identity, as reported by the model or the sidecar |
| `weights_hash` | SHA-256 of the model file (cached on size+mtime), or `""` |
| `latency_ms` | measured by PRAMAAN around the call. Also `inference_ms` |
| `explanation` | shown in the analysis UI |
| `heatmap_available`, `regions`, `timestamps` | rendered where the model provides them |
| `status` | `OK` \| `UNAVAILABLE` \| `ERROR` \| `UNSUPPORTED_MEDIA` |
| `detail` | why, when there is no score |
| `interface_version` | `"2.0"` |
| `extras` | anything else the model returned |

The score reaches the verdict through the `ai_detection` signal of the fusion
engine (declared weight 0.35, renormalised over the signals that were actually
available). Fusion, not the detector, decides `AUTHENTIC` / `MANIPULATED` /
`INSUFFICIENT_EVIDENCE`; the detector never assigns a verdict, because
duplicating the thresholds would let it disagree with the case verdict.

## 4. Abstention is a first-class answer

This is the part that must not be "improved":

* No model installed → `manipulation_score: null`, `abstained: true`, status
  `UNAVAILABLE`, and the `ai_detection` signal is **excluded** from fusion.
* `score=None` from an installed model → the same treatment. "I cannot say" is
  reported as a missing signal, not as 0.5.
* A model that raises → status `ERROR`, score `null`. Every exception a model
  runtime can throw is caught; a broken model never becomes a 500.
* An unusable `confidence` is dropped to `null` on its own, without discarding an
  otherwise valid score.

A missing detection is **not** evidence of authenticity and **not** evidence of
manipulation. Excluded ≠ zero. If enough signal weight is missing, fusion returns
`INSUFFICIENT_EVIDENCE`, which is a statement about the analysis and not about the
media.

Never substitute a placeholder score to make a page look populated.

## 5. Verifying an installation

```bash
# 1. Is it loaded, and if not, exactly which socket failed?
curl -s localhost:8000/api/detector/status | python -m json.tool

# 2. Run it against one file, per modality.
curl -s -F file=@sample.jpg  localhost:8000/api/detect
curl -s -F file=@sample.mp4  localhost:8000/api/detect
curl -s -F file=@sample.wav  localhost:8000/api/detect

# 3. Run it against registered evidence (this one is audited).
curl -s -F evidence_id=<id> localhost:8000/api/detect

# 4. Confirm it reaches the verdict.
curl -s -X POST localhost:8000/api/cases/<case_id>/analyse | python -m json.tool
```

Expect, in order: `available: true` with the model name and version; a non-null
`manipulation_score` with a `latency_ms` and a `weights_hash`; and an
`ai_detection` signal with `included: true` in the fused verdict's signal table.

`GET /api/detector/status` names the failing socket precisely — a model file
configured with no inference code, an entrypoint that will not import, an
unreadable file — so a failed install does not need to be diagnosed from logs.

## 6. Checklist for the model author

Per modality (image, video, audio), supply:

1. The model file, or the package that contains the weights.
2. The inference callable as `module:callable`, honouring section 2.
3. `model_name` and `model_version` — via the sidecar, `register_inference`, or
   the returned mapping. Version strings end up in the PDF report and the audit
   chain, so they need to identify a specific set of weights.
4. Any preprocessing the callable does not do itself, as a sidecar spec.
5. The intended input range: resolution, duration, sample rate, codecs. PRAMAAN
   will hand over whatever was ingested; a model that cannot handle an input
   should return `score=None` with an explanation rather than a guess.
6. Optional but valuable: `explanation`, `regions`, `timestamps`,
   `heatmap_available` — the analysis UI already renders them.
7. Known error rates, if any exist. The absence of a measured error rate is
   itself reported to the examiner rather than hidden.

Nothing else in PRAMAAN needs to change: ingestion, hashing, perceptual
retrieval, provenance, forensics, fusion, propagation, reporting and the audit
chain all keep working exactly as they do with the detector absent — the only
difference is that the `ai_detection` signal starts contributing.
