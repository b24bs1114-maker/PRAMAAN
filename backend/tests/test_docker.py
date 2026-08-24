"""TASK 15 -- container deployment files.

Docker is not installed in this environment and this sandbox forbids listening
sockets, so the image cannot be built or run here. Everything that *can* be
verified without a Docker daemon is verified here instead, and it is more than a
lint pass:

* every ``COPY`` source exists and is not excluded by ``.dockerignore``;
* every ``PRAMAAN_*`` variable the image and compose file set is a real
  ``Settings`` field, so no setting is being configured under a name the app
  never reads;
* the health-check programs are *extracted from the deployment files and
  executed* against the real ASGI app, including the failure directions, so they
  are known to pass on a healthy service and fail on an unhealthy one;
* the paths that hold mutable state are backed by volumes, so a rebuilt
  container cannot lose evidence, the database, the index or the reports;
* the forbidden infrastructure (PostgreSQL, Redis, Kubernetes) appears nowhere.

Building and running the image is a manual step on a host with Docker; see the
README.
"""

from __future__ import annotations

import importlib.util
import json
import re
import urllib.request
from pathlib import Path

import pytest
import yaml

from app.config import BACKEND_DIR, Settings

DOCKERFILE = BACKEND_DIR / "Dockerfile"
COMPOSE_FILE = BACKEND_DIR / "docker-compose.yml"
DOCKERIGNORE = BACKEND_DIR / ".dockerignore"
REQUIREMENTS = BACKEND_DIR / "requirements.txt"

CONTAINER_DATA_PATHS = ("/data", "/reports")
FORBIDDEN = ("postgres", "postgresql", "redis", "kubernetes", "k8s", "rabbitmq")

# Distribution name -> module name, where they differ.
IMPORT_NAMES = {
    "pillow": "PIL",
    "sqlalchemy": "sqlalchemy",
    "python-multipart": "multipart",
    "pydantic-settings": "pydantic_settings",
    "uvicorn[standard]": "uvicorn",
}


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _dockerfile_lines() -> list[str]:
    """Dockerfile instructions with line continuations joined, as Docker sees them."""
    joined = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", "")
    return [
        line.rstrip()
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _instruction(keyword: str) -> list[str]:
    """Every instruction line starting with ``keyword``."""
    return [
        line for line in _dockerfile_lines()
        if line.split(maxsplit=1)[0].upper() == keyword.upper()
    ]


def _json_array(line: str) -> list[str]:
    """Parse the JSON exec-form array out of an instruction line."""
    return json.loads(line[line.index("[") : line.rindex("]") + 1])


def _dockerfile_env() -> dict[str, str]:
    """Every ``PRAMAAN_*`` variable set by ``ENV`` in the Dockerfile."""
    values: dict[str, str] = {}
    for line in _instruction("ENV"):
        for key, value in re.findall(r"(PRAMAAN_[A-Z0-9_]+)=(\S+)", line):
            values[key] = value
    return values


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def service(compose) -> dict:
    assert list(compose["services"]) == ["backend"], (
        "the backend deploys as a single service; extra services were added"
    )
    return compose["services"]["backend"]


def _settings_env_names() -> set[str]:
    return {f"PRAMAAN_{name.upper()}" for name in Settings.model_fields}


# --------------------------------------------------------------------------- #
# The files exist and describe the intended image
# --------------------------------------------------------------------------- #
def test_deployment_files_are_present():
    for path in (DOCKERFILE, COMPOSE_FILE, DOCKERIGNORE):
        assert path.is_file(), f"missing deployment file: {path.name}"


def test_image_is_built_on_python_312():
    from_lines = _instruction("FROM")
    assert len(from_lines) == 1, "single-stage build expected"
    # The project mandates Python 3.12 -- not 3.9, not 3.14.
    assert re.search(r"python:3\.12(\.\d+)?-slim", from_lines[0]), from_lines[0]


def test_container_runs_as_a_non_root_user():
    users = [line.split()[1] for line in _instruction("USER")]
    assert users, "no USER instruction: the container would run as root"
    assert users[-1] != "root"

    lines = _dockerfile_lines()
    user_at = max(i for i, line in enumerate(lines) if line.startswith("USER "))
    cmd_at = max(i for i, line in enumerate(lines) if line.startswith("CMD "))
    assert user_at < cmd_at, "USER must be dropped before CMD"


def test_every_copy_source_exists_and_survives_dockerignore():
    ignored = {
        line.strip().rstrip("/")
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        and not line.startswith("!")
    }

    copied = 0
    for line in _instruction("COPY"):
        parts = line.split()[1:]
        assert len(parts) >= 2, f"malformed COPY: {line}"
        for source in parts[:-1]:
            path = BACKEND_DIR / source
            assert path.exists(), f"COPY source does not exist: {source}"
            assert source.rstrip("/") not in ignored, (
                f"COPY {source} is excluded by .dockerignore"
            )
            copied += 1

    # requirements, app, tests, conftest.py, pytest.ini
    assert copied >= 5


def test_dockerignore_keeps_host_state_out_of_the_image():
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    for pattern in ("data/", ".venv/", "__pycache__/", ".git/"):
        assert pattern in text, f".dockerignore should exclude {pattern}"


def test_no_forbidden_infrastructure_is_introduced(compose):
    # Comments are allowed to *name* what is excluded; instructions are not.
    for line in _dockerfile_lines():
        lowered = line.lower()
        for term in FORBIDDEN:
            assert term not in lowered, f"Dockerfile instruction references {term}: {line}"

    # For compose, scan the parsed structure so commented-out examples and the
    # explanatory header are excluded but every real key and value is covered.
    rendered = json.dumps(compose).lower()
    for term in FORBIDDEN:
        assert term not in rendered, f"docker-compose.yml declares {term}"


def test_no_apt_packages_are_installed():
    # SQLite is in the standard library and Pillow ships manylinux wheels, so an
    # apt layer would only add attack surface and image size.
    for line in _instruction("RUN"):
        assert "apt-get install" not in line, line


# --------------------------------------------------------------------------- #
# Configuration is real configuration
# --------------------------------------------------------------------------- #
def test_dockerfile_env_names_are_real_settings():
    declared = _dockerfile_env()
    valid = _settings_env_names()

    assert declared, "the image sets no PRAMAAN_* variables"
    unknown = sorted(set(declared) - valid)
    assert not unknown, f"these variables are not Settings fields: {unknown}"


def test_compose_env_names_are_real_settings(service):
    valid = _settings_env_names()
    unknown = sorted(set(service["environment"]) - valid)
    assert not unknown, f"these variables are not Settings fields: {unknown}"


def test_storage_paths_are_absolute_and_consistent(service):
    image_env = _dockerfile_env()
    compose_env = service["environment"]

    for key, expected in (
        ("PRAMAAN_DATA_DIR", "/data"),
        ("PRAMAAN_REPORTS_DIR", "/reports"),
        ("PRAMAAN_CORPUS_DIR", "/corpus"),
    ):
        assert image_env[key] == expected
        # The compose file must not silently point the app somewhere else.
        assert compose_env[key] == expected


def test_the_settings_object_accepts_the_container_environment(monkeypatch, tmp_path):
    """The declared variables must actually parse into a usable Settings."""
    for key, value in _dockerfile_env().items():
        monkeypatch.setenv(key, value)
    # Redirect storage into tmp_path: /data is not writable here, and this test
    # is about parsing, not about the filesystem.
    for key, name in (
        ("PRAMAAN_DATA_DIR", "data"),
        ("PRAMAAN_REPORTS_DIR", "reports"),
        ("PRAMAAN_CORPUS_DIR", "corpus"),
    ):
        monkeypatch.setenv(key, str(tmp_path / name))

    settings = Settings()
    assert settings.environment == "production"
    assert settings.debug is False
    assert settings.enable_docs is False, "interactive docs must be off in production"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.data_dir.is_absolute()
    assert settings.database_url.startswith("sqlite")


def test_the_database_stays_sqlite_in_the_container(monkeypatch, tmp_path):
    monkeypatch.setenv("PRAMAAN_DATA_DIR", str(tmp_path))
    settings = Settings()
    assert settings.database_url == f"sqlite+pysqlite:///{tmp_path / 'pramaan.db'}"
    assert settings.db_path.parent == tmp_path


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_mutable_state_is_declared_as_volumes():
    declared = set()
    for line in _instruction("VOLUME"):
        declared.update(_json_array(line))
    for path in CONTAINER_DATA_PATHS:
        assert path in declared, f"{path} is not a VOLUME: state would die with the image"


def test_compose_persists_database_evidence_index_reports_and_corpus(compose, service):
    targets = {entry.split(":")[1]: entry.split(":")[0] for entry in service["volumes"]}

    # The SQLite database, the evidence store and the perceptual index all live
    # under /data, which must be a named volume so `docker compose down` keeps it.
    assert "/data" in targets
    assert targets["/data"] in compose["volumes"], "/data must be a named volume"

    # Reports and the reference corpus are bind-mounted so the examiner can read
    # the PDFs and drop corpus images in from the host.
    assert targets["/reports"].startswith("./")
    assert targets["/corpus"].startswith("../")

    assert (BACKEND_DIR / targets["/reports"].lstrip("./")).is_dir()
    assert (BACKEND_DIR / targets["/corpus"]).resolve().is_dir()


def test_no_state_is_written_inside_the_image(service):
    """Every configured storage path must resolve onto a volume."""
    mounted = {entry.split(":")[1] for entry in service["volumes"]}
    for key in ("PRAMAAN_DATA_DIR", "PRAMAAN_REPORTS_DIR", "PRAMAAN_CORPUS_DIR"):
        path = service["environment"][key]
        assert path in mounted, f"{key}={path} is not on a volume"


# --------------------------------------------------------------------------- #
# The service definition
# --------------------------------------------------------------------------- #
def test_api_is_published_on_loopback_only(service):
    assert service["ports"] == ["127.0.0.1:8000:8000"], (
        "an evidence-handling service must not be exposed on all interfaces by "
        "default"
    )


def test_compose_pins_the_image_and_restart_policy(service):
    assert service["build"]["context"] == "."
    assert service["build"]["dockerfile"] == "Dockerfile"
    assert service["image"].startswith("pramaan-backend:")
    assert service["restart"] == "unless-stopped"


def test_compose_declares_the_uid_so_bind_mounts_are_writable(service):
    assert "user" in service, (
        "without a uid:gid the container may be unable to write bind-mounted "
        "reports on Linux"
    )
    assert service["user"].startswith("${PRAMAAN_UID:-1000}")


def test_the_command_runs_one_uvicorn_worker():
    cmd = _json_array(_instruction("CMD")[-1])
    assert "uvicorn" in cmd
    assert "app.main:app" in cmd
    assert cmd[cmd.index("--host") + 1] == "0.0.0.0", "must bind inside the container"
    assert cmd[cmd.index("--port") + 1] == "8000"
    # SQLite is single-writer and the audit chain is sequential: more workers
    # would contend on both.
    assert cmd[cmd.index("--workers") + 1] == "1"


# --------------------------------------------------------------------------- #
# The health check actually works
# --------------------------------------------------------------------------- #
def _dockerfile_healthcheck() -> tuple[list[str], str]:
    line = _instruction("HEALTHCHECK")[0]
    return _json_array(line), line


def _compose_healthcheck(service) -> list[str]:
    test = service["healthcheck"]["test"]
    assert test[0] == "CMD", "shell-form health checks need a shell in the image"
    return test[1:]


def _run(program: str, monkeypatch, *, status: int, body: bytes) -> tuple[int, dict]:
    """Execute a health-check program against a stubbed HTTP response."""
    seen: dict = {}

    class _Response:
        def __init__(self) -> None:
            self.status = status

        def read(self, *args: object) -> bytes:
            return body

    def _urlopen(url, timeout=None):  # noqa: ANN001 - mirrors urllib's signature
        seen["url"] = url
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    code = 0
    try:
        exec(compile(program, "<healthcheck>", "exec"), {"__name__": "__main__"})
    except SystemExit as exit_signal:
        code = exit_signal.code or 0
    return code, seen


def test_dockerfile_healthcheck_uses_the_stdlib_and_the_right_endpoint():
    command, line = _dockerfile_healthcheck()

    assert command[0] == "python", "curl/wget are not installed in the slim image"
    assert command[1] == "-c"
    assert "urllib.request" in command[2]
    assert "http://127.0.0.1:8000/health" in command[2]
    assert "--interval=" in line and "--retries=" in line
    assert "--start-period=" in line, "the app needs a moment to create its schema"


def test_healthcheck_programs_pass_against_the_real_health_endpoint(
    client, monkeypatch, service
):
    live = client.get("/health")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}, "the health contract itself changed"

    for program in (_dockerfile_healthcheck()[0][2], _compose_healthcheck(service)[2]):
        code, seen = _run(
            program, monkeypatch, status=live.status_code, body=live.content
        )
        assert code == 0, f"health check failed against a healthy service: {program}"
        assert seen["url"] == "http://127.0.0.1:8000/health"
        # A hung request must not hold the check open until the daemon's timeout.
        assert 0 < seen["timeout"] <= 5


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, b'{"status":"ok"}'),          # right body, wrong status
        (200, b'{"status":"degraded"}'),    # right status, wrong body
        (200, b"{}"),                       # no status at all
    ],
)
def test_healthcheck_programs_fail_when_the_service_is_unhealthy(
    monkeypatch, service, status, body
):
    for program in (_dockerfile_healthcheck()[0][2], _compose_healthcheck(service)[2]):
        code, _ = _run(program, monkeypatch, status=status, body=body)
        assert code == 1, (
            f"health check passed a {status} / {body!r} response: {program}"
        )


def test_compose_healthcheck_timings_are_sane(service):
    check = service["healthcheck"]
    assert check["interval"] == "30s"
    assert check["timeout"] == "5s"
    assert check["retries"] == 3
    assert "start_period" in check


# --------------------------------------------------------------------------- #
# Requirements match the environment the image will build
# --------------------------------------------------------------------------- #
def _required_requirements() -> list[str]:
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    return [
        line.split("#")[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_every_required_dependency_is_importable():
    """A pin the code cannot import would break the image at runtime."""
    for requirement in _required_requirements():
        name = re.split(r"[<>=!\[]", requirement, maxsplit=1)[0].strip().lower()
        module = IMPORT_NAMES.get(requirement.lower(), IMPORT_NAMES.get(name, name))
        assert importlib.util.find_spec(module) is not None, (
            f"{requirement} is pinned but not importable as {module!r}"
        )


def test_optional_dependencies_are_not_pinned_as_required():
    """The fallback-backed extras must stay commented out."""
    required = " ".join(_required_requirements()).lower()
    for optional in ("faiss", "reportlab", "imagehash", "c2pa", "onnxruntime", "torch"):
        assert optional not in required, (
            f"{optional} is optional and has an in-tree fallback; it must not be a "
            "hard requirement"
        )


def test_requirements_documents_the_fallback_for_each_optional_dependency():
    text = REQUIREMENTS.read_text(encoding="utf-8").lower()
    for optional in ("faiss", "reportlab", "imagehash", "c2pa", "onnxruntime"):
        assert optional in text, f"{optional} is not documented as optional"
    assert "fallback" in text


def test_python_312_is_stated_in_requirements():
    assert "3.12" in REQUIREMENTS.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Offline posture
# --------------------------------------------------------------------------- #
def test_nothing_in_the_deployment_reaches_out_to_the_network():
    text = (
        DOCKERFILE.read_text(encoding="utf-8")
        + COMPOSE_FILE.read_text(encoding="utf-8")
    )
    # Only pip, at build time, may use the network. No runtime downloads, model
    # fetches or telemetry endpoints.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "pip install" in stripped:
            continue
        for scheme in ("https://", "http://"):
            if scheme in stripped:
                assert "localhost" in stripped or "127.0.0.1" in stripped, (
                    f"unexpected outbound URL in deployment files: {stripped}"
                )


def test_the_detector_is_configured_to_abstain_rather_than_guess(service):
    # `auto` means "use a local model if one is mounted in, otherwise report
    # UNAVAILABLE". It never invents a score.
    assert service["environment"]["PRAMAAN_DETECTOR_BACKEND"] in {"auto", "null"}
    assert "PRAMAAN_DETECTOR_MODEL_PATH" not in service["environment"], (
        "a model path must not be set unless a model is actually mounted"
    )
