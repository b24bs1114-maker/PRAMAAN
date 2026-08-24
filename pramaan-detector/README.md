# PRAMAAN Detector

A multi-modal AI forensic detector that tells you whether an **image**, **video**, or **audio**
file is real or AI-generated / manipulated.

It plugs into the PRAMAAN backend as a single `POST /detect` service.
It does **not** handle C2PA, metadata, pHash, FAISS, provenance, propagation, or the final
verdict — those are separate backend signals.

---

## What it detects

| Modality | What it looks for |
|----------|-------------------|
| Image    | AI-generated images, face swaps, GAN/diffusion artifacts, lighting/texture anomalies, blending artifacts |
| Video    | Deepfake video, frame-level artifacts, temporal inconsistencies, facial anomalies, lip-sync mismatch |
| Audio    | AI-generated voice, voice cloning, synthetic speech, abnormal spectral/prosodic patterns |

---

## Requirements

Before you start, make sure you have:

- **Python 3.9 or newer** — check with `python --version`
- **pip** — comes with Python
- **Git** — to clone the repo
- A terminal / command prompt

> You do NOT need a GPU. Everything runs on CPU for the prototype.

---

## Step 1 — Get the code

Open a terminal and run:

```bash
git clone https://github.com/your-org/pramaan-detector.git
cd pramaan-detector
```

If you downloaded a ZIP instead, unzip it and open a terminal inside the `pramaan-detector` folder.

---

## Step 2 — Create a virtual environment

This keeps the project packages separate from the rest of your system.

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Mac / Linux:**
```bash
python -m venv venv
source venv/bin/activate
```

You should see `(venv)` at the start of your terminal prompt. That means it is active.

---

## Step 3 — Install the package

### Option A — Install everything (recommended)

```bash
pip install -e ".[all]"
```

This installs:
- PyTorch + TorchVision (image and video models)
- Transformers + librosa + soundfile (audio model)
- OpenCV (video frame extraction)
- scikit-learn (evaluation metrics)
- ONNX (model export)
- pytest (tests)

### Option B — Install only what you need

```bash
# Just image detection
pip install -e "."

# Image + audio
pip install -e ".[audio]"

# Image + video
pip install -e ".[video]"

# Everything except export
pip install -e ".[audio,video,eval]"
```

> The first install downloads PyTorch and model weights (~500 MB+). This is normal.

---

## Step 4 — Verify the install

Run the tests to make sure everything is working:

```bash
pytest tests/ -v
```

All tests should pass. If any fail, make sure your virtual environment is active and all
packages installed correctly.

---

## Step 5 — Run your first detection

You can detect any image, video, or audio file right away — no training needed.
The models use pretrained weights out of the box.

### Detect an image

```bash
python scripts/detect.py photo.jpg
```

### Detect a video

```bash
python scripts/detect.py clip.mp4
```

### Detect an audio file

```bash
python scripts/detect.py voice.wav
```

### Get JSON output (for backend integration)

```bash
python scripts/detect.py photo.jpg --json
```

Example output:

```json
{
  "media_type": "image",
  "label": "MANIPULATED",
  "manipulation_score": 0.87,
  "confidence": 0.74,
  "abstained": false,
  "model": "ImageForensicNet-EfficientNetB4",
  "model_version": "1.0.0",
  "weights_hash": "pretrained-imagenet-only",
  "latency_ms": 312.4,
  "explanation": "Image classified as AI-generated or manipulated (score=0.870). Suspicious regions detected at 2 location(s).",
  "evidence": {},
  "heatmap_available": true,
  "regions": [],
  "timestamps": []
}
```

> If the score is close to 0.5, the detector returns `INSUFFICIENT_EVIDENCE` instead of
> guessing. This is intentional — the system never forces a confident classification.

---

## Step 6 — Train on your own data (optional but recommended)

Pretrained-only models give rough results. For accurate detection, fine-tune on deepfake datasets.

### Prepare your data

Create a CSV file for each split (train / val / test) with no header:

```
/path/to/real_image.jpg,0
/path/to/fake_image.jpg,1
```

`0` = authentic, `1` = manipulated.

Put your CSV files in the `data/` folder:

```
data/image_train.csv
data/image_val.csv
data/video_train.csv
data/video_val.csv
data/audio_train.csv
data/audio_val.csv
```

### Train the image detector

```bash
python scripts/train.py image \
  --train data/image_train.csv \
  --val   data/image_val.csv \
  --output weights/image_detector.pt \
  --epochs 10 \
  --device cpu
```

Use `--device cuda` if you have a GPU.

### Train the video detector

```bash
python scripts/train.py video \
  --train data/video_train.csv \
  --val   data/video_val.csv \
  --output weights/video_detector.pt \
  --epochs 10
```

### Train the audio detector

```bash
python scripts/train.py audio \
  --train data/audio_train.csv \
  --val   data/audio_val.csv \
  --output weights/audio_detector.pt \
  --epochs 10
```

### Run detection with your trained weights

```bash
python scripts/detect.py photo.jpg \
  --image-weights weights/image_detector.pt

python scripts/detect.py clip.mp4 \
  --video-weights weights/video_detector.pt

python scripts/detect.py voice.wav \
  --audio-weights weights/audio_detector.pt
```

---

## Step 7 — Evaluate your model

After training, measure how well it performs on a test set:

```bash
python scripts/benchmark.py data/image_test.csv \
  --image-weights weights/image_detector.pt \
  --output results.json
```

You can mix modalities in one CSV and the benchmark will split metrics automatically:

```bash
python scripts/benchmark.py data/all_test.csv \
  --image-weights weights/image_detector.pt \
  --audio-weights weights/audio_detector.pt \
  --output results.json
```

Metrics reported per modality:

```
accuracy, AUC, EER, precision, recall, F1,
confusion matrix, abstention rate, latency_ms (mean + p95)
```

Results are saved to `results.json`.

---

## Step 8 — Export for deployment (optional)

Once trained, export to a portable format for the backend.

### ONNX

```bash
python scripts/export_onnx.py image weights/image_detector.pt weights/image.onnx
python scripts/export_onnx.py audio weights/audio_detector.pt weights/audio.onnx
```

### TorchScript

```bash
python scripts/export_onnx.py image weights/image_detector.pt weights/image_ts.pt --format torchscript
python scripts/export_onnx.py audio weights/audio_detector.pt weights/audio_ts.pt --format torchscript
```

---

## Using it as a Python library

```python
from pramaan.service import DetectorService

svc = DetectorService(
    image_weights="weights/image_detector.pt",  # optional
    video_weights="weights/video_detector.pt",  # optional
    audio_weights="weights/audio_detector.pt",  # optional
    device="cpu",
)

result = svc.detect("photo.jpg")   # auto-routes by file extension
print(result.label)                # AUTHENTIC / MANIPULATED / INSUFFICIENT_EVIDENCE
print(result.manipulation_score)   # 0.0 to 1.0  (None if abstained)
print(result.to_dict())            # full JSON-serialisable dict
```

---

## Architecture

### Image Detector
- Backbone: EfficientNet-B4 (ImageNet pretrained, fine-tuned for forgery detection)
- Explainability: Grad-CAM on last conv block → suspicious region bounding boxes
- Augmentations: RandomResizedCrop, HorizontalFlip, ColorJitter, JPEG recompression (q 50–95)
- Input: JPG, PNG, WEBP at 380×380

### Video Detector
- Architecture: frame sampling → ImageForensicNet per frame → temporal consistency analysis → aggregation
- Face counting per frame via OpenCV Haar cascade
- Exposes: video score, per-frame scores, temporal inconsistency score, suspicious timestamps, faces detected
- Augmentations: random frame sampling, JPEG recompression per frame, ColorJitter
- Input: MP4, MOV

### Audio Detector
- Backbone: facebook/wav2vec2-base (fine-tuned for anti-spoofing); falls back to lightweight CNN if transformers unavailable
- Windowed inference: 3-second chunks with mean aggregation
- Exposes: manipulation score, suspicious time ranges, spectrogram evidence (mean dB, std dB, spectral flatness)
- Augmentations: Gaussian noise, resampling artefact (8/11/22 kHz), amplitude scaling
- Input: WAV, MP3, M4A, AAC

### Output contract (all modalities)

```json
{
  "media_type": "image | video | audio",
  "label": "AUTHENTIC | MANIPULATED | INSUFFICIENT_EVIDENCE",
  "manipulation_score": 0.0,
  "confidence": 0.0,
  "abstained": false,
  "model": "...",
  "model_version": "...",
  "weights_hash": "...",
  "latency_ms": 0,
  "explanation": "...",
  "evidence": {},
  "heatmap_available": false,
  "regions": [],
  "timestamps": []
}
```

Abstention fires when `|score - 0.5| < 0.15`. The system never forces a confident classification.

---

## Recommended datasets for training

| Modality | Dataset |
|----------|---------|
| Image    | [FaceForensics++](https://github.com/ondyari/FaceForensics), [DFDC](https://ai.facebook.com/datasets/dfdc/), [CIFAKE](https://www.kaggle.com/datasets/birdy654/cifake-real-and-ai-generated-synthetic-images) |
| Video    | [FaceForensics++](https://github.com/ondyari/FaceForensics), [Celeb-DF](https://github.com/yuezunli/celeb-deepfakeforensics) |
| Audio    | [ASVspoof 2019](https://datashare.ed.ac.uk/handle/10283/3336), [WaveFake](https://github.com/RUB-SysSec/WaveFake) |

---

## Project structure

```
pramaan-detector/
├── pramaan/
│   ├── __init__.py
│   ├── schema.py              # shared DetectionResult contract + abstention logic
│   ├── service.py             # DetectorService — routes by file extension
│   ├── detectors/
│   │   ├── image_detector.py  # EfficientNet-B4 + Grad-CAM + region extraction
│   │   ├── video_detector.py  # frame sampling + temporal analysis + face count
│   │   └── audio_detector.py  # Wav2Vec2 + windowed inference + spectrogram evidence
│   ├── training/
│   │   ├── image_trainer.py   # image fine-tuning with JPEG recompression augmentation
│   │   ├── video_trainer.py   # video frame-based fine-tuning
│   │   └── audio_trainer.py   # audio fine-tuning with noise/resampling augmentations
│   ├── evaluation/
│   │   ├── metrics.py         # accuracy, AUC, EER, F1, confusion matrix, abstention rate
│   │   └── benchmark.py       # per-modality benchmark runner with latency stats
│   └── export/
│       └── onnx_export.py     # ONNX + TorchScript export for image and audio models
├── scripts/
│   ├── detect.py              # CLI: detect a single file
│   ├── train.py               # CLI: train image / video / audio
│   ├── benchmark.py           # CLI: evaluate on a labelled CSV
│   └── export_onnx.py         # CLI: export to ONNX or TorchScript
├── tests/
│   └── test_pramaan.py        # full unit test suite (schema, routing, detectors, metrics, benchmark)
├── conftest.py                # makes repo root importable for pytest
├── data/
│   └── README.txt             # where to put training CSVs
├── weights/
│   └── README.txt             # where trained weights are saved
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'pramaan'`**
Make sure your virtual environment is active and you ran `pip install -e ".[all]"` from inside
the `pramaan-detector` folder.

**`RuntimeError: Neither librosa nor soundfile is installed`**
```bash
pip install librosa soundfile
```

**`RuntimeError: Neither cv2 nor decord is installed`**
```bash
pip install opencv-python
```

**Model always returns `INSUFFICIENT_EVIDENCE`**
This is expected with pretrained-only weights. The model is uncertain because it has not been
trained on deepfake data yet. Fine-tune it on a labelled dataset first (Step 6).

**Slow inference on CPU**
CPU inference is supported but slow for video (many frames). Use `--device cuda` if you have
a GPU, or reduce `MAX_FRAMES` in `pramaan/detectors/video_detector.py`.

**`pytest` cannot find the `pramaan` module**
Make sure `conftest.py` exists in the project root. It adds the root to `sys.path` automatically.
