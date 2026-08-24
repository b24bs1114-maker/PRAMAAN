# PRAMAAN — Exact Render & Vercel Production Deployment Guide

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

## 1. ⚙️ Vercel Settings (Frontend)

- **Repository Root Directory**: `frontend`
- **Framework Preset**: `Vite`
- **Build Command**: `npm run build`
- **Output Directory**: `dist`
- **SPA Routing Manifest**: `frontend/vercel.json`
  ```json
  {
    "framework": "vite",
    "buildCommand": "npm run build",
    "outputDirectory": "dist",
    "rewrites": [
      { "source": "/(.*)", "destination": "/index.html" }
    ]
  }
  ```

### Vercel Environment Variables:
| Variable Name | Example Value | Purpose |
|---|---|---|
| `VITE_API_URL` | `https://pramaan-backend.onrender.com` | Backend origin for all API requests |

---

## 2. ⚙️ Render Settings (Backend)

- **Service Type**: `Web Service`
- **Environment**: `Python 3` (`PYTHON_VERSION = 3.12.13`)
- **Root Directory**: `./` (repository root)
- **Build Command**: `bash scripts/render_build.sh`
- **Start Command**: `PYTHONPATH=backend gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:$PORT`
- **Health Check Path**: `/health`

### Render Environment Variables:
| Variable Name | Value | Purpose |
|---|---|---|
| `PYTHON_VERSION` | `3.12.13` | Enforce Python 3.12 runtime |
| `PRAMAAN_ENVIRONMENT` | `production` | Production mode flag |
| `PRAMAAN_HOST` | `0.0.0.0` | Bind host |
| `PRAMAAN_CORS_ALLOW_ORIGINS` | `https://*.vercel.app,http://localhost:5173,*` | CORS whitelist for Vercel origin |
| `PRAMAAN_CORS_ALLOW_METHODS` | `*` | Allowed HTTP methods |
| `PRAMAAN_CORS_ALLOW_HEADERS` | `*` | Allowed HTTP headers |
| `PRAMAAN_IMAGE_MODEL_PATH` | `pramaan-detector/weights/image_detector.pt` | Path to Swin-B image weights |
| `PRAMAAN_AUDIO_MODEL_PATH` | `pramaan-detector/weights/audio_detector.pt` | Path to Wav2Vec2 audio weights |
| `PRAMAAN_IMAGE_WEIGHTS_URL` | *(Optional)* Direct download URL | Auto-download image weights during build |
| `PRAMAAN_AUDIO_WEIGHTS_URL` | *(Optional)* Direct download URL | Auto-download audio weights during build |

---

## 3. 📦 Model Weight Deployment Strategy for Render

To provide the 1.5 GB PyTorch model weights (`image_detector.pt` 347 MB, `audio_detector.pt` 1.26 GB) **without committing large binary files directly into Git history**:

### Zero-Cost Recommended Method:
1. Upload `image_detector.pt` and `audio_detector.pt` to **HuggingFace Hub**, **GitHub Releases**, or a **Cloudflare R2** public bucket.
2. In the Render Dashboard under **Environment Variables**, set:
   - `PRAMAAN_IMAGE_WEIGHTS_URL` = `https://huggingface.co/your-username/pramaan-weights/resolve/main/image_detector.pt`
   - `PRAMAAN_AUDIO_WEIGHTS_URL` = `https://huggingface.co/your-username/pramaan-weights/resolve/main/audio_detector.pt`
3. The Render build step (`bash scripts/render_build.sh`) invokes `scripts/download_weights.py`, which checks if `pramaan-detector/weights/` already has the weights. If missing, it downloads them via HTTP before `gunicorn` starts.

---

## 4. ⚠️ Free-Tier Memory Limitations Assessment

- **Render Free Tier Limit**: 512 MB RAM.
- **Model Memory Requirement**: Loading PyTorch Swin-B (~347 MB) and Wav2Vec2 (~1.26 GB) simultaneously into RAM requires ~1.5 GB RAM.
- **Recommendation**:
  - For full real PyTorch inference on Render, use a **Starter / Standard Instance** (1 GB – 2 GB RAM).
  - If deploying on the **512 MB Free Tier**, set `PRAMAAN_DETECTOR_BACKEND=null` in Render environment variables. PRAMAAN will run in fallback abstention mode (`status: UNAVAILABLE`) with zero crashes, full audit integrity, perceptual hashing, metadata analysis, and 3-page PDF report generation.

---

## 5. 🚀 Step-by-Step Deployment Instructions

### Step 1: Push Code to GitHub
```bash
git add .
git commit -m "Deploy PRAMAAN to Render and Vercel"
git push origin main
```

### Step 2: Deploy Backend to Render
1. Log in to [Render Dashboard](https://dashboard.render.com/) and click **New +** → **Blueprint** (or **Web Service**).
2. Select your GitHub repository.
3. Render reads `render.yaml` automatically and configures the build script and environment variables.
4. Click **Apply** / **Create Web Service**.
5. Copy your Render service URL (e.g. `https://pramaan-backend.onrender.com`).

### Step 3: Deploy Frontend to Vercel
1. Log in to [Vercel Dashboard](https://vercel.com/) and click **Add New...** → **Project**.
2. Select your GitHub repository.
3. Set **Root Directory** to `frontend`.
4. Under **Environment Variables**, add:
   - `VITE_API_URL` = `https://pramaan-backend.onrender.com`
5. Click **Deploy**.

---

## 🧪 Verification Commands

Run locally before pushing:

```bash
# Frontend build & contract verification
cd frontend
npm run build
npm run verify:contract

# Backend test suite
cd ..
./.venv/bin/pytest backend/tests -v
```
