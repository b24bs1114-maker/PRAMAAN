"""Pytest configuration.

Every test run gets its own throwaway data directory, so tests never touch a
real case database, evidence store or index. The environment is set *before*
``app.config`` is imported so the cached ``Settings`` picks it up.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="pramaan-tests-"))

os.environ["PRAMAAN_ENVIRONMENT"] = "testing"
os.environ["PRAMAAN_DEBUG"] = "false"
os.environ["PRAMAAN_LOG_LEVEL"] = "WARNING"
os.environ["PRAMAAN_LOG_ACCESS"] = "false"
os.environ["PRAMAAN_DATA_DIR"] = str(_TEST_ROOT / "data")
os.environ["PRAMAAN_REPORTS_DIR"] = str(_TEST_ROOT / "reports")
os.environ["PRAMAAN_CORPUS_DIR"] = str(_TEST_ROOT / "corpus")
os.environ["PRAMAAN_IMAGE_MODEL_PATH"] = ""
os.environ["PRAMAAN_VIDEO_MODEL_PATH"] = ""
os.environ["PRAMAAN_AUDIO_MODEL_PATH"] = ""
os.environ["PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT"] = ""
os.environ["PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT"] = ""
os.environ["PRAMAAN_AUDIO_DETECTOR_ENTRYPOINT"] = ""


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_root() -> Iterator[None]:
    yield
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def test_root() -> Path:
    return _TEST_ROOT


@pytest.fixture(scope="session")
def settings():
    from app.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def client() -> Iterator[object]:
    """Session-scoped TestClient with lifespan (schema creation) executed."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
