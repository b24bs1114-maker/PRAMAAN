# PRAMAAN — War Room
_Last updated: 2026-09-04 · Stage: build (backend done, frontend redesign mid-migration) · Time remaining: **UNKNOWN — confirm event + deadline**_

## 1. Hackathon facts
- Event: **[ASSUMED] MOSIP Decode 2026** — only criteria doc present is `~/Desktop/Evaluation_Criteria_MOSIP_Decode_2026.pdf`. **Not confirmed that PRAMAAN is entered in this event.**
- [VERIFIED, web] MOSIP Decode 2026: IIIT-Bangalore, global virtual, students; registration closes **14 Sep 2026**; judged **November**; US$5,000 pool + MOSIP internships.
- [VERIFIED, web] Its four published problem statements are conformance testing, VC interoperability sandbox, native mobile SSO, **face liveness detection** — none is media provenance/deepfake forensics. **PRAMAAN's fit is unclear → resolve first.**
- [VERIFIED, repo] Round 1 was submitted ~24 Aug 2026 (`~/Desktop/PRAMAAN_DEPLOY/PRAMAAN_BACKEND_ROUND1.zip`, commit "Initial PRAMAAN Round-1 source").
- Separate event on Desktop, **not** PRAMAAN: SARCathon 2026 Agentic AI — Round 1 idea (2 pages) due **7 Sep 2026**, finale Oct. Different project entirely.

## 2. Judging & rules — [VERIFIED from the criteria PDF]
| Weight | Criterion | What it actually measures |
|---|---|---|
| **40%** | Functionality Completion | objectives met, functional requirements fulfilled, effectiveness |
| **40%** | Quality of Deliverable | code quality · documentation · scalability · reusability · maintainability |
| 10% | User Experience & Design | intuitive, aesthetic, navigable, inclusive, accessible |
| 10% | Presentation & Communication | problem/solution/impact clarity, storytelling, value prop |
- Multiplier: problem statements tiered High/Medium/Low complexity; higher complexity → increased weighting for depth + innovation.
- **Implication: 80% of the score is code + completeness, not UI.** Polish is capped at 10%.
- Submission requirements, format, deadline: **UNKNOWN — get these.**

## 3. Problem & insight
- Problem: a shared photo/video/audio clip can't be traced — who posted it first, was it manipulated, has it been re-encoded, and can any of it be defended in court.
- Core insight: **an examiner-grade tool earns trust by refusing to guess.** A signal that cannot be measured is *excluded*, never scored zero; "never run" is a third state distinct from "found nothing"; every verdict carries its own caveat; the audit chain is hash-linked so the report is checkable.
- Differentiator: forensic honesty + full traceability, offline/on-premise, no cloud call.

## 4. What is DONE — [VERIFIED by running it today]
- **Backend, complete**: 14,169 LOC app + 8,738 LOC tests, `pytest` exit 0 (11 skips). Ingest → SHA-256 + pHash/dHash/aHash → EXIF/ISO-BMFF → flat exact perceptual index → near-duplicate matching → propagation + earliest known instance → C2PA provenance → compression forensics → transparent weighted fusion → hash-chained audit → PDF report. ~50 endpoints.
- **Detector engine**: 2,264 LOC, **57/57 tests**. Real weights, digest-verified: image Swin-B 347 MB, audio Wav2Vec2-large 1.26 GB.
- **API contract**: recorded from the live app and replayed by the frontend — **72/72 checks pass**.
- **Deployed**: Render backend (`pramaan-6oph.onrender.com`) + Vercel frontend, CORS + `/api/backend/*` proxy wired.
- **Committed frontend console works**: 10 screens, 12,733 LOC — this is what is live today.

## 5. What is NOT done — the real gaps
| # | Gap | Rubric hit | Status |
|---|---|---|---|
| 1 | **Redesign is dark code.** 9,725 LOC (9 components + 4 libs + 6 CSS files, all untracked) import only each other. No screen, no `App.tsx`, no `main.tsx` references them. | 10% UX + drags 40% quality | blocker |
| 2 | **Working tree does not build.** 16 TS errors — `IconName` lacks 14 glyphs (`fingerprint`, `copy`, `audio`, `zoom-in/out`, `chevron-left/right`, `target`, `flag`, `sitemap`, `evidence`, `layers`, `activity`) + `Media.tsx` imports a non-existent `mediaIcon`. `npm run build` runs `tsc -b` → **cannot deploy**. | 40% functionality | blocker |
| 3 | **`main.tsx` loads `global.css` (old, 3,520 lines), never `styles/index.css` (new, 5,748 lines).** Of 532 classes used in TSX: 291 exist only in the new CSS (unstyled if used), 159 only in `global.css` (break on a naive swap), **40 defined nowhere** (print/report view + propagation graph — broken today). | 40% quality + 10% UX | blocker |
| 4 | **Production AI detector is OFF** — `PRAMAAN_ENABLE_AI_DETECTOR=false` in `render.yaml` (Render free tier RAM). A judge on the live URL sees the flagship deepfake signal abstain. | 40% functionality | high |
| 5 | 4 orphan modules never imported by anything: `Artefact.tsx`, `Async.tsx`, `Overlays.tsx`, `lib/caseflow.ts`. | 40% quality | medium |
| 6 | No `video_detector.pt` → video honestly UNAVAILABLE. Honest, but a visible feature hole. | 40% functionality | medium |
| 7 | No measured accuracy anywhere. `scripts/test_phash.py` exists but its report is not published. Thresholds/weights uncalibrated. | 40% functionality | medium |
| 8 | Fusion weights disagree between the workflow spec (visual 30/credentials 25/perceptual 20/metadata 15/compression 10) and the implementation (ai_detection .35/perceptual .20/metadata .20/provenance .15/forensics .10). Unresolved product decision. | 40% quality | medium |
| 9 | Docker image has never been built or run (no daemon in the dev env). README says so honestly. | 40% quality | low |
| 10 | README claims 350 tests; actual count has grown. Docs drift. | 40% quality | low |
| 11 | No demo runbook, no designated recorded-backup demo (4 screen recordings exist on Desktop, unlabelled). | 10% presentation | medium |
| 12 | Deck `~/Desktop/PRAMAAN_Hackathon_Final.pdf` is from 25 Aug — predates everything since. | 10% presentation | medium |

## 6. Current constraint
**The working tree does not compile.** Nothing ships, and 9,725 lines of finished design work score zero, until the icon set is closed and the stylesheet entry point is switched. Fix that first; everything else queues behind it.

## 7. Next moves — in order
1. **Close the icon set** (~1h): add the 14 missing names + SVG paths to `Icon.tsx`, export `mediaIcon`. → `npm run typecheck` clean.
2. **Switch the stylesheet entry** (~30m): `main.tsx` → `./styles/index.css`; keep `global.css` alongside for one step; confirm no screen regresses.
3. **Finish the migration** (~3–4h): move screens off the 159 `global.css`-only classes onto the new primitives; define or delete the 40 nowhere classes (print view + propagation graph); wire or delete the 4 orphans; then **delete `global.css`** so one CSS system remains.
4. **Ship it** (~1h): `npm run build` + `npm run verify:contract` (72/72) + commit + push → Vercel redeploys.
5. **Decide the detector story for production**: upgrade the Render plan, or run image-only (347 MB fits where 1.26 GB does not), or demo locally with the detector on and keep the live URL as the backup. Do not let a judge's first click land on an abstention.
6. **Publish one honest number**: run `scripts/test_phash.py`, put the recall-vs-transformation table in the README and the deck.
7. **Quality pass for the 40%**: reconcile the fusion weights, refresh README counts, build the Docker image once on a machine with a daemon.
8. **Demo + deck**: label one screen recording as the official backup, write the runbook, update the 25 Aug deck to the shipped state.

## 8. Risk log
| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Wrong event assumed → optimising against the wrong rubric | med / fatal | confirm event, rubric, deadline, submission format before step 5 |
| CSS migration cascades and breaks working screens | med / high | migrate screen by screen, `verify:contract` after each, `global.css` stays until the last screen is off it |
| Judge clicks the live URL, detector abstains, reads it as "doesn't work" | high / high | step 5 + say the abstention is deliberate before they ask |
| Deadline closer than assumed | unknown / fatal | steps 1–4 are the shippable core; do them first regardless |

## 9. Decision log
- 2026-08-20 — frontend ownership moved to Daksh; full redesign authorised. Backend + API contract + fusion stay authoritative.
- 2026-09-01 — video and audio detectors made to abstain rather than run an untrained head. Honesty over coverage.
- 2026-09-04 — audited the tree: backend green, redesign unintegrated and non-compiling. Constraint = the build.
