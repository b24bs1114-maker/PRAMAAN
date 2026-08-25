# PRAMAAN — Render & Vercel Production Deployment Guide

This document contains the step-by-step production deployment instructions, environment variables, build commands, and model weight provisioning strategies for hosting PRAMAAN on **Render** (Backend) and **Vercel** (Frontend).

---

## 🏗️ Architecture & Component Mapping

```
                               ┌───────────────────────────┐
                               │     Vercel Frontend       │
                               │  https://<app>.vercel.app │
                               └─────────────┬─────────────┘
                                             │ HTTP API Calls (JSON / Multipart)
                                             ▼
                               ┌───────────────────────────┐
                               │      Render Backend       │
                               │ https://<api>.onrender.com│
                               └─────────────┬─────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
          ┌──────────────────────────┐               ┌──────────────────────────┐
          │  SQLite Database & Index │               │   pramaan-detector/      │
          │   backend/data/pramaan.db│               │ weights/*.pt PyTorch     │
          └──────────────────────────┘               └──────────────────────────┘
```

---

## 1. ⚙️ Image Model Weight Provisioning via `PRAMAAN_IMAGE_WEIGHTS_URL`

To provide the real Swin-B PyTorch model weights (`image_detector.pt` ~347 MB) to Render **without committing binary files directly into Git**:

1. **Upload `image_detector.pt`** to a public direct download URL (e.g., GitHub Releases, HuggingFace Hub, Google Drive direct link, or Cloudflare R2).
2. **Configure Environment Variable in Render**:
   - In Render Dashboard $\rightarrow$ Environment Variables (or `render.yaml`), set:
     ```env
     PRAMAAN_IMAGE_WEIGHTS_URL = https://your-direct-link.com/image_detector.pt
     ```
3. **Docker Build Provisioning**:
   - During Docker image compilation on Render, `backend/Dockerfile` executes `scripts/download_weights.py`.
   - If `/app/pramaan-detector/weights/image_detector.pt` already exists (e.g. local build), it skips downloading.
   - If missing, it downloads `image_detector.pt` directly into `/app/pramaan-detector/weights/image_detector.pt` and verifies the file SHA-256 hash.
   - Runtime path remains **EXACTLY**:
     `/app/pramaan-detector/weights/image_detector.pt`

### Official Baseline Model Hash:
```
SHA-256: 25edbe34eaa7168366e2c98c49e09c98ca1afd4ca4be0d21d6b84f2b9a24b83f
```

---

## 2. ⚙️ Render Settings (Backend)

- **Service Type**: `Web Service`
- **Environment**: `Docker`
- **Dockerfile Path**: `backend/Dockerfile`
- **Root Directory**: *(blank / repository root)*
- **Health Check Path**: `/health`

### Render Environment Variables:
| Variable Name | Value | Purpose |
|---|---|---|
| `PRAMAAN_ENVIRONMENT` | `production` | Production mode flag |
| `PRAMAAN_HOST` | `0.0.0.0` | Bind host |
| `PRAMAAN_CORS_ALLOW_ORIGINS` | `https://*.vercel.app,http://localhost:5173,*` | CORS whitelist for Vercel origin |
| `PRAMAAN_IMAGE_MODEL_PATH` | `/app/pramaan-detector/weights/image_detector.pt` | Path to Swin-B image weights |
| `PRAMAAN_VIDEO_MODEL_PATH` | `/app/pramaan-detector/weights/image_detector.pt` | Path to Swin-B frame weights |
| `PRAMAAN_IMAGE_WEIGHTS_URL` | `https://.../image_detector.pt` | Direct download URL for image model weights |

---

## 3. ⚙️ Vercel Settings (Frontend)

- **Repository Root Directory**: `frontend`
- **Framework Preset**: `Vite`
- **Build Command**: `npm run build`
- **Output Directory**: `dist`
- **Environment Variable**: `VITE_API_URL = https://pramaan-backend.onrender.com`

---

## 4. 🧪 Verifying Live Detector Status

Once Render finishes building and deploying, verify detector status via HTTP:

```bash
curl https://pramaan-backend.onrender.com/api/detector/status
```

Expected JSON response:
```json
{
  "available": true,
  "model": "SwinB-AI-Image-Detector",
  "modalities": {
    "image": { "available": true, "model": "SwinB-AI-Image-Detector" },
    "video": { "available": true, "model": "SwinB-AI-Image-Detector" },
    "audio": { "available": false, "reason": "No audio detector is installed in this deployment" }
  }
}
```
