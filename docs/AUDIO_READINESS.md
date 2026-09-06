# Audio detector: what "ready" means, and what it does not mean

The audio socket runs **AASIST** (`SpeechAntiSpoofingBenchmarks/AASIST`: Graph
Attention Network with a Sinc-Conv RawNet2 encoder) from the local checkpoint
`pramaan-detector/weights/audio_detector.pth` (~1.3 MB). This document records
the readiness contract enforced in code, because "a file exists" was previously
enough for the status endpoint to advertise a working detector that then
failed on every request.

Authoritative definitions live in code and are quoted, not paraphrased, below:
`pramaan.detectors.audio_detector.READINESS_CONTRACT`, `POSITIVE_INDEX`,
`PEAK_MEMORY_BYTES`, and `checkpoint_readiness()`.

> **Superseded model.** An earlier build ran
> `garystafford/wav2vec2-deepfake-voice-detector` (~1.26 GB) here. That model
> is gone; nothing in the current tree loads it, and every "wav2vec2" size or
> memory figure you may still find in old notes refers to it.

## Ready requires the checkpoint, not a name

`READINESS_CONTRACT` (audio_detector.py):

> ready = AASIST checkpoint present with RawNet2/GAT parameters,
> and label mapping consistent with positive_index=0 (spoof/fake).

`checkpoint_readiness()` returns `(True, None)` only when the checkpoint file
resolves and loads with AASIST's architecture (`strict=True` state-dict load).
It never invents a score when the file is absent:

> "Audio checkpoint file not found at ... This is NOT a finding of
> authenticity and NOT a finding of manipulation -- the signal is missing and
> is excluded from fusion."

`PEAK_MEMORY_BYTES = 25_000_000` (~25 MB) is the measured peak resident cost of
the model; it is small enough to provision on any instance that can hold the
image detector.

## Label direction: class 0 = spoof/fake (inverted vs image and video)

AASIST upstream declares `{0: "spoof", 1: "bonafide"}`, so
`POSITIVE_INDEX = 0` and `spoof_prob = probs[0, 0]` — the manipulation score.
This is the opposite direction of the image (1 = manipulated) and video
(1 = fake) sockets. `verify_label_direction()` enforces it before any tensor is
read and **raises** on a config that contradicts the assumed direction, because
reading the wrong index inverts every verdict — spoofed speech reported as
bonafide and vice versa — with nothing in the output to show it happened.

Consistency of `positive_index` across the manifest
(`weights/model_manifest.json`), the sidecar (`weights/audio_detector.pth.json`)
and the module constant is regression-tested in
`pramaan-detector/tests/test_model_metadata_consistency.py`.

## When it is not ready

With no checkpoint the detector abstains with `score=None` and
`NO_TRAINED_MODEL_EXPLANATION`:

> "No trained AASIST audio detector could be loaded, so no voice-manipulation
> score was produced. ... This is NOT a finding of authenticity and NOT a
> finding of manipulation -- the signal is missing and is excluded from
> fusion."

The backend excludes the audio signal from fusion and renormalises over the
signals that did run; nothing downstream reports "authentic" because audio was
silent.
