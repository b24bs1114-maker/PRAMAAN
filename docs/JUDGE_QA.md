# PRAMAAN — Judge Q&A Preparation

Ownership answers for the demo. These reflect the work actually performed and
recorded in the repository — do not distribute technical ownership evenly for
the sake of balance.

## Team (concise wording)

Three-member team:

**Daksh — primary technical/product owner responsible for the implementation
and integration of PRAMAAN across frontend, backend, forensic pipeline,
provenance, models, reporting, and overall product.**

**Suyash — fusion and forensic demo validation.**

**Dev — demo QA, reliability, startup/runbook, and recovery readiness.**

## Presenter roles

**Daksh — primary presenter.**
- Product explanation and differentiation
- Full system architecture walkthrough
- UI/UX and frontend
- Backend and API integration
- Evidence pipeline (ingestion, SHA-256, perceptual hashes, indexing, matching)
- Provenance, propagation, earliest-known instance
- Audit chain and reports
- Overall technical implementation

**Suyash.**
- Fusion explanation and fusion reasoning
- Demo forensic result checking (AI/forensic outputs)
- Supporting technical validation

**Dev.**
- Demo QA and reliability
- Startup/runbook
- Backup/recovery
- Pre-demo checks

## Ownership answers

**"Who built the backend?"**
"Daksh handled the backend and overall system integration."

**"Who worked on the AI?"**
"Daksh integrated the multimodal detector stack, while Suyash handled fusion
and forensic result validation."

**"What did Dev work on?"**
"Dev focused on final QA, demo reliability, startup/readiness checks, backups,
and recovery."

**"Who owns the technical architecture?"**
"Daksh."

**"Who did the frontend?"**
"Daksh — including the UI/UX, the API client, and the frontend/backend
contract."

**"Who built the models?"**
"The three detectors (CommunityForensics ViT image, AASIST audio, VideoMAE
video) are published pretrained checkpoints — PRAMAAN integrates them,
digest-verifies them against a manifest, and runs them through its own honest
abstention contract. Daksh did that integration; Suyash validated the fusion
outputs they produce."

## Honesty rules for Q&A

- A detector that abstains is a feature, not a failure — say so before being
  asked (see docs/FINAL_DEMO_CHECKLIST.md, "Known limitations").
- Fusion weights and thresholds are uncalibrated prototype values; every score
  is a model output, not a probability.
- "Earliest known instance" is scoped to the indexed corpus — never present it
  as real-world origin.
- Do not claim the team trained the detector models. They are integrated,
  verified and wrapped, which is what the repository shows.
