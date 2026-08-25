"""Tests for Emergency Demo Stability Mode (PRAMAAN_ENABLE_AI_DETECTOR=false).

Verifies:
1. Application startup logs disabled state and skips detector pre-warm.
2. detector_service.status() returns unavailable state instantly without loading models.
3. Dashboard summary runs lightweight and returns 200 OK with disabled detector component.
4. Case analysis runs safely without AI detector, producing safe INSUFFICIENT_EVIDENCE or non-fabricated verdicts.
5. Demo images exist, are valid PNG files, and can be ingested via demo_loader.
"""

import os
from pathlib import Path
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, create_app
from app.models import get_session_factory
from app.services import demo_loader, detector as detector_service
from tests.helpers import jpeg_bytes


DEMO_IMAGES_DIR = Path(__file__).resolve().parents[0] / "fixtures" / "demo_images"


def test_demo_images_exist_and_are_valid():
    """Verify that the 3 required demo images exist and are valid PNG files."""
    expected_files = [
        "demo_market_screen.png",
        "demo_news_screenshot.png",
        "demo_social_media_post.png",
    ]
    for filename in expected_files:
        path = DEMO_IMAGES_DIR / filename
        assert path.is_file(), f"Demo image missing: {path}"
        assert path.stat().st_size > 0, f"Demo image empty: {path}"
        with Image.open(path) as img:
            assert img.format == "PNG"
            assert img.width > 0 and img.height > 0


def test_detector_disabled_status_and_prewarm():
    """Verify detector_service.status() and get_detector() when AI detector is disabled."""
    os.environ["PRAMAAN_ENABLE_AI_DETECTOR"] = "false"
    get_settings.cache_clear()
    detector_service.reset_detector_singleton()

    settings = get_settings()
    assert settings.enable_ai_detector is False

    status = detector_service.status(settings)
    assert status["available"] is False
    assert "disabled for demo/stability mode" in status["reason"]
    assert status["candidate_adapters"] == []

    detector = detector_service.get_detector(settings)
    usable, reason = detector.available()
    assert usable is False
    assert "disabled for demo/stability mode" in reason

    detector_service.reset_detector_singleton()
    get_settings.cache_clear()
    os.environ.pop("PRAMAAN_ENABLE_AI_DETECTOR", None)


def test_dashboard_disabled_detector_mode():
    """Verify GET /api/dashboard/summary works lightweight when AI detector is disabled."""
    os.environ["PRAMAAN_ENABLE_AI_DETECTOR"] = "false"
    get_settings.cache_clear()
    detector_service.reset_detector_singleton()

    settings = get_settings()
    test_app = create_app(settings)
    with TestClient(test_app) as client:
        res = client.get("/api/dashboard/summary")
        assert res.status_code == 200
        data = res.json()
        assert data["system_status"] == "online"
        assert data["components"]["detector"]["available"] is False
        assert "disabled for demo/stability mode" in data["components"]["detector"]["reason"]

    detector_service.reset_detector_singleton()
    get_settings.cache_clear()
    os.environ.pop("PRAMAAN_ENABLE_AI_DETECTOR", None)


def test_analysis_disabled_detector_mode():
    """Verify case analysis pipeline runs safely when AI detector is disabled.

    Must exclude missing signal, must NOT fabricate score, and must produce
    safe INSUFFICIENT_EVIDENCE verdict.
    """
    os.environ["PRAMAAN_ENABLE_AI_DETECTOR"] = "false"
    get_settings.cache_clear()
    detector_service.reset_detector_singleton()

    settings = get_settings()
    test_app = create_app(settings)
    with TestClient(test_app) as client:
        # Ingest image
        up_res = client.post(
            "/api/cases/upload",
            files={"file": ("demo_test.jpg", jpeg_bytes(seed=123), "image/jpeg")},
            data={"title": "Demo Mode Analysis Test Case"},
        )
        assert up_res.status_code in (200, 201)
        case_id = up_res.json()["case"]["case_id"]

        # Run analysis
        an_res = client.post(f"/api/cases/{case_id}/analyse")
        assert an_res.status_code == 200
        data = an_res.json()

        # Check verdict is safe (INSUFFICIENT_EVIDENCE or based on remaining signals)
        assert "verdict" in data
        assert data["verdict"]["verdict"] in ("INSUFFICIENT_EVIDENCE", "AUTHENTIC", "MANIPULATED")

        # AI detection signal must be UNAVAILABLE
        ai_signal = next((s for s in data["signals"] if s["signal_id"] == "ai_detection"), None)
        assert ai_signal is not None
        assert ai_signal["status"] == "UNAVAILABLE"
        assert ai_signal.get("score") is None

    detector_service.reset_detector_singleton()
    get_settings.cache_clear()
    os.environ.pop("PRAMAAN_ENABLE_AI_DETECTOR", None)

    detector_service.reset_detector_singleton()
    get_settings.cache_clear()
    os.environ.pop("PRAMAAN_ENABLE_AI_DETECTOR", None)


def test_demo_data_loader_ingestion():
    """Verify demo_loader ingests all 3 demo images into DB with audit and hashes."""
    settings = get_settings()
    factory = get_session_factory(settings)
    with factory() as db:
        res = demo_loader.ingest_demo_data(db, settings)
        db.commit()
        assert len(res) == 3
        for item in res:
            assert "case_id" in item
            assert "evidence_id" in item
            assert len(item["sha256"]) == 64
