# PRAMAAN

**Offline-first forensic evidence analysis platform.**

PRAMAAN ingests digital media, fingerprints it, reconstructs how it spread, and
produces a forensic report — entirely on-premise, with no external service calls.
Every conclusion is traceable: signals are fused transparently, each analysis step
is recorded in a hash-chained audit log, and the exported PDF cites the evidence
behind every claim.

Design constraints that shape the whole system:

- **Offline / on-premise first** — no cloud dependency, no outbound network calls
  at runtime.
- **SQLite** as the datastore (not PostgreSQL) — a single portable case file.
- **Flat (exhaustive) perceptual index** for exact, explainable nearest-neighbour
  retrieval — FAISS `IndexBinaryFlat` when installed, an equivalent numpy
  XOR+popcount search otherwise. Both are exact; only speed differs.
- **Perceptual hashing** (pHash / dHash / aHash) for near-duplicate and re-encode
  detection.
- **SHA-256** for exact evidence integrity hashing.
- **Transparent fusion** — signal weights are inspectable, never a black box, and
  a signal that cannot be measured is *excluded*, never scored as zero.
- **Hash-chained audit log** — tamper-evident record of every operation.

## Status

Backend complete. **350 tests pass** (`pytest`, 3.12 venv).

Implemented end to end:

| Capability | Where |
| --- | --- |
| Evidence ingestion, content sniffing, quarantine-safe storage | `app/services/ingestion.py`, `storage.py` |
| SHA-256 integrity + pHash / dHash / aHash | `app/services/hashing.py` |
| EXIF / ISO-BMFF metadata extraction | `app/services/metadata.py` |
| Perceptual index (flat, exact) + live ingestion | `app/services/index.py`, `indexing.py` |
| Near-duplicate retrieval with verified Hamming distances | `app/services/matching.py` |
| Propagation reconstruction + earliest known instance | `app/services/propagation.py` |
| Multi-modal AI-manipulation detector interface (image, video, audio; abstains when absent) | `app/services/detector.py`, [docs/DETECTOR_PLUGIN.md](docs/DETECTOR_PLUGIN.md) |
| C2PA provenance inspection | `app/services/provenance.py` |
| Compression forensics | `app/services/forensics.py` |
| Transparent weighted fusion | `app/services/fusion.py` |
| Hash-chained audit log + chain verification | `app/services/audit.py` |
| Forensic PDF report | `app/services/report.py`, `app/utils/pdf.py` |
| Container deployment | `backend/Dockerfile`, `backend/docker-compose.yml` |

Not implemented: user accounts/authentication, multi-tenancy, and a calibrated
detector model (see [Fallbacks and limitations](#fallbacks-and-limitations)).

## Repository layout

```
PRAMAAN/
├── backend/
│   ├── app/
│   │   ├── main.py            app factory, health probe, error envelope, CORS
│   │   ├── config.py          environment-driven settings + logging setup
│   │   ├── api/               cases, evidence, analysis, index, detector,
│   │   │                      reports, dashboard, alerts, audit, system routers
│   │   ├── services/          ingestion, hashing, metadata, index, matching,
│   │   │                      propagation, detector, provenance, forensics,
│   │   │                      fusion, audit, report, pipeline, storage
│   │   ├── models/            SQLAlchemy models (Case, Evidence, Match, …)
│   │   ├── schemas/           Pydantic API contracts
│   │   └── utils/             canonical JSON, PDF writer, time helpers
│   ├── tests/                 pytest suite (26 modules + helpers.py)
│   ├── data/                  SQLite DB, evidence store, index (git-ignored)
│   ├── reports/               generated PDF reports (git-ignored)
│   ├── Dockerfile             offline container image
│   ├── docker-compose.yml     single-service deployment
│   └── requirements.txt
├── corpus/                    synthetic corpus + manifest.json (generated)
├── frontend/                  React client (owned separately from the backend)
├── docs/
│   └── DETECTOR_PLUGIN.md     the detector contract: how a model is plugged in
├── scripts/
│   ├── generate_corpus.py     build the synthetic corpus
│   ├── build_index.py         ingest the corpus and rebuild the index
│   ├── verify_integration.py  replay the API and record the contract
│   └── test_phash.py          perceptual-hash robustness report
├── reports/                   exported forensic reports
├── .env.example               configuration template
└── README.md
```

## Requirements

- **Python 3.12** — required. Developed and tested on 3.12.13. Do **not** use the
  macOS system Python 3.9 (too old) or Python 3.14 (several optional forensic
  dependencies have no wheels for it).
- No database server, message broker or cloud account is needed.

```bash
python3.12 --version
```

## Setup

From the repository root:

```bash
python3.12 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install --upgrade pip
```

```bash
pip install -r backend/requirements.txt
```

Optionally create a local configuration file (all values have working defaults):

```bash
cp .env.example .env
```

Windows (PowerShell) activation:

```powershell
.venv\Scripts\Activate.ps1
```

## Running the API

Run from `backend/` so `app.main` resolves:

```bash
cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Add `--reload` while developing. With the venv activated, `uvicorn app.main:app
--host 127.0.0.1 --port 8000` is equivalent.

| URL | Purpose |
| --- | --- |
| http://127.0.0.1:8000/health | health probe — returns exactly `{"status": "ok"}` |
| http://127.0.0.1:8000/ | service name, version, environment |
| http://127.0.0.1:8000/docs | interactive OpenAPI docs (non-production only) |

```bash
curl -s http://127.0.0.1:8000/health
```

Stop with `Ctrl+C`. The database, evidence store and index are created on first
start; no migration step is needed.

## Running tests

From `backend/`:

```bash
cd backend && ../.venv/bin/python -m pytest
```

Every run gets a throwaway data directory, so tests never touch a real case
database. The end-to-end module drives all fifteen workflow steps plus the six
failure paths:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_end_to_end.py -v
```

The API contract the frontend is built against is recorded by replaying the real
app and writing every response to `reports/integration-recordings.json`:

```bash
cd backend && ../.venv/bin/python ../scripts/verify_integration.py
```

The frontend then replays those recordings — no running backend needed:

```bash
cd frontend && npm run verify:contract
```

Regenerate the recordings whenever a response shape changes, so a contract drift
fails a check instead of a page.

## Corpus and index bootstrap

The synthetic corpus is what near-duplicate retrieval searches against. It is
generated locally and deterministically — it is **synthetic demo data, not
real-world evidence**.

```bash
.venv/bin/python scripts/generate_corpus.py
```

```bash
.venv/bin/python scripts/build_index.py
```

```bash
.venv/bin/python scripts/test_phash.py
```

## API

The API is the whole contract: it is JSON over HTTP, needs no session state, and
is independent of any frontend. Multipart form fields are used for uploads,
query parameters for options, and a JSON body only where noted.

### Cases and evidence

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/cases/upload` | multipart: `file` (required), `case_id`, `title`, `description`, `examiner` as **form fields**. Omit `case_id` to create a case. `201` for new evidence, `200` when the SHA-256 is already in the case (`duplicate: true`). |
| `GET` | `/api/cases` | `limit` (default 100), `offset` |
| `GET` | `/api/cases/{case_id}` | case + evidence count |
| `PATCH` | `/api/cases/{case_id}` | form fields: `title`, `description`, `examiner`, `case_status` |
| `DELETE` | `/api/cases/{case_id}` | deletes the case, its evidence files and its analyses; audit rows are retained |
| `GET` | `/api/cases/{case_id}/evidence` | evidence list with hashes and dimensions |
| `GET` | `/api/cases/library/all` | evidence across every case; `case_id`, `media_type`, `limit`, `offset` |
| `GET` | `/api/evidence/{evidence_id}` | one item: record, file state, which analysis stages exist. `verify=true` re-hashes the stored bytes and audits the check; without it `integrity_verified` is `null` (not checked), never `false` |
| `GET` | `/api/evidence/{evidence_id}/file` | the stored bytes, unchanged, with `X-PRAMAAN-Evidence-SHA256`. `download=true` switches the disposition to `attachment` |
| `GET` | `/api/evidence/{evidence_id}/analysis` | stored analysis stages, verdict and signals. Reads only — runs nothing |

### Analysis

Every `POST` here computes and stores; the matching `GET` reads back what was
stored and computes nothing. An item with no stored result is reported as
*pending*, which is not the same as an inconclusive one.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/cases/{case_id}/metadata` | `refresh` |
| `POST` | `/api/cases/{case_id}/matches` | `top_k`, `max_distance` |
| `GET` | `/api/cases/{case_id}/matches` | stored candidates. `searched: false` means the search never ran — distinct from a search that found nothing |
| `GET` | `/api/cases/{case_id}/provenance` | C2PA state per item, and what this deployment can actually validate; `refresh` |
| `GET` | `/api/cases/{case_id}/propagation` | `refresh` |
| `POST` | `/api/cases/{case_id}/detect` | `refresh` |
| `POST` | `/api/cases/{case_id}/verdict` | `refresh` |
| `GET` | `/api/cases/{case_id}/verdict` | stored verdicts, plus `pending_evidence` for items not yet fused |
| `POST` | `/api/cases/{case_id}/analyse` | `refresh`, `audit_limit` — one call for the whole pipeline |
| `GET` | `/api/cases/{case_id}/audit` | `limit` |
| `POST` | `/api/cases/{case_id}/audit/verify` | `record` (default true) — set `false` for a read-only check |

### Index

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/index/status` | `indexed_count`, `last_updated`, `index_version`, backend |
| `POST` | `/api/index/rebuild` | rebuild from the database (the index is derived state) |
| `POST` | `/api/index/add/{evidence_id}` | index one existing item |
| `POST` | `/api/index/ingest` | multipart: `file` plus optional `source_id`, `parent_id`, `generation`, `platform`, `observed_at`, `transformation`, `is_synthetic`. Stores, hashes, indexes and makes the file **immediately searchable** — this is the live judge-file handover path. |

### Detector and reports

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/detector/status` | adapter, model identity and availability per modality, and which socket failed if unavailable |
| `POST` | `/api/detect` | multipart: `file` **or** `evidence_id`, plus optional `media_type` (`image`/`video`/`audio`). The route a plugged-in model is exercised through. With no model installed it returns the abstention (`manipulation_score: null`, `abstained: true`) as a `200` — the question was answered truthfully |
| `POST` | `/api/cases/{case_id}/report` | JSON body `{"examiner": "…"}`, query `refresh`; returns the PDF's SHA-256, page count and audit head hash |
| `GET` | `/api/cases/{case_id}/reports` | reports generated for the case |
| `GET` | `/api/cases/{case_id}/reports/{report_id}` | download the PDF |
| `GET` | `/api/reports` | reports across every case; `case_id`, `limit`, `offset`. Rows are identical to the per-case rows and nothing is re-rendered |

### Dashboard, alerts, audit and system

Read-only views built for the operator-facing pages. Every figure is a database
aggregate or a service's own capability report — there are no synthetic numbers,
and an empty deployment returns an honest empty state.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/dashboard/summary` | case/evidence/verdict/alert counts, recent activity, capability state |
| `GET` | `/api/alerts` | derived at read time from stored analysis — there is no alerts table. `severity`, `category`, `case_id`, `limit`, `offset`. Counts are computed before filtering, so they do not shift as the client pages |
| `GET` | `/api/audit` | the chain across every case; `case_id`, `event`, `actor`, `since`, `until`, `limit`, `offset`. An unparseable bound is a `400`, never silently dropped |
| `POST` | `/api/audit/verify` | recompute the whole chain; `record` (default true), `include_events` |
| `GET` | `/api/system/status` | configuration, capabilities, real row counts, the detector contract and the published vocabularies. Read-only: weights and model paths are deployment configuration, not client state. Reading it does **not** verify the audit chain |

### Service

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/health` | exactly `{"status": "ok"}` |
| `GET` | `/` | name, version, environment, docs URL |
| `GET` | `/openapi.json`, `/docs`, `/redoc` | non-production only |

### Contract notes for the frontend

- CORS is preconfigured for `http://localhost:5173` (Vite) and
  `http://localhost:3000` (CRA / Next.js), on both `localhost` and `127.0.0.1`.
  Add origins via `PRAMAAN_CORS_ALLOW_ORIGINS` (comma-separated).
- Every response carries an `X-Request-ID` header, exposed to the browser. Send
  it back on retries and quote it in bug reports to match a call to its logs.
- Errors use one envelope, and never contain stack traces:

  ```json
  {
    "error": { "type": "http_error", "message": "Not Found" },
    "request_id": "9f2c41ab7d05"
  }
  ```

  `type` is one of `http_error`, `validation_error`, `internal_server_error`.
  Validation errors add `error.details` with the failing field locations.
- Analysis endpoints are idempotent and cached: they return the stored result
  unless `refresh=true`. Cached payloads carry `cached: true`.
- Every payload that can be misread carries an `interpretation` or `caveat`
  string. Render them — they are part of the result, not decoration.
- A measurement that could not be made is `null` with a `status` of
  `UNAVAILABLE` / `INCONCLUSIVE` / `UNSUPPORTED` and `included: false`. Never
  substitute `0` for `null` in the UI: zero means "no indication of
  manipulation", `null` means "not measured".
- **Never-run is a third state.** `GET .../matches` reports `searched: false`,
  `GET .../verdict` lists `pending_evidence`, and evidence integrity is
  `integrity_verified: null` until `verify=true` is passed. None of these are
  "nothing found" and none are failures — render them as *not yet run*.
- Every vocabulary the UI needs to label a value — verdicts, signal statuses,
  provenance states, match bands, audit events, the origin wording — is published
  at `GET /api/system/status` under `vocabularies`. Read it rather than hardcoding
  labels, and never invent one.
- The origin of a file is reported as the **earliest known instance in the indexed
  evidence corpus**. That exact wording is the claim the backend can support; do
  not shorten it to "original" or "source" in the UI.

### Worked example

```bash
curl -s -F file=@photo.jpg -F examiner="A. Examiner" http://127.0.0.1:8000/api/cases/upload
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/cases/$CASE_ID/analyse
```

```bash
curl -s -X POST -H 'Content-Type: application/json' -d '{"examiner":"A. Examiner"}' http://127.0.0.1:8000/api/cases/$CASE_ID/report
```

```bash
curl -s -F file=@handed-over-in-court.jpg -F platform=live-handover http://127.0.0.1:8000/api/index/ingest
```

## Docker

Single service, no PostgreSQL, no Redis, no orchestrator. All mutable state lives
on volumes, so a container can be destroyed and recreated without losing
evidence, the database, the index or the reports.

From `backend/`:

```bash
cd backend && docker compose up --build
```

```bash
cd backend && docker compose ps
```

```bash
cd backend && docker compose run --rm backend pytest -q
```

```bash
cd backend && docker compose down
```

- The API is published on `127.0.0.1:8000` only — it handles evidence and must
  not be exposed to a LAN by accident.
- `/data` is a named volume (`pramaan-data`): SQLite database, evidence store and
  perceptual index. `docker compose down` keeps it; `down -v` destroys it.
- `./reports` and `../corpus` are bind mounts, so the examiner can read generated
  PDFs and drop corpus images in from the host. On Linux, set `PRAMAAN_UID` /
  `PRAMAAN_GID` to your own `id -u` / `id -g` if those directories are owned by
  another user.
- The image runs as non-root (uid 1000), installs no apt packages, and its
  health check uses only the Python standard library.
- Air-gapped build: populate a wheelhouse, add `COPY wheels /wheels` above the
  install step, and build with
  `--build-arg PIP_EXTRA_ARGS="--no-index --find-links=/wheels"`.

**Not validated here:** Docker is not installed in the environment this backend
was developed in, so the image has never been built or run. Everything checkable
without a daemon is enforced by `tests/test_docker.py` (31 tests) — including
executing the health-check programs extracted from `Dockerfile` and
`docker-compose.yml` against the real app — but `docker compose up --build` is a
manual step still to be performed on a host with Docker.

## Configuration

Settings are read from environment variables (or a `.env` file at the project
root or in `backend/`), all prefixed `PRAMAAN_`. See [.env.example](.env.example)
for the full list with defaults. Highlights:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRAMAAN_ENVIRONMENT` | `development` | `development` / `testing` / `production` |
| `PRAMAAN_HOST` / `PRAMAAN_PORT` | `127.0.0.1` / `8000` | bind address |
| `PRAMAAN_LOG_LEVEL` | `INFO` | console log verbosity |
| `PRAMAAN_DATA_DIR` | `backend/data` | SQLite DB, evidence, index |
| `PRAMAAN_REPORTS_DIR` | `backend/reports` | generated PDF reports |
| `PRAMAAN_CORPUS_DIR` | `corpus` | synthetic evidence corpus |
| `PRAMAAN_CORS_ALLOW_ORIGINS` | localhost `5173`, `3000` | allowed frontend origins |
| `PRAMAAN_MAX_UPLOAD_BYTES` | `67108864` (64 MB) | upload ceiling |
| `PRAMAAN_NEAR_DUPLICATE_MAX_DISTANCE` | `12` | retrieval cut-off, in bits |
| `PRAMAAN_STRONG_DUPLICATE_MAX_DISTANCE` | `6` | strong-candidate band |
| `PRAMAAN_DETECTOR_BACKEND` | `auto` | `auto` (use a local detector if one is installed, else abstain) / `null` (always abstain) |
| `PRAMAAN_DETECTOR_MODEL_PATH` | unset | image-model fallback; unset means the detector abstains |
| `PRAMAAN_IMAGE_MODEL_PATH` / `_VIDEO_` / `_AUDIO_` | unset | per-modality model file |
| `PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT` / `_VIDEO_` / `_AUDIO_` | unset | per-modality inference code as `module:callable` |

Interactive API docs are served automatically except when
`PRAMAAN_ENVIRONMENT=production`.

## Plugging in a detector

The detector is a replaceable component: **no code in `app/services/` changes
when a model arrives.** There are two sockets — a model file
(`PRAMAAN_*_MODEL_PATH`, plus an optional JSON sidecar for preprocessing) and
inference code (`PRAMAAN_*_DETECTOR_ENTRYPOINT` as `module:callable`, or an
in-process `detector.register_inference(...)` call).

[docs/DETECTOR_PLUGIN.md](docs/DETECTOR_PLUGIN.md) is the full contract: the
callable signature, the accepted return shapes, what each result field means, how
the score reaches fusion, and how to verify an installation. The live contract is
also served at `GET /api/system/status` under `detector_contract`, generated from
the code rather than from the document.

With every socket empty, the `ai_detection` signal is reported `UNAVAILABLE` and
excluded from fusion. Every other stage — ingestion, hashing, perceptual
retrieval, provenance, forensics, propagation, reporting, audit — works exactly as
it does with a model installed.

## Fallbacks and limitations

Six optional dependencies cannot be installed in the offline environment this
backend was built in. Each has a tested in-tree fallback, and the API always
reports which path actually served a request — it never implies the optional
library is present.

| Missing | Fallback in use | Consequence |
| --- | --- | --- |
| `faiss-cpu` | numpy XOR + popcount flat search | Identical results (both exhaustive), slower on a large corpus. `GET /api/index/status` reports `backend` and `faiss_available`. |
| `reportlab` | `app/utils/pdf.py`, a minimal PDF 1.4 writer | Valid PDF with base-14 fonts; plainer typography, same content. The report reports its `renderer`. |
| `imagehash` | native pHash (DCT-II) / dHash / aHash | Self-contained implementations, verified against known-answer tests. |
| `c2pa-python` | provenance reported as `UNAVAILABLE` | No manifest can be parsed or verified. Absence of a manifest is the expected condition for almost all media and is never scored as manipulation. |
| `onnxruntime` / `torch` | `NullDetector`, which abstains | No AI-detection score. The signal is reported `UNAVAILABLE` with `score: null` and **excluded** from fusion; the declared weight stays visible so a reader can see what is missing. |

Honest statements of scope, all enforced by tests:

- **Thresholds and weights are uncalibrated prototype defaults.** They have not
  been validated against a forensic reference dataset and no error rate is known
  for them. Every verdict carries that caveat.
- **The corpus is synthetic.** It is generated locally by
  `scripts/generate_corpus.py` and must not be presented as real-world evidence.
- **"Origin" means earliest known instance in the local index** — not the
  absolute real-world origin, first publisher or creator. Earlier copies may
  exist outside the corpus, and embedded or platform timestamps can be wrong or
  deliberately altered.
- **Near-duplicate candidates are candidates.** A low Hamming distance is
  consistent with resizing, recompression, cropping or screenshotting; it does
  not establish derivation or a shared real-world origin.
- **Missing metadata is not evidence of manipulation.** Platforms strip EXIF
  routinely; absence is the expected state for shared media.
- **The audit log is tamper *evidence*, not tamper proof.** Anyone with write
  access to the database can recompute the whole chain; it becomes meaningful
  once the head hash is anchored outside the database, which is why the generated
  report prints it.
- **No live socket in the development sandbox.** Tests drive the app through the
  ASGI transport rather than a bound port, so `uvicorn` startup on a real port —
  like `docker compose up` — is a manual verification step.

## Logging

Logs go to stdout as `timestamp | level | logger | message`. Each request logs
method, path, status, duration and request id. **Query strings, headers and
request bodies are never logged**, so credentials and case identifiers cannot
leak into log files; secrets added later must be declared as `SecretStr`.
