"""Unit and integration tests for Google Cloud Vision Public Web Provenance Discovery.

Verifies:
1. API disabled (PRAMAAN_WEB_DISCOVERY_ENABLED=false) -> PUBLIC_WEB_DISCOVERY_UNAVAILABLE
2. Missing credentials -> PUBLIC_WEB_DISCOVERY_UNAVAILABLE
3. Successful web detection parsing:
   - matching page parsing
   - full image match parsing
   - partial match parsing
   - visually similar parsing
   - web entities and best guess labels
4. Duplicate URL removal
5. No-match response handling -> NO_RESULTS
6. API failure handling -> graceful ERROR response
7. Invalid/corrupted result payload handling
8. Public image verification gate (pHash + DINOv2)
9. Coexistence of internal and external provenance
10. HTTP endpoints: POST and GET /api/cases/{case_id}/web-discovery
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import app
from app.schemas.api import WebDiscoveryResponse
from app.services.web_provenance import (
    WebProvenanceService,
    extract_domain,
    is_web_discovery_available,
)
from tests.helpers import make_image


MOCK_VISION_WEB_DETECTION = {
    "webEntities": [
        {"entityId": "/m/019h78", "score": 0.8841, "description": "High Court of Delhi"},
        {"entityId": "/m/05g14", "score": 0.7420, "description": "Legal Document"},
    ],
    "fullMatchingImages": [
        {"url": "https://example.com/images/delhi_court_official.jpg"},
        {"url": "https://news.example.org/static/court_original.jpg"},
    ],
    "partialMatchingImages": [
        {"url": "https://blog.example.net/uploads/cropped_evidence.jpg"},
    ],
    "pagesWithMatchingImages": [
        {
            "url": "https://news.example.org/delhi-court-notice",
            "pageTitle": "Delhi High Court Notice Circulation Analysis",
            "fullMatchingImages": [
                {"url": "https://news.example.org/static/court_original.jpg"},  # Duplicate URL to test dedup
            ],
            "partialMatchingImages": [
                {"url": "https://news.example.org/static/court_thumb.jpg"},
            ],
        },
        {
            "url": "https://forum.example.com/topic/12345",
            "pageTitle": "Public Forum Discussion on Viral Image",
        },
    ],
    "visuallySimilarImages": [
        {"url": "https://archive.example.org/variants/similar_doc.png"},
    ],
    "bestGuessLabels": [
        {"label": "delhi high court notice 2026"},
    ],
}


def make_test_settings(**kwargs: Any) -> Settings:
    """Create test settings instance with overrides."""
    return Settings(
        environment="testing",
        pramaan_web_discovery_enabled=kwargs.get("enabled", False),
        google_application_credentials=kwargs.get("creds", ""),
        web_discovery_verify_downloads=False,  # default off in fast unit tests
    )


# --------------------------------------------------------------------------- #
# 1. API Disabled & Missing Credentials
# --------------------------------------------------------------------------- #
def test_web_discovery_disabled() -> None:
    settings = make_test_settings(enabled=False)
    available, reason = is_web_discovery_available(settings)
    assert available is False
    assert "disabled" in str(reason).lower()

    service = WebProvenanceService(settings)
    res = service.run_discovery(case_id="case-123", image_bytes=b"dummy")
    assert res.status == "PUBLIC_WEB_DISCOVERY_UNAVAILABLE"
    assert res.available is False
    assert res.occurrences == []


def test_web_discovery_missing_credentials(tmp_path: Path) -> None:
    # Enabled, but credentials path does not exist
    non_existent = str(tmp_path / "does_not_exist_credentials.json")
    settings = make_test_settings(enabled=True, creds=non_existent)
    available, reason = is_web_discovery_available(settings)
    assert available is False
    assert "not found" in str(reason).lower() or "not configured" in str(reason).lower()

    service = WebProvenanceService(settings)
    res = service.run_discovery(case_id="case-123", image_bytes=b"dummy")
    assert res.status == "PUBLIC_WEB_DISCOVERY_UNAVAILABLE"
    assert res.available is False


# --------------------------------------------------------------------------- #
# 2. Domain Extraction
# --------------------------------------------------------------------------- #
def test_extract_domain() -> None:
    assert extract_domain("https://www.example.com/path/to/img.jpg") == "example.com"
    assert extract_domain("https://sub.domain.org:8080/file.png?query=1") == "sub.domain.org:8080"
    assert extract_domain("not-a-valid-url") == "not-a-valid-url"


# --------------------------------------------------------------------------- #
# 3. Successful Web Detection Response Parsing
# --------------------------------------------------------------------------- #
def test_successful_web_detection_parsing() -> None:
    settings = make_test_settings(enabled=True)
    service = WebProvenanceService(settings)

    img = make_image(128, 128, seed=99)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    res = service.run_discovery(
        case_id="case-101",
        evidence_id="ev-202",
        image_bytes=img_bytes,
        raw_web_detection=MOCK_VISION_WEB_DETECTION,
    )

    assert res.status == "SUCCESS"
    assert res.available is True
    assert res.case_id == "case-101"
    assert res.evidence_id == "ev-202"

    # Check entities
    assert len(res.web_entities) == 2
    assert res.web_entities[0].description == "High Court of Delhi"
    assert res.web_entities[0].score == pytest.approx(0.8841, rel=1e-3)

    # Check best guess labels
    assert "delhi high court notice 2026" in res.best_guess_labels

    # Check summary
    assert res.summary.total_occurrences > 0
    assert res.summary.full_matches_count >= 2
    assert res.summary.pages_count >= 2
    assert res.summary.partial_matches_count >= 2
    assert res.summary.visually_similar_count >= 1

    # Check earliest discovered occurrence
    assert res.earliest_discovered_occurrence is not None
    assert res.earliest_discovered_occurrence.match_type in ("EXACT_MATCH", "NEAR_DUPLICATE", "VISUALLY_SIMILAR")

    # Check timeline
    assert len(res.timeline) == len(res.occurrences)
    assert res.timeline[0].is_earliest is True


# --------------------------------------------------------------------------- #
# 4. Deduplication of URLs
# --------------------------------------------------------------------------- #
def test_duplicate_url_removal() -> None:
    settings = make_test_settings(enabled=True)
    service = WebProvenanceService(settings)

    res = service.run_discovery(
        case_id="case-102",
        image_bytes=b"dummy",
        raw_web_detection=MOCK_VISION_WEB_DETECTION,
    )

    urls = [occ.url for occ in res.occurrences]
    assert len(urls) == len(set(urls)), "Occurrences must not contain duplicate URLs"
    # Verify the duplicate from pagesWithMatchingImages was merged
    assert urls.count("https://news.example.org/static/court_original.jpg") == 1


# --------------------------------------------------------------------------- #
# 5. Matching Page, Partial, and Visually Similar Parsing
# --------------------------------------------------------------------------- #
def test_category_parsing_and_match_types() -> None:
    settings = make_test_settings(enabled=True)
    service = WebProvenanceService(settings)

    res = service.run_discovery(
        case_id="case-103",
        image_bytes=b"dummy",
        raw_web_detection=MOCK_VISION_WEB_DETECTION,
    )

    page_occ = next((o for o in res.occurrences if o.url == "https://forum.example.com/topic/12345"), None)
    assert page_occ is not None
    assert page_occ.match_type == "PAGE_ONLY"
    assert page_occ.page_title == "Public Forum Discussion on Viral Image"
    assert page_occ.domain == "forum.example.com"

    partial_occ = next((o for o in res.occurrences if o.url == "https://blog.example.net/uploads/cropped_evidence.jpg"), None)
    assert partial_occ is not None
    assert partial_occ.match_type == "VISUALLY_SIMILAR"
    assert partial_occ.raw_google_category == "partial_matching_images"

    similar_occ = next((o for o in res.occurrences if o.url == "https://archive.example.org/variants/similar_doc.png"), None)
    assert similar_occ is not None
    assert similar_occ.match_type == "VISUALLY_SIMILAR"
    assert similar_occ.raw_google_category == "visually_similar_images"


# --------------------------------------------------------------------------- #
# 6. No-Match Response
# --------------------------------------------------------------------------- #
def test_no_match_response() -> None:
    settings = make_test_settings(enabled=True)
    service = WebProvenanceService(settings)

    empty_detection = {
        "webEntities": [],
        "fullMatchingImages": [],
        "partialMatchingImages": [],
        "pagesWithMatchingImages": [],
        "visuallySimilarImages": [],
    }

    res = service.run_discovery(
        case_id="case-empty",
        image_bytes=b"dummy",
        raw_web_detection=empty_detection,
    )

    assert res.status == "NO_RESULTS"
    assert res.available is True
    assert res.occurrences == []
    assert res.timeline == []
    assert res.earliest_discovered_occurrence is None


# --------------------------------------------------------------------------- #
# 7. API Failure Handling
# --------------------------------------------------------------------------- #
def test_api_failure_handling(tmp_path: Path) -> None:
    creds_file = tmp_path / "creds.json"
    creds_file.write_text("{}")
    settings = make_test_settings(enabled=True, creds=str(creds_file))
    service = WebProvenanceService(settings)

    class FailingClient:
        def detect_web(self, **kwargs: Any) -> Any:
            raise ConnectionError("Simulated gRPC / Google API connection error")

    res = service.run_discovery(
        case_id="case-fail",
        image_bytes=b"dummy",
        vision_client=FailingClient(),
    )

    assert res.status == "ERROR"
    assert res.available is True
    assert "connection error" in str(res.unavailable_reason).lower()


# --------------------------------------------------------------------------- #
# 8. Invalid Result Structure
# --------------------------------------------------------------------------- #
def test_invalid_result_structure() -> None:
    settings = make_test_settings(enabled=True)
    service = WebProvenanceService(settings)

    # Truncated or unexpected structure
    malformed_detection = {
        "webEntities": [{"corrupted": 123}],
        "fullMatchingImages": [{"no_url_key": True}],
        "pagesWithMatchingImages": "not-a-list",
    }

    res = service.run_discovery(
        case_id="case-malformed",
        image_bytes=b"dummy",
        raw_web_detection=malformed_detection,
    )

    assert res.status in ("NO_RESULTS", "SUCCESS")
    assert isinstance(res.occurrences, list)


# --------------------------------------------------------------------------- #
# 9. Verification Gate: Exact Match vs Visually Similar
# --------------------------------------------------------------------------- #
def test_verification_gate_simulated(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_test_settings(enabled=True)
    settings.web_discovery_verify_downloads = True
    service = WebProvenanceService(settings)

    img = make_image(128, 128, seed=12)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    raw_evidence_bytes = buf.getvalue()

    # Mock HTTP download of candidate image: returns identical image.
    # Mirrors the real httpx streaming interface the verifier now uses
    # (streamed reads enforce the size cap during download, not after).
    class MockStream:
        status_code = 200
        url = "https://trusted.media.org/photo.jpg"

        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self) -> "MockStream":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def iter_bytes(self):
            yield self._body

    class MockHttpClient:
        def __init__(self, *args: Any, **kwargs: Any):
            pass
        def __enter__(self) -> MockHttpClient:
            return self
        def __exit__(self, *args: Any) -> None:
            pass
        def stream(self, method: str, url: str) -> MockStream:
            return MockStream(raw_evidence_bytes)

    import httpx
    monkeypatch.setattr(httpx, "Client", MockHttpClient)

    # The SSRF gate resolves the host for real; this unit test exercises the
    # perceptual verification logic against a mocked client, not the gate
    # (which has its own test below), so the fictional host is allowed here.
    import app.services.web_provenance as wp
    monkeypatch.setattr(wp, "_url_is_public_http", lambda url: True)

    single_match_detection = {
        "fullMatchingImages": [{"url": "https://trusted.media.org/photo.jpg"}],
    }

    res = service.run_discovery(
        case_id="case-verify",
        image_bytes=raw_evidence_bytes,
        raw_web_detection=single_match_detection,
    )

    assert res.status == "SUCCESS"
    assert len(res.occurrences) == 1
    occ = res.occurrences[0]
    assert occ.verified is True
    assert occ.match_type == "EXACT_MATCH"
    assert occ.perceptual_distance == 0
    assert occ.similarity == 1.0


# --------------------------------------------------------------------------- #
# 10. Coexistence of Internal and External Provenance
# --------------------------------------------------------------------------- #
def test_internal_and_external_provenance_coexistence() -> None:
    """Verifies that internal provenance and external web discovery coexist cleanly."""
    with TestClient(app) as client:
        # 1. Ingest evidence into a new case via /api/cases/upload
        img = make_image(128, 128, seed=55)
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        up_res = client.post(
            "/api/cases/upload",
            data={
                "title": "Provenance Coexistence Case",
                "description": "Internal propagation alongside external discovery.",
            },
            files={"file": ("test_coexist.jpg", buf.getvalue(), "image/jpeg")},
        )
        assert up_res.status_code == 201
        upload_data = up_res.json()
        case_id = upload_data["case"]["case_id"]
        evidence_id = upload_data["evidence"]["evidence_id"]

        # 2. Internal provenance propagation endpoint works
        prop_res = client.get(f"/api/cases/{case_id}/propagation")
        assert prop_res.status_code == 200
        prop_data = prop_res.json()
        assert prop_data["origin"] is not None
        assert "earliest known instance in the indexed evidence corpus" in prop_data["origin"]["label"].lower()

        # 3. External web discovery endpoint works (unconfigured defaults to UNAVAILABLE)
        web_res = client.get(f"/api/cases/{case_id}/web-discovery")
        assert web_res.status_code == 200
        web_data = web_res.json()
        assert web_data["status"] == "PUBLIC_WEB_DISCOVERY_UNAVAILABLE"
        assert web_data["available"] is False

        # 4. POST /web-discovery runs without crashing
        post_web_res = client.post(
            f"/api/cases/{case_id}/web-discovery?evidence_id={evidence_id}"
        )
        assert post_web_res.status_code == 200
        assert post_web_res.json()["status"] == "PUBLIC_WEB_DISCOVERY_UNAVAILABLE"



# --------------------------------------------------------------------------- #
# 11. SSRF gate: candidate URLs are attacker-influenced and must be refused
#     when they target private, loopback or non-http space. (Regression for
#     the second-order SSRF the verifier previously exposed.)
# --------------------------------------------------------------------------- #
def test_ssrf_gate_blocks_private_and_non_http_targets() -> None:
    from app.services.web_provenance import _url_is_public_http

    # Non-http schemes and malformed URLs are refused outright.
    assert _url_is_public_http("ftp://example.com/x.jpg") is False
    assert _url_is_public_http("file:///etc/passwd") is False
    assert _url_is_public_http("not a url") is False
    assert _url_is_public_http("http://") is False

    # Literal loopback / private / link-local / unspecified hosts resolve to
    # rejected ranges even though the DNS lookup itself succeeds.
    assert _url_is_public_http("http://127.0.0.1:8000/api/cases") is False
    assert _url_is_public_http("http://localhost/secret") is False
    assert _url_is_public_http("http://10.1.2.3/img.jpg") is False
    assert _url_is_public_http("http://172.16.0.1/img.jpg") is False
    assert _url_is_public_http("http://192.168.1.10/img.jpg") is False
    assert _url_is_public_http("http://169.254.169.254/latest/meta-data/") is False
    assert _url_is_public_http("http://0.0.0.0/") is False

    # A host that does not resolve is refused rather than fetched blind.
    assert _url_is_public_http("http://this-host-does-not-resolve.invalid/x.jpg") is False


def test_ssrf_gate_rejects_public_name_resolving_to_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DNS name that resolves into private space must not pass the gate.

    Rebinding-style tricks point an ordinary-looking hostname at an internal
    address; the gate resolves every address the host maps to and refuses if
    any of them is private.
    """
    import socket

    from app.services import web_provenance as wp

    def fake_getaddrinfo(host: str, port: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.5", port))]

    monkeypatch.setattr(wp.socket, "getaddrinfo", fake_getaddrinfo)
    assert wp._url_is_public_http("https://rebinding.attacker.example/x.jpg") is False

    def fake_getaddrinfo_public(host: str, port: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(wp.socket, "getaddrinfo", fake_getaddrinfo_public)
    assert wp._url_is_public_http("https://rebinding.attacker.example/x.jpg") is True
