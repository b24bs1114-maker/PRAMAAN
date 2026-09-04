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
                               │  (Docker, non-root uvicorn)│
                               └─────────────┬─────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
          ┌──────────────────────────┐               ┌──────────────────────────┐
          │  PERSISTENT DISK  /data  │               │  IMAGE LAYER (ephemeral) │
          │  pramaan.db  (cases,     │               │ /app/pramaan-detector/   │
          │    evidence rows, audit  │               │   weights/*.pt  Swin-B   │
          │    chain)                │               │ /corpus  synthetic demo  │
          │  evidence/  stored bytes │               │   index (regenerable)    │
          │  index/     pHash index  │               └──────────────────────────┘
          │  reports/   generated PDF│
          └──────────────────────────┘
```

Everything on the left must survive a restart and a redeploy; everything on the
right is rebuilt from the image. That split is why the blueprint declares a disk —
see §5.

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

`render.yaml` in the repository root is the blueprint and declares all of this;
the list below is what it sets, for a service configured by hand in the dashboard.

- **Service Type**: `Web Service`
- **Runtime**: `Docker` (`runtime: docker` — the `env: python` key is deprecated)
- **Dockerfile Path**: `backend/Dockerfile`
- **Docker Build Context**: `.` *(repository root, not `backend/`)*
- **Root Directory**: *(blank / repository root)*
- **Health Check Path**: `/health`
- **Instance Type**: `1c-2g` — see §5 for why nothing smaller runs the detector,
  and why nothing on the free tier can persist evidence
- **Disk**: `pramaan-evidence`, mounted at `/data`, 5 GB
- **Build / Start Command**: *(none — the Dockerfile declares both)*

There is no start command because the Dockerfile has one. The previous blueprint
ran `gunicorn -k uvicorn.workers.UvicornWorker`, and `gunicorn` is not in
`backend/requirements.txt`; the image serves with `uvicorn`, which is.

### Render Environment Variables:
Render passes each of these as a Docker **build argument** as well as a runtime
variable, which is how the weights settings below reach the build.

| Variable Name | Value | Purpose |
|---|---|---|
| `PRAMAAN_DATA_DIR` | `/data` | Case DB, evidence store, hash index, audit chain — on the disk |
| `PRAMAAN_REPORTS_DIR` | `/data/reports` | Generated PDFs; a service may attach only one disk |
| `PRAMAAN_WEIGHTS_MODALITIES` | `image` | Which checkpoints the build provisions (`none` for no AI) |
| `PRAMAAN_CONVERT_SAFETENSORS` | `1` | Build the memory-mapped copy: 396 MB resident instead of 1052 MB |
| `PRAMAAN_FAIL_ON_MISSING_WEIGHTS` | `0` | A failed fetch degrades to UNAVAILABLE instead of a red deploy |
| `PRAMAAN_ENABLE_AI_DETECTOR` | `true` | Master toggle; `false` on an instance too small to load a model |
| `PRAMAAN_TORCH_THREADS` | `1` | Read from the process env, not `app/config.py`; unpinned, torch starves `/health` |
| `PRAMAAN_CORS_ALLOW_ORIGINS` | *(exact frontend origins, comma-separated)* | CORS allow-list |
| `PRAMAAN_IMAGE_WEIGHTS_URL` | `https://.../image_detector.pt` | Optional: a mirror or signed URL instead of the manifest's release asset |

Do not put `*` in `PRAMAAN_CORS_ALLOW_ORIGINS`. List the deployed frontend
origins exactly, as `render.yaml` does.

The model paths, the three detector entrypoints, `PRAMAAN_DETECTOR_OFFLINE=true`,
`PRAMAAN_ENVIRONMENT=production` and `PYTHONPATH` are **baked into the
Dockerfile** and are deliberately not repeated in the blueprint — two places to
set one path is two places for them to disagree.

`PRAMAAN_VIDEO_MODEL_PATH` is **deliberately left unset**, and must stay that way.
No video checkpoint has been published for PRAMAAN (see the `video` entry in
`pramaan-detector/weights/model_manifest.json`). Pointing it at
`image_detector.pt` loads nothing — those are Swin-B image-classifier parameters
and the video frame model is EfficientNet-B0 — while making
`/api/detector/status` advertise a video deepfake detector that then abstains on
every request. Unset, the video modality reports `available: false` with the
reason stated, and every video analysis returns `INSUFFICIENT_EVIDENCE` with
`score: null`.

`PRAMAAN_AUDIO_MODEL_PATH` is set in the Dockerfile but is only *satisfied* when
the build provisioned the audio checkpoint. It is not provisioned on the declared
`1c-2g` profile: building and holding the wav2vec2 model peaks at ~1.67 GB RSS
(measured, `PEAK_MEMORY_BYTES` in `pramaan/detectors/audio_detector.py`), and 2 GB
does not hold that *plus* the resident image model. Audio therefore reports
`INSUFFICIENT_EVIDENCE` instead of being OOM-killed mid-request.

To enable audio, use `2c-4g` (2 CPU / 4 GB) and build with
`PRAMAAN_WEIGHTS_MODALITIES=image,audio`, so the checkpoint *and* its cached
architecture config are baked into the image — the runtime is offline and cannot
fetch the config itself. On a 2 GB instance you may run audio *instead of* image
(`PRAMAAN_WEIGHTS_MODALITIES=audio`), not alongside it.

Audio direction is declared, not empirically validated: see
`docs/AUDIO_READINESS.md` and the `validation_status` field in the manifest.

---

## 3. ⚙️ Vercel Settings (Frontend)

- **Repository Root Directory**: `frontend`
- **Framework Preset**: `Vite`
- **Build Command**: `npm run build`
- **Output Directory**: `dist`
- **Environment Variable**: `VITE_API_URL = https://pramaan-6oph.onrender.com`

That hostname is whatever Render assigned the service, **not** the `name:` in
`render.yaml` — Render appends a suffix when a name is already taken, which is
why this is `pramaan-6oph` and not `pramaan-backend`. Two places must agree with
it: this variable, and the rewrite in `frontend/vercel.json`, which maps
`/api/backend/*` to the same origin. Read the real hostname off the Render
dashboard after the first deploy and set both.

`VITE_API_URL` is not optional in the sense of being decorative. Left unset, the
frontend falls back to the same-origin path `/api/backend`, which on Vercel
follows that rewrite to the deployed backend anyway — so an unset variable does
not keep a preview deployment local, it silently sends it to production. The
browser console logs which of the two was used on every load.

---

## 4. 🧪 Verifying Live Detector Status

Once Render finishes building and deploying, verify detector status via HTTP.
The host is the one Render assigned — the same origin `frontend/vercel.json`
rewrites to, and **not** the `name:` in `render.yaml` (see §3):

```bash
curl https://pramaan-6oph.onrender.com/api/detector/status
```

Expected JSON response on the default single-modality deployment (image only):
```json
{
  "available": true,
  "model": "SwinB-AI-Image-Detector",
  "modalities": {
    "image": { "available": true, "model": "SwinB-AI-Image-Detector" },
    "video": { "available": false, "model": "none", "weights_hash": "",
               "reason": "No trained video detector is installed, so no video manipulation score was produced..." },
    "audio": { "available": false, "model": "none",
               "reason": "No audio detector is installed in this deployment" }
  }
}
```

`video.model` is `"none"` and `video.weights_hash` is `""` by design: a socket
that cannot score a frame must not report another model's name or another
checkpoint's digest. If you ever see `"video": { "available": true }` here, or
see it naming `SwinB-AI-Image-Detector`, the deployment has
`PRAMAAN_VIDEO_MODEL_PATH` pointed at a checkpoint that is not a video
checkpoint — unset it.

---

## 5. 💾 Persistent Storage and the Instance-Type Decision

Two platform facts constrain everything above, and neither can be worked around
in a config file:

**A persistent disk requires a paid instance type.** Render does not offer disks
on the free instance. Without one the container filesystem is ephemeral, so on
every restart and every deploy the service loses the SQLite case file, the stored
evidence bytes, the perceptual-hash index, the generated reports **and the
append-only audit chain**. The chain still verifies after that — it verifies an
empty chain. Evidence that does not survive a restart is not evidence, so
`render.yaml` declares a disk and therefore a paid plan.

**512 MB cannot hold either detector.** Measured, and recorded in
`pramaan-detector/weights/model_manifest.json`:

| Model | Peak RSS | Smallest plan that can run it |
|---|---|---|
| Swin-B image, memory-mapped safetensors | 396 MB + web process | `1c-2g` |
| Swin-B image, plain `.pt` load | 1052 MB + web process | `1c-2g` |
| Grad-CAM heatmap (on top of the above) | +206 MB | `1c-2g` |
| wav2vec2 audio | ~1.67 GB | `2c-4g` (or `1c-2g` with image off) |

### The three honest profiles

| | `free` | `0.5c-512mb` | `1c-2g` *(declared)* |
|---|---|---|---|
| Evidence survives restart | **no** | yes (disk) | yes (disk) |
| Hash, pHash retrieval, metadata, C2PA, JPEG/ELA | yes | yes | yes |
| AI image score | no | no | **yes** |
| AI audio score | no | no | no (needs `2c-4g`) |
| AI video score | no | no | no — none exists |
| Cost | none | paid | paid |

Switching profile is an explicit, minimal edit — `plan`,
`PRAMAAN_ENABLE_AI_DETECTOR` and `PRAMAAN_WEIGHTS_MODALITIES` — and the exact
lines are commented at the top of `render.yaml`.

Turning the AI off does **not** make results wrong or silently weaker: the
`ai_detection` signal reports `UNAVAILABLE` with its reason, it is excluded from
fusion rather than counted as zero, and fusion renormalises over the signals that
did run. What it costs you is the signal, stated as missing.

---

## 6. 🔐 How the Mounted Disk Becomes Writable

The container starts as root, prepares the mount, and then drops privileges for
good — `backend/docker-entrypoint.py`, wired as the image `ENTRYPOINT`:

1. `chown` each of `PRAMAAN_DATA_DIR`, `PRAMAAN_REPORTS_DIR` and
   `PRAMAAN_CORPUS_DIR` to the unprivileged `pramaan` account, recursing only when
   the directory's own owner is wrong (a first boot, or a volume last written by a
   root container). An already-correct store is not walked on every restart.
2. `setgroups` / `setgid` / `setuid` to `pramaan`. On Linux with no capabilities
   retained this is irreversible, which is the point.
3. `execvp` the CMD, so uvicorn inherits PID 1 and receives Render's `SIGTERM`
   directly.

A freshly mounted disk belongs to root and the mount does not exist at build time,
so there is no `chown` a Dockerfile could do instead. Running the server as root
would trade the entire non-root posture for a `mkdir`. `docker run --user 1000`
still works: the entrypoint sees it is already unprivileged, skips the `chown`, and
warns if the storage is unwritable rather than failing on the first upload.

### Post-deploy verification

```bash
BASE=https://pramaan-6oph.onrender.com
curl -s $BASE/health
curl -s $BASE/api/detector/status | head -40
curl -s -X POST $BASE/api/audit/verify
```

Then confirm persistence, which is the whole reason for the disk — upload a file,
note its `case_id`, restart the service from the Render dashboard, and re-request
it:

```bash
curl -s $BASE/api/cases/<case_id>
curl -s "$BASE/api/audit?limit=5"
curl -s -X POST $BASE/api/audit/verify
```

The case, its evidence and the audit events must still be there, and `/verify`
must still report the chain intact — each event's hash is
`SHA-256(previous_hash || canonical_json(payload))`, so a chain that survived a
restart verifies across the restart boundary. If the case is gone, the service has
no disk attached, whatever the dashboard's plan says.
