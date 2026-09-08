# PRAMAAN Detector Model Inventory & Integrity Specification

This document provides the definitive specification of all official multi-modal forensic detector models integrated into the PRAMAAN platform, their provenance, exact cryptographic digests, input requirements, class indices, and verification procedures.

---

## 1. Official Model Inventory

| Modality | Architecture / Model | Hugging Face Source | Local Checkpoint Path | Format | Size (Bytes) | Exact SHA-256 Digest | Head / Classes | License |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Image** | **OwensLab-CommunityForensics-ViT384** (`vit_small_patch16_384.augreg_in21k_ft_in1k` + `Linear(384, 1)`) | [`OwensLab/commfor-model-384`](https://huggingface.co/OwensLab/commfor-model-384) | `pramaan-detector/weights/image_detector.safetensors` | SafeTensors | 87,262,324 | `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387` | Output logit -> Sigmoid; Index `1` = Manipulated / Synthetic | MIT |
| **Audio** | **AASIST** (Audio Anti-Spoofing using Integrated Spectro-Temporal Graph Attention Networks) | [`SpeechAntiSpoofingBenchmarks/AASIST`](https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST) | `pramaan-detector/weights/audio_detector.pth` | PyTorch State Dict (`.pth`) | 1,281,532 | `51d2d9cf0738172f61e2a384ec50a54a55363240f67c971ed55a92435bc1a1c0` | Softmax `2` classes; Index `0` = Manipulated (Spoof), Index `1` = Authentic | MIT |
| **Video** | **VideoMAE-DeepFake-Detector** (`VideoMAEForVideoClassification` 16-frame spatiotemporal tubelet transformer) | [`Vansh180/VideoMae-ffc23-deepfake-detector`](https://huggingface.co/Vansh180/VideoMae-ffc23-deepfake-detector) | `pramaan-detector/weights/video_detector.safetensors` | SafeTensors | 344,937,328 | `293c668a5d3289d3162902d8cac6687cd11551cf2358768bad126de32b6d7f29` | Softmax `2` classes; Index `0` = Authentic, Index `1` = Manipulated (Deepfake) | MIT |

---

## 2. Model Preprocessing & Forensic Scoring Details

### Image Forensics (`image_detector.safetensors`)
- **Input Channels / Format:** RGB.
- **Resolution Pipeline:** Resize to $440 \times 440$, Center Crop to $384 \times 384$.
- **Normalization:** ImageNet mean (`[0.485, 0.456, 0.406]`), std (`[0.229, 0.224, 0.225]`).
- **Inference Pipeline:** Evaluates Vision Transformer patch representations. Applies `torch.sigmoid(head_output)`.
- **Positive Class:** Index `1` (Manipulated / AI-Generated). Higher score ($\to 1.0$) indicates synthetic image artifacts (Diffusion, GAN, autoregressive models).

### Audio Forensics (`audio_detector.pth`)
- **Input Sample Rate:** 16,000 Hz single channel (mono).
- **Window Size:** 64,600 samples (~4.0375 seconds); shorter samples are tiled/padded to 64,600 samples; minimum duration is 0.25 seconds.
- **Architecture:** Sinc-convolution frontend + graph attention network (GAT).
- **Inference Pipeline:** Log-softmax / Softmax output over 2 classes.
- **Positive Class:** **Index `0`** is Spoof / Synthetic / Cloned Voice (ASVspoof convention); **Index `1`** is Authentic Speech. Score exported to forensic pipeline is probability at index `0`.

### Video Forensics (`video_detector.safetensors`)
- **Input Sampling:** 16 uniformly sampled frames across the video duration.
- **Resolution Pipeline:** Each frame resized to $224 \times 224$ RGB.
- **Normalization:** ImageNet mean (`[0.485, 0.456, 0.406]`), std (`[0.229, 0.224, 0.225]`).
- **Tensor Shape:** `(1, 3, 16, 224, 224)` (spatiotemporal tubelet volume).
- **Inference Pipeline:** `VideoMAEForVideoClassification` head with 2 classes.
- **Positive Class:** Index `1` is Manipulated (Deepfake / Face-swap).

---

## 3. Storage Architecture: Git LFS & Fallback Bootstrap

Model weights are tracked in Git using **Git LFS (Large File Storage)**. This prevents git bloat while allowing exact, deterministic tracking of binary model weights alongside code.

### Git LFS Tracking Rules (`.gitattributes`)
```gitattributes
pramaan-detector/weights/*.safetensors filter=lfs diff=lfs merge=lfs -text
pramaan-detector/weights/*.pth filter=lfs diff=lfs merge=lfs -text
pramaan-detector/weights/*.pt filter=lfs diff=lfs merge=lfs -text
```

### Deterministic Bootstrap Script (`scripts/download_weights.py`)
In addition to Git LFS, the repository provides automated bootstrap tooling with cryptographic verification:
1. **GitHub Release Assets:** Downloads from the configured repository release tag (`PRAMAAN_WEIGHTS_RELEASE_REPO`, `PRAMAAN_WEIGHTS_RELEASE_TAG`).
2. **Direct Hugging Face Fallback:** Automatically fetches the exact pinned file from Hugging Face Hub if no release asset is accessible.
3. **Atomic Writes:** Downloads to temporary `.part` files and computes SHA-256 before moving into place.
4. **Strict Digest Enforcement:** Rejects any checkpoint whose byte size or SHA-256 digest fails to match `pramaan-detector/weights/model_manifest.json`.

---

## 4. Setup & Verification Instructions for a Fresh Clone

### Option A: Using Git LFS
If cloning a fresh copy of the repository with Git LFS installed:
```bash
git lfs install
git clone https://github.com/b24bs1114-maker/PRAMAAN.git
cd PRAMAAN
git lfs pull
```

### Option B: Using the Bootstrap Script
If Git LFS was not installed prior to cloning or if pulling pointers only:
```bash
# Provision all models with strict SHA-256 validation
python scripts/download_weights.py --all --strict

# Or verify existing local checkpoints without downloading
python scripts/download_weights.py --verify-only --all
```

### Option C: Standalone Forensic Asset Verification
Verify that all checkpoints exist, match their manifests, and load properly into their PyTorch/SafeTensors architectures:
```bash
python scripts/verify_model_assets.py
```

### Executing Model Test Suites
Run the dedicated live inference and smoke tests:
```bash
# Smoke test real models
pytest backend/tests/test_real_models_smoke.py -v

# Live multi-modal model pipeline test
pytest backend/tests/test_live_multimodal_models_pipeline.py -v

# pramaan-detector unit test suite
pytest pramaan-detector/tests/ -v
```
