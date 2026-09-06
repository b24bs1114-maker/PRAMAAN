"""System and settings status: what this deployment is actually configured to do.

This is the page an examiner reads to know what bounds the conclusions on this
host, so every figure on it has to come from real state. The counts are database
aggregates; the capability blocks come from the services themselves, so "detector
available" means a detector that loaded rather than a path that happens to be
set.

Two refusals are asserted as well. The endpoint is read-only -- fusion weights
and model paths are deployment configuration, not something a client can change
between two analyses of the same file. And reading it does not verify the audit
chain: a status page must not be able to pass for an integrity check.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.system import PROTOTYPE_NOTE, READ_ONLY_NOTE
from app.services import assessment as assessment_service
from app.services import audit as audit_service
from app.services import detector as detector_service
from app.services import fusion as fusion_service
from app.services import propagation as propagation_service
from tests.helpers import jpeg_bytes


def _status(client: TestClient) -> dict[str, Any]:
    res = client.get("/api/system/status")
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# Real state, not decoration
# --------------------------------------------------------------------------- #
def test_counts_are_database_aggregates(client: TestClient) -> None:
    before = _status(client)["counts"]
    assert set(before) == {
        "cases",
        "evidence",
        "analysis_results",
        "fused_evidence",
        "matches",
        "reports",
        "audit_entries",
    }

    uploaded = client.post(
        "/api/cases/upload",
        files={"file": ("status-count.jpg", jpeg_bytes(seed=45001), "image/jpeg")},
    )
    assert uploaded.status_code == 201
    case_id = uploaded.json()["case"]["case_id"]

    after = _status(client)["counts"]
    assert after["cases"] == before["cases"] + 1
    assert after["evidence"] == before["evidence"] + 1
    assert after["audit_entries"] > before["audit_entries"]
    # Ingestion alone stores no analysis and fuses nothing.
    assert after["fused_evidence"] == before["fused_evidence"]

    assert client.post(f"/api/cases/{case_id}/verdict").status_code == 200
    fused = _status(client)["counts"]
    assert fused["fused_evidence"] == before["fused_evidence"] + 1
    assert fused["analysis_results"] > after["analysis_results"]


def test_storage_and_database_state_is_reported_as_found(
    client: TestClient, settings
) -> None:
    data = _status(client)

    storage = data["storage"]
    assert set(storage) == {
        "data_dir",
        "evidence_dir",
        "index_dir",
        "reports_dir",
        "corpus_dir",
        "temp_dir",
    }
    assert storage["evidence_dir"]["path"] == str(settings.evidence_dir)
    for state in storage.values():
        assert state["exists"] is True
        assert state["writable"] is True

    database = data["database"]
    assert database["engine"] == "sqlite"
    assert database["path"] == str(settings.db_path)
    assert database["exists"] is True
    assert isinstance(database["size_bytes"], int) and database["size_bytes"] > 0


def test_capabilities_come_from_the_services_themselves(client: TestClient) -> None:
    data = _status(client)
    capabilities = data["capabilities"]

    detector = client.get("/api/detector/status").json()
    assert capabilities["detector"]["available"] == detector["available"]
    assert capabilities["detector"]["interface_version"] == (
        detector_service.INTERFACE_VERSION
    )
    if not detector["available"]:
        assert detector_service.UNAVAILABLE_EXPLANATION in data["notes"]

    index_state = client.get("/api/index/status").json()
    perceptual = capabilities["perceptual_index"]
    assert perceptual["indexed_count"] == index_state["indexed_count"]
    assert perceptual["pending_evidence_count"] >= 0
    assert perceptual["hashable_evidence_count"] >= perceptual["pending_evidence_count"]
    # Video and audio are outside perceptual retrieval, not missing from it.
    assert "not perceptually indexed" in perceptual["covers"]

    assert "signature_validation_available" in capabilities["c2pa_validator"]
    # A real PDF is produced either way: reportlab is an upgrade, not a gate.
    assert capabilities["report_renderer"]["writer"]
    assert capabilities["metadata_extractor"]["extractor"]


# --------------------------------------------------------------------------- #
# The detector socket
# --------------------------------------------------------------------------- #
def test_detector_contract_names_every_socket_a_model_plugs_into(
    client: TestClient,
) -> None:
    contract = _status(client)["detector_contract"]

    assert contract["interface_version"] == detector_service.INTERFACE_VERSION
    assert contract["modalities"] == list(detector_service.MODALITIES) == [
        "image",
        "video",
        "audio",
    ]
    assert "analyse(path, media_type=" in contract["entrypoint"]

    # The fields a detector may return, including the ones that make a result
    # auditable rather than just a number.
    for field in (
        "manipulation_score",
        "abstained",
        "model",
        "model_version",
        "weights_hash",
        "latency_ms",
        "explanation",
        "heatmap_available",
        "regions",
        "timestamps",
    ):
        assert field in contract["result_fields"]

    sockets = contract["sockets"]["configuration"]
    assert set(sockets) == set(detector_service.MODALITIES)
    for modality, socket in sockets.items():
        assert socket["model_path_env"] == f"PRAMAAN_{modality.upper()}_MODEL_PATH"
        assert socket["entrypoint_env"] == (
            f"PRAMAAN_{modality.upper()}_DETECTOR_ENTRYPOINT"
        )
        # Unset configuration is null, never an empty-string placeholder.
        assert socket["model_path"] is None or socket["model_path"]
        assert socket["entrypoint"] is None or socket["entrypoint"]
    assert "register_inference" in contract["sockets"]["in_process"]

    # The guarantee that makes the socket safe to leave empty.
    assert "manipulation_score is null" in contract["guarantee"]
    assert "No score is ever substituted for a missing one" in contract["guarantee"]


# --------------------------------------------------------------------------- #
# Configuration, reported and not editable
# --------------------------------------------------------------------------- #
def test_fusion_configuration_is_reported_exactly_as_configured(
    client: TestClient, settings
) -> None:
    fusion = _status(client)["fusion"]

    assert fusion["method"] == fusion_service.FUSION_METHOD
    assert fusion["version"] == fusion_service.FUSION_VERSION
    assert fusion["declared_weights"] == settings.fusion_weights
    assert fusion["thresholds"] == {
        "manipulated_at_or_above": settings.verdict_manipulated_threshold,
        "authentic_at_or_below": settings.verdict_authentic_threshold,
        "min_effective_weight": settings.fusion_min_effective_weight,
    }
    assert fusion["primary_signals"] == list(fusion_service.PRIMARY_SIGNALS)
    assert fusion["caveat"] == fusion_service.CAVEAT
    # Fusion measures; it does not decide. The block says so, so nobody reads the
    # weighted mean as the finding.
    assert "does not decide" in fusion["decision_authority"]
    # The weights are prototype defaults and the response says so on every read.
    assert PROTOTYPE_NOTE in _status(client)["notes"]


def test_the_decision_policy_is_published_in_full(
    client: TestClient, settings
) -> None:
    """The rules that produce a finding are visible, not inferred from findings.

    An examiner has to be able to see which checks were ever *entitled* to
    decide. A verdict alone cannot distinguish a signal that scored low from a
    signal that was never allowed to vote, and that distinction is the whole
    point of the eligible set.
    """
    policy = _status(client)["assessment"]

    assert policy["policy_id"] == assessment_service.POLICY_ID
    assert policy["policy_version"] == assessment_service.POLICY_VERSION

    # Eligibility, stated outright. Both members are task-scoped statements
    # about how the asset was produced.
    assert policy["eligible_checks"] == list(assessment_service.ELIGIBLE_CHECKS)
    assert policy["eligible_checks"] == ["ai_detection", "provenance_c2pa"]
    note = policy["eligible_checks_note"]
    assert "Missing EXIF" in note
    assert "absent C2PA manifest" in note
    assert "not synthetic-media evidence" in note

    # One scope per modality, each naming its own question, so no model's output
    # can be read as a universal authenticity claim.
    assert set(policy["scopes"]) == {"image", "video", "audio"}
    for media_type, entry in policy["scopes"].items():
        assert entry["scope"] == assessment_service.scope_for(media_type)
        assert entry["detail"]
    assert policy["scopes"]["audio"]["scope"] == (
        "synthetic_or_spoofed_speech_indicators"
    )

    # The four task-qualified states, and the two that count as a finding.
    assert set(policy["states"]) == set(assessment_service.ASSESSMENT_STATES)
    assert policy["conclusive_states"] == [
        "INDICATORS_DETECTED",
        "NO_INDICATORS_DETECTED",
    ]
    assert "NOT_ASSESSED" in policy["state_vs_execution_note"]

    # Missingness stays six distinct things all the way out to the client.
    assert set(policy["check_statuses"]) == set(assessment_service.CHECK_STATUSES)
    assert "Not a measurement of zero" in policy["check_statuses"]["ABSTAINED"]

    assert set(policy["reason_codes"]) == set(assessment_service.REASON_CODES)
    assert set(policy["roles"]) == {"DECISIVE", "DESCRIPTIVE"}

    # Thresholds are the deployment's configured ones, republished rather than
    # restated, and the note admits they are uncalibrated.
    assert policy["thresholds"] == {
        "manipulated_at_or_above": settings.verdict_manipulated_threshold,
        "authentic_at_or_below": settings.verdict_authentic_threshold,
        "minimum_eligible_coverage": settings.fusion_min_effective_weight,
    }
    assert any("calibrated" in text for text in policy["limitations"])

    # Conflict is refused, not resolved by averaging.
    assert "None." in policy["conflict_policy"]
    assert "not averaged" in policy["conflict_policy"]

    # Concept D is absent by design, and null rather than borrowed from C.
    assert policy["examiner_conclusion"] is None
    assert "never presented as a human judgement" in (
        policy["examiner_conclusion_note"]
    )

    # The legacy enum is declared to be a projection, including its lossiness.
    projection = policy["legacy_projection"]
    assert projection["mapping"] == dict(
        assessment_service.LEGACY_VERDICT_BY_STATE
    )
    assert projection["mapping"]["NOT_ASSESSED"] == "INSUFFICIENT_EVIDENCE"
    assert projection["mapping"]["INCONCLUSIVE"] == "INSUFFICIENT_EVIDENCE"
    assert "never a second decision" in projection["detail"]

    assert "single source" in policy["authority"]


def test_status_reports_configuration_and_cannot_change_it(
    client: TestClient,
) -> None:
    assert READ_ONLY_NOTE in _status(client)["notes"]
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/api/system/status").status_code == 405


def test_ingestion_limits_are_reported(client: TestClient, settings) -> None:
    ingestion = _status(client)["ingestion"]

    assert ingestion["max_upload_bytes"] == settings.max_upload_bytes
    assert ingestion["hash_bits"] == settings.hash_bits
    assert set(ingestion["allowed_extensions"]) == {"image", "video", "audio"}
    assert ingestion["allowed_extensions"]["audio"]
    assert "sniffing the file's own bytes" in ingestion["identification"]
    assert ingestion["retrieval"]["near_duplicate_max_distance"] == (
        settings.near_duplicate_max_distance
    )
    assert ingestion["retrieval"]["strong_duplicate_max_distance"] == (
        settings.strong_duplicate_max_distance
    )


# --------------------------------------------------------------------------- #
# Audit and vocabulary
# --------------------------------------------------------------------------- #
def test_reading_the_status_does_not_verify_the_chain(client: TestClient) -> None:
    def verifications() -> int:
        res = client.get(
            "/api/audit",
            params={"event": audit_service.EVENT_AUDIT_VERIFIED, "limit": 1},
        )
        return res.json()["total_rows"]

    before = verifications()
    for _ in range(3):
        _status(client)
    assert verifications() == before


def test_last_verification_is_the_one_that_was_actually_run(
    client: TestClient,
) -> None:
    verified = client.post("/api/audit/verify")
    assert verified.status_code == 200

    recorded = client.get(
        "/api/audit", params={"event": audit_service.EVENT_AUDIT_VERIFIED, "limit": 5000}
    ).json()["events"][-1]

    audit_block = _status(client)["audit"]
    assert audit_block["last_verified_at"] == recorded["timestamp"]
    assert audit_block["last_verification"]["audit_id"] == recorded["audit_id"]
    assert audit_block["last_verification"]["valid"] is True
    assert audit_block["genesis_hash"] == audit_service.GENESIS_HASH
    assert audit_block["algorithm"] == audit_service.ALGORITHM
    assert audit_block["verify_url"] == "/api/audit/verify"
    assert audit_block["trail_url"] == "/api/audit"
    assert "must not be able to pass for an integrity check" in (
        audit_block["last_verification_detail"]
    )


def test_vocabularies_are_published_so_the_ui_never_invents_a_label(
    client: TestClient,
) -> None:
    vocabularies = _status(client)["vocabularies"]

    assert set(vocabularies["verdicts"]) == {
        fusion_service.VERDICT_AUTHENTIC,
        fusion_service.VERDICT_MANIPULATED,
        fusion_service.VERDICT_INSUFFICIENT,
    }
    insufficient_text = vocabularies["verdicts"][fusion_service.VERDICT_INSUFFICIENT]
    assert "about the analysis" in insufficient_text
    assert "never about the media" in insufficient_text
    # The legacy token collapses two different assessment states, and the
    # vocabulary has to admit that rather than let a client read it as one.
    assert "INCONCLUSIVE" in insufficient_text
    assert "NOT_ASSESSED" in insufficient_text
    assert "assessment.state" in insufficient_text

    # The assessment vocabulary is published alongside the legacy tokens, so a
    # client can render the four real states without inventing labels.
    assert set(vocabularies["assessment_states"]) == set(
        assessment_service.ASSESSMENT_STATES
    )
    assert set(vocabularies["assessment_reason_codes"]) == set(
        assessment_service.REASON_CODES
    )
    assert set(vocabularies["check_statuses"]) == set(
        assessment_service.CHECK_STATUSES
    )

    assert set(vocabularies["signal_statuses"]) == {
        fusion_service.SIGNAL_OK,
        fusion_service.SIGNAL_INCONCLUSIVE,
        fusion_service.SIGNAL_UNAVAILABLE,
        fusion_service.SIGNAL_ERROR,
        fusion_service.SIGNAL_UNSUPPORTED,
    }
    # An unavailable signal is excluded, and the vocabulary says outright that it
    # is not zero.
    assert "Not zero" in vocabularies["signal_statuses"][
        fusion_service.SIGNAL_UNAVAILABLE
    ]
    assert len(vocabularies["provenance_states"]) == 4
    assert "not an indicator of manipulation" in vocabularies["provenance_states"][
        "ABSENT"
    ]
    assert len(vocabularies["match_bands"]) == 2
    assert all("not proof" in text for text in vocabularies["match_bands"].values())

    # The origin wording is fixed, and it is the one the propagation service uses.
    assert vocabularies["origin_label"] == propagation_service.ORIGIN_LABEL
    assert vocabularies["origin_label"] == (
        "earliest known instance in the indexed evidence corpus"
    )
    assert vocabularies["origin_caveats"]

    assert vocabularies["analysis_stages"] == [
        "metadata",
        "detector",
        "provenance",
        "forensics",
        "fusion",
        "propagation",
    ]
    assert set(vocabularies["audit_events"]) == set(audit_service.KNOWN_EVENTS)
    assert vocabularies["report"]["limitations"]


def test_app_block_states_the_offline_posture(client: TestClient, settings) -> None:
    app_block = _status(client)["app"]
    assert app_block["name"] == settings.app_name
    assert app_block["version"] == settings.app_version
    assert app_block["offline"] is True
    assert "no outbound network calls" in app_block["offline_detail"]
    assert app_block["cors_allow_origins"] == list(settings.cors_origins)


# --------------------------------------------------------------------------- #
# Public web discovery: the one stage that leaves the machine
# --------------------------------------------------------------------------- #
def test_web_discovery_capability_is_reported_with_a_reason(
    client: TestClient,
) -> None:
    """A settings page must be able to say whether this stage can actually run.

    Not "optional" in the abstract: disabled and uncredentialed are different
    problems with different fixes, so the block carries both the flag and the
    explanation. The reason names the environment variable, never its value.
    """
    block = _status(client)["capabilities"]["web_discovery"]
    assert block["available"] is False
    assert block["enabled"] is False
    assert "disabled" in block["reason"].lower()
    assert block["provider"] == "Google Cloud Vision Web Detection"


def test_offline_flag_follows_web_discovery_rather_than_being_asserted(
    client: TestClient,
) -> None:
    """The offline claim has to stop being true when the deployment goes online.

    ``offline`` was a hardcoded ``True`` printed beside a detail line promising
    "no outbound network calls at runtime". Credential public web discovery and
    that promise is false, but the status page went on making it -- and the
    settings screen went on rendering "OFFLINE VERIFICATION GUARD: ENFORCED"
    over a deployment that could reach Google Cloud Vision.

    The online half runs against a throwaway settings copy installed as a
    dependency override rather than a monkeypatched fixture. The ``settings``
    fixture is session-scoped and captured once, while other modules in this
    suite call ``get_settings.cache_clear()``; after one of those runs the
    fixture object is no longer the instance the app resolves, and patching it
    would silently assert nothing.
    """
    from pydantic import SecretStr

    from app.config import get_settings
    from app.main import app

    baseline = _status(client)
    assert baseline["app"]["offline"] is True
    assert baseline["capabilities"]["web_discovery"]["available"] is False

    credentialed = get_settings().model_copy(
        update={
            "pramaan_web_discovery_enabled": True,
            "google_cloud_vision_api_key": SecretStr("test-key-not-a-real-secret"),
        }
    )
    app.dependency_overrides[get_settings] = lambda: credentialed
    try:
        online = _status(client)
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert online["capabilities"]["web_discovery"]["available"] is True
    assert online["capabilities"]["web_discovery"]["reason"] is None
    assert online["app"]["offline"] is False
    assert "except public web discovery" in online["app"]["offline_detail"]

    # And the override is gone: the deployment is offline again for every test
    # that follows.
    assert _status(client)["app"]["offline"] is True

