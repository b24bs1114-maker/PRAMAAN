"""Pytest configuration.

Every test run gets its own throwaway data directory, so tests never touch a
real case database, evidence store or index. The environment is set *before*
``app.config`` is imported so the cached ``Settings`` picks it up.

Evidence ingestion requires an authenticated operator (see
``app.api.deps.get_current_user``). Rather than making several hundred existing
tests perform a login round-trip, the suite installs a dependency override that
resolves the seeded operator directly -- the same object the real dependency
would return, minus the token exchange. The ``anonymous_client`` fixture takes
the override off again, so ``tests/test_auth.py`` still exercises the genuine
login/token/401 path end to end.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends
from sqlalchemy import select

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
# Off, whatever the developer's .env says. Settings reads that file, so a machine
# with the local auth bypass enabled would otherwise run the whole suite with
# authentication disabled -- and every test asserting that an anonymous request is
# refused would pass by accident, or fail for the wrong reason. The suite tests the
# default; the bypass is exercised deliberately in tests/test_dev_auth_bypass.py,
# which patches its own settings.
os.environ["PRAMAAN_DEV_AUTH_BYPASS"] = "false"


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


def install_operator_override(app) -> None:
    """Make ``app`` resolve the seeded operator instead of demanding a token.

    Tests that build their own application with ``create_app()`` get their own
    ``dependency_overrides`` dict, so the suite-wide override installed on the
    module-level ``app`` does not reach them. Call this right after creating one.
    """
    from app.api.deps import get_current_user, get_db
    from app.config import get_settings
    from app.models import User
    from app.services import identity

    username = get_settings().seed_operator_username

    # No annotations here on purpose: this module uses postponed evaluation, and
    # FastAPI resolves a callable's annotations against its *module* globals --
    # where a name imported inside a function does not exist. Declaring the
    # dependency as a default value sidesteps annotation resolution entirely.
    def _current_user_override(db=Depends(get_db)):  # noqa: B008 - FastAPI idiom
        user = identity.get_user_by_username(db, username)
        if user is None:  # pragma: no cover - seeding guarantees one exists
            user = db.execute(select(User)).scalars().first()
        assert user is not None, "no operator account available for tests"
        return user

    app.dependency_overrides[get_current_user] = _current_user_override


@pytest.fixture(scope="session", autouse=True)
def _seeded_operator() -> Iterator[None]:
    """Create the schema, seed operators, and stand in for a signed-in operator.

    Autouse and session-scoped so it is in place before any test builds a
    ``TestClient``. ``app`` is a module-level singleton, so the override applies
    to every client the suite creates against it. The override resolves the
    operator through the request's own session, so the row handed to the
    endpoint is always attached to the session serving that request.
    """
    from app.api.deps import get_current_user
    from app.config import get_settings
    from app.main import app
    from app.models import init_db, session_scope
    from app.services import identity

    current_settings = get_settings()
    current_settings.ensure_directories()
    init_db(current_settings)
    with session_scope() as session:
        identity.seed_operators(session, current_settings)

    install_operator_override(app)
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(scope="session")
def operator(settings) -> dict[str, str]:
    """Credentials of the seeded primary operator, for real-login tests."""
    return {
        "username": settings.seed_operator_username,
        "password": settings.seed_operator_password.get_secret_value(),
        "display_name": settings.seed_operator_display_name,
        "role": settings.seed_operator_role,
    }


#: Intake now refuses to open a case without a title and an incident
#: description. These stand in for what an examiner types into the form.
INTAKE_TITLE = "Test case"
INTAKE_DESCRIPTION = "Automated test upload; no operational significance."

_UPLOAD_PATH = "/api/cases/upload"


def _fills_intake_defaults(url: object, kwargs: dict) -> bool:
    """True when this request opens a new case and omitted the intake fields."""
    if str(url).split("?", 1)[0].rstrip("/") != _UPLOAD_PATH:
        return False
    data = kwargs.get("data")
    if data is None:
        return True
    if not isinstance(data, dict):
        return False
    # Adding evidence to an existing case does not create one, so it carries no
    # title or description and must be passed through untouched.
    return not data.get("case_id")


def _intake_client_class():
    """Build the TestClient subclass used by the ``client`` fixture.

    ``POST /api/cases/upload`` refuses to open a case without a title and an
    incident description -- that is the intake contract the UI is held to. Most
    tests in this suite open a throwaway case only to get as far as hashing,
    retrieval, fusion, audit or reporting; none of them are about the intake
    form. This client supplies the two mandatory fields when, and only when, a
    test leaves them out, which is what the form does for a real operator.

    Nothing is ever overridden: a test that sends its own title or description
    keeps it verbatim. The requirement itself is asserted against a plain client
    in ``tests/test_intake_contract.py``, so this convenience cannot hide a
    regression in the validation it stands in for.
    """
    from fastapi.testclient import TestClient

    class IntakeDefaultsClient(TestClient):
        def post(self, url, *args, **kwargs):  # type: ignore[override]
            if _fills_intake_defaults(url, kwargs):
                data = dict(kwargs.get("data") or {})
                data.setdefault("title", INTAKE_TITLE)
                data.setdefault("description", INTAKE_DESCRIPTION)
                kwargs["data"] = data
            return super().post(url, *args, **kwargs)

    return IntakeDefaultsClient


@pytest.fixture(scope="session")
def client() -> Iterator[object]:
    """Session-scoped TestClient with lifespan (schema creation) executed.

    Fills in the mandatory intake fields for new-case uploads; see
    :func:`_intake_client_class`.
    """
    from app.main import app

    with _intake_client_class()(app) as test_client:
        yield test_client


@pytest.fixture
def strict_client() -> Iterator[object]:
    """A plain TestClient: no intake defaults, nothing filled in.

    Use this to assert what the API does with exactly the fields a test sends --
    the required-field rejections and the authentication boundary.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client() -> Iterator[object]:
    """A TestClient with the operator override *removed*: nobody is signed in.

    The override is what lets the rest of the suite skip the login round-trip, so
    any test of the real authentication boundary -- 401s, tokens, ``/api/auth/*``
    -- has to take it off first. It is restored afterwards, unconditionally, so a
    failure here cannot leave every later test unauthenticated.
    """
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.main import app

    previous = app.dependency_overrides.pop(get_current_user, None)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        if previous is not None:
            app.dependency_overrides[get_current_user] = previous
