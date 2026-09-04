# Audio detector: what "ready" means, and what it does not mean

The audio socket runs `garystafford/wav2vec2-deepfake-voice-detector`
(`Wav2Vec2ForSequenceClassification`, Apache-2.0) from the local checkpoint
`pramaan-detector/weights/audio_detector.pt`. This document records the readiness
contract enforced in code, because "a file exists" was previously enough for the
status endpoint to advertise a working detector that then failed on every
request.

Authoritative definitions live in code and are quoted, not paraphrased, below:
`pramaan.detectors.audio_detector.READINESS_CONTRACT`, `POSITIVE_INDEX`,
`PEAK_MEMORY_BYTES`, and `checkpoint_readiness()`.

## Ready requires three things, not one

`checkpoint_readiness()` returns `(True, None)` only when all three hold. It
never loads tensors — status is polled by the dashboard, and loading costs
~1.67 GB.

1. **The checkpoint is present and is this architecture.** Its `data.pkl` member
   must declare `wav2vec2.`, `projector.` and `classifier.` parameters. This is
   an allowlist: a checkpoint for some other model cannot pass by not being on a
   denylist. (The video socket's equivalent check was a denylist, and
   `checkpoint_readiness("audio_detector.pt")` returned `True` for the *video*
   detector because the wav2vec2 file happens to contain the string
   `classifier.`.)
2. **The architecture `config.json` is reachable.** Only the config is ever
   fetched — never weights; the tensors come from the verified checkpoint on
   disk. With `PRAMAAN_DETECTOR_OFFLINE` set, transformers may not reach the hub,
   so the config must already be in the local Hugging Face cache. If it is not,
   readiness reports that state instead of advertising a detector that abstains
   on every request. `backend/Dockerfile` bakes the config into the image when
   the build provisions audio.
3. **The config's `id2label` agrees with `POSITIVE_INDEX`.** Upstream declares
   `{0: "real", 1: "fake"}`; `weights/audio_detector.pt.json` declares
   `positive_index: 1`; the module reports `probs[1]`. `verify_label_direction()`
   compares them before any tensor is read and **raises** on a config that
   contradicts the assumed direction, because reading the wrong index inverts
   every verdict — a clone reported as authentic speech and authentic speech
   reported as a clone — with nothing in the output to show it happened.

## Readiness is not accuracy

**This detector has not been empirically validated in PRAMAAN.** What has been
checked is that the checkpoint loads key-for-key, that the score is read from the
class the config labels synthetic, and that the pipeline abstains when it cannot
load. No accuracy, error rate, ROC or operating threshold has been measured on
ground-truth speech in this configuration, so the score is a model output, not a
calibrated probability, and the verdict thresholds are demonstration defaults.

Sine-wave and synthetic-tone fixtures in the test suite exercise the *plumbing*
— resampling, windowing, abstention, label direction. They are not speech, and
passing them is not evidence about deepfake voices.

Known domain limits: the model targets human speech and voice cloning. Music,
ambient audio and non-speech material are outside its training domain. Indian-
language speech, telephony codecs, and generators absent from its training set
are unmeasured here.

## Memory decides whether audio can run at all

`PEAK_MEMORY_BYTES = 1_670_000_000` — ~1.67 GB peak RSS to build and hold the
model, measured on this checkpoint. A 512 MB instance cannot run it. On such an
instance leave `PRAMAAN_AUDIO_MODEL_PATH` unset: audio then reports
`INSUFFICIENT_EVIDENCE`, which is honest, instead of being OOM-killed mid-request.
Enabling audio means an instance with ≥ 2 GB and a build with
`PRAMAAN_WEIGHTS_MODALITIES=image,audio`.

## The five states you can see, and what each means

`INSUFFICIENT_EVIDENCE` from an unavailable detector is **not** a finding of
authenticity and **not** a finding of manipulation. `null` is not `0.0`, and the
signal is excluded from fusion rather than counted as zero.

| State | `available` | `score` | Meaning |
|---|---|---|---|
| Not installed | `false` | `null` | No checkpoint configured or present. |
| Installed, unusable | `false` | `null` | Checkpoint present but wrong architecture, or config unreachable offline. |
| Disabled | `false` | `null` | The socket was deliberately not configured for this deployment. |
| Ran, abstained | `true` | `null` | Model loaded; this file yielded no scoreable audio (too short, no decodable stream). |
| Ran, scored | `true` | `0.0–1.0` | A model output for this file. Not a probability of guilt. |

## Checking it yourself

```bash
python scripts/verify_model_assets.py --modality audio
```

Verifies presence, size and SHA-256 against `model_manifest.json`, then builds
the detector and fails unless it is genuinely usable. Add `--no-load` to check
the bytes only. `GET /api/detector/status` reports the same readiness at runtime,
per modality, with the reason when unavailable.
