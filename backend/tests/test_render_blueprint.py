"""``render.yaml`` is a deployment artifact, so it is tested like one.

Every assertion here corresponds to a defect the blueprint actually had:

* it ran ``gunicorn``, which is not in ``backend/requirements.txt``, so the
  service could only ever have failed to boot;
* it declared no disk, so the SQLite case file, the stored evidence bytes, the
  perceptual-hash index, the generated reports and the append-only audit chain
  were destroyed on every restart and every deploy -- a chain of custody that does
  not survive a restart is not one;
* it set ``PRAMAAN_ENABLE_AI_DETECTOR=false`` *and* the two model paths, so the
  configuration advertised a detector it had switched off;
* it ran on an instance whose 512 MB cannot hold either checkpoint;
* it allowed ``*`` among the CORS origins.

The plan/RAM numbers below are Render's published compute plans and the measured
peaks in ``pramaan-detector/weights/model_manifest.json``. Nothing here contacts
Render: the blueprint is parsed as data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from app.config import PROJECT_ROOT, Settings

BLUEPRINT = PROJECT_ROOT / "render.yaml"
REQUIREMENTS = PROJECT_ROOT / "backend" / "requirements.txt"
MANIFEST = PROJECT_ROOT / "pramaan-detector" / "weights" / "model_manifest.json"

#: Render's compute plans, as MB of RAM. ``free`` is listed so the tests can say
#: what it cannot do rather than pretending it is not offered.
PLAN_MEMORY_MB = {
    "free": 512,
    "starter": 512,
    "0.5c-512mb": 512,
    "1c-2g": 2048,
    "2c-4g": 4096,
    "4c-8g": 8192,
    "8c-16g": 16384,
    "16c-32g": 32768,
}
#: Plans on which Render does not offer a persistent disk.
NO_DISK_PLANS = {"free"}

#: Resident cost of the memory-mapped Swin-B load path, plus the web process it
#: has to share the instance with. ``derived_safetensors.note`` in the manifest
#: records the 396 MB measurement; the rest is uvicorn, SQLAlchemy and Pillow.
IMAGE_DETECTOR_FOOTPRINT_MB = 396 + 250

#: ``PRAMAAN_*`` names the blueprint may set that are *not* ``Settings`` fields,
#: mapped to the file that must be shown to read them. Keeping the reader here
#: rather than a bare allow-list means a name cannot stay exempt after whatever
#: consumed it is deleted -- and cannot be invented in the first place.
PROCESS_ENV_ONLY = {
    "PRAMAAN_CONVERT_SAFETENSORS": PROJECT_ROOT / "backend" / "Dockerfile",
    "PRAMAAN_TORCH_THREADS": (
        PROJECT_ROOT / "pramaan-detector" / "pramaan" / "detectors" / "image_detector.py"
    ),
}


@pytest.fixture(scope="module")
def blueprint() -> dict:
    return yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def service(blueprint: dict) -> dict:
    services = blueprint["services"]
    assert len(services) == 1, f"expected exactly one service, got {len(services)}"
    return services[0]


@pytest.fixture(scope="module")
def env(service: dict) -> dict[str, object]:
    """The ``envVars`` list as a mapping, with duplicates rejected.

    Values are returned exactly as PyYAML parsed them, unconverted: whether
    ``"true"`` is a string or a bool is itself one of the things under test.
    """
    variables: dict[str, object] = {}
    for entry in service.get("envVars", []):
        assert "key" in entry, f"env var entry without a key: {entry}"
        key = entry["key"]
        assert key not in variables, f"{key} is declared twice"
        assert "value" in entry, (
            f"{key} has no value; a blueprint that relies on a dashboard-only "
            "value cannot be reviewed as an artifact"
        )
        variables[key] = entry["value"]
    return variables


def _declared_distributions() -> set[str]:
    """Distribution names from ``requirements.txt``, ignoring comments.

    Only uncommented lines count. The optional block at the bottom of that file
    is commented out precisely because it is not installed, so a start command
    naming one of those would be as broken as the ``gunicorn`` one was.
    """
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0]
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _server_program(command: str) -> str | None:
    """The program a start command would actually execute.

    Handles the four forms that appear in practice: ``gunicorn ...``,
    ``uvicorn ...``, ``python -m uvicorn ...`` and a JSON exec array wrapping
    ``sh -c "python -m uvicorn ..."``. What matters is the importable or
    executable name whose absence would stop the container from booting.
    """
    tokens = re.findall(r"[A-Za-z0-9_.\-/]+", command)
    for index, token in enumerate(tokens):
        program = token.rsplit("/", 1)[-1]
        if program.startswith("-") or program in {"sh", "bash", "exec", "env"}:
            continue
        if re.fullmatch(r"python(3(\.\d+)?)?", program):
            rest = tokens[index + 1 :]
            if "-m" in rest:
                module = rest[rest.index("-m") + 1 :]
                return module[0].split(".")[0] if module else None
            continue
        return program
    return None


def _plan_memory_mb(plan: str) -> int:
    assert plan in PLAN_MEMORY_MB, (
        f"unknown plan {plan!r}; add it to PLAN_MEMORY_MB with its RAM so the "
        "memory assertions below keep meaning something"
    )
    return PLAN_MEMORY_MB[plan]


def _provisioned_modalities(env: dict[str, object]) -> set[str]:
    """Which checkpoints the build would fetch. ``none`` means exactly that."""
    return {
        item.strip()
        for item in str(env.get("PRAMAAN_WEIGHTS_MODALITIES", "")).split(",")
        if item.strip() and item.strip() != "none"
    }


def _ai_enabled(env: dict[str, object]) -> bool:
    return str(env.get("PRAMAAN_ENABLE_AI_DETECTOR", "false")).strip().lower() == "true"


def test_the_blueprint_declares_one_docker_web_service(service: dict) -> None:
    assert service["type"] == "web"
    assert service["name"] == "pramaan-backend"
    assert "env" not in service, (
        "`env:` is the deprecated spelling of `runtime:`; a blueprint that still "
        "uses it is asking for the native Python runtime, which cannot install "
        "ffmpeg or libsndfile1 -- audio and video decoding would be broken in a "
        "way no build step could report"
    )
    assert service.get("runtime") == "docker", (
        f"runtime is {service.get('runtime')!r}; the blueprint must build the "
        "Dockerfile, which is the provisioning path the repository's own scripts "
        "and tests exercise"
    )


def test_it_builds_a_dockerfile_the_repository_actually_contains(service: dict) -> None:
    dockerfile = service["dockerfilePath"]
    assert dockerfile == "./backend/Dockerfile", dockerfile
    assert (PROJECT_ROOT / "backend" / "Dockerfile").is_file()
    context = service["dockerContext"]
    assert context == ".", (
        f"dockerContext is {context!r}, but the image COPYs pramaan-detector/ and "
        "scripts/, which live above backend/"
    )


def test_nothing_starts_a_program_that_is_not_installed(service: dict) -> None:
    """The gunicorn defect.

    The blueprint used to declare ``startCommand: gunicorn -k
    uvicorn.workers.UvicornWorker ...`` while ``gunicorn`` appears nowhere in
    ``backend/requirements.txt``. That service could not boot once, ever; the
    error would have surfaced only as a failed deploy on Render. So: whatever
    program is configured to serve must be a declared dependency -- here, and in
    the ``CMD`` of the image that is actually used.
    """
    declared = _declared_distributions()
    assert "uvicorn" in declared, declared

    for field in ("startCommand", "buildCommand", "dockerCommand"):
        command = service.get(field)
        if not command:
            continue
        program = _server_program(command)
        assert program in declared, (
            f"{field} runs {program!r}, which is not in backend/requirements.txt"
        )

    cmd = re.search(
        r'^CMD\s+(.+)$',
        (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert cmd, "the image declares no CMD"
    assert _server_program(cmd.group(1)) == "uvicorn", cmd.group(1)


def test_a_persistent_disk_is_declared(service: dict) -> None:
    """Without one, the chain of custody is destroyed on every deploy.

    The container filesystem is ephemeral. No disk means that on each restart the
    service loses the case database, the stored evidence bytes, the perceptual
    hash index, the generated reports and the append-only audit chain. The chain
    still verifies afterwards -- it verifies an empty chain, which is worse than
    a failure, because it looks like a pass.
    """
    disk = service.get("disk")
    assert disk, "no disk: uploaded evidence would not survive a restart"
    assert disk["name"] == "pramaan-evidence", disk
    assert disk["mountPath"].startswith("/"), disk
    assert isinstance(disk["sizeGB"], int) and disk["sizeGB"] >= 1, disk


def test_every_stored_artefact_resolves_inside_the_mounted_disk(
    service: dict, env: dict[str, object]
) -> None:
    """A disk that nothing is written to is decoration.

    The blueprint sets two paths; ``Settings`` derives four more from them, so
    this asks the real settings object where each artefact would land rather than
    assuming the layout.
    """
    mount = Path(service["disk"]["mountPath"])
    data_dir = env["PRAMAAN_DATA_DIR"]
    reports_dir = env["PRAMAAN_REPORTS_DIR"]

    settings = Settings(data_dir=Path(str(data_dir)), reports_dir=Path(str(reports_dir)))
    derived = {
        "case database": settings.db_path,
        "evidence store": settings.evidence_dir,
        "hash index": settings.index_dir,
        "generated reports": settings.reports_dir,
        "upload scratch": settings.temp_dir,
    }
    for label, path in derived.items():
        assert path == mount or mount in path.parents, (
            f"the {label} resolves to {path}, outside the {mount} disk, so it "
            "would be lost on the next deploy"
        )
    assert str(settings.database_url).endswith(str(settings.db_path)), settings.database_url


def test_a_disk_implies_a_plan_that_can_have_one(service: dict) -> None:
    """Render does not offer disks on the free instance.

    This is the constraint that forces a paid plan, and it cannot be worked
    around in configuration: persistence is the cost.
    """
    plan = service["plan"]
    _plan_memory_mb(plan)
    if service.get("disk"):
        assert plan not in NO_DISK_PLANS, (
            f"plan {plan!r} cannot mount a disk; Render would reject this "
            "blueprint, or accept it without the persistence it claims"
        )


def test_the_plan_can_hold_whatever_the_detector_settings_switch_on(
    service: dict, env: dict[str, object]
) -> None:
    """512 MB cannot hold either checkpoint. Both peaks are measured.

    The audio figure is read from the manifest rather than repeated here, so the
    test tracks the recorded measurement instead of a copy of it.
    """
    memory_mb = _plan_memory_mb(service["plan"])
    modalities = _provisioned_modalities(env)
    ai_enabled = _ai_enabled(env)

    if ai_enabled and "image" in modalities:
        assert memory_mb >= IMAGE_DETECTOR_FOOTPRINT_MB, (
            f"plan {service['plan']!r} gives {memory_mb} MB; the memory-mapped "
            f"Swin-B load peaks at 396 MB before the web process. Either raise "
            "the plan or set PRAMAAN_ENABLE_AI_DETECTOR to false and let the "
            "signal report UNAVAILABLE honestly"
        )

    if ai_enabled and "audio" in modalities:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        peak_mb = manifest["models"]["audio"]["peak_memory_bytes"] // (1000 * 1000)
        needed = peak_mb + (IMAGE_DETECTOR_FOOTPRINT_MB if "image" in modalities else 250)
        assert memory_mb >= needed, (
            f"audio is provisioned on {service['plan']!r} ({memory_mb} MB) but "
            f"peaks at {peak_mb} MB and needs ~{needed} MB alongside the rest of "
            "this deployment; it would be OOM-killed mid-request instead of "
            "returning a forensic result"
        )


def test_the_master_toggle_and_the_provisioned_weights_agree(
    env: dict[str, object]
) -> None:
    """The blueprint used to switch the detector off and set the model paths.

    Those two statements contradict each other, and the contradiction is not
    harmless: the configuration advertised a detector it had disabled. Only two
    combinations are coherent -- on with something to load, or off with nothing
    provisioned.
    """
    modalities = _provisioned_modalities(env)
    if _ai_enabled(env):
        assert modalities, (
            "PRAMAAN_ENABLE_AI_DETECTOR is true but no checkpoint is provisioned, "
            "so the detector would report UNAVAILABLE on an instance paid for to "
            "run it"
        )
    else:
        assert not modalities, (
            f"the detector is disabled but {sorted(modalities)} would still be "
            "downloaded into the image: minutes of build time and hundreds of MB "
            "for a model that is switched off, and a configuration that claims a "
            "capability it has turned off"
        )


def test_no_video_checkpoint_is_provisioned_or_pointed_at(
    env: dict[str, object]
) -> None:
    """Video is honestly unavailable, and the blueprint must not imply otherwise.

    Tied to the manifest rather than hardcoded: if a real ``video_detector.pt``
    is ever published, ``status`` changes and this test stops constraining it.
    Until then, pointing the video slot at ``image_detector.pt`` loads nothing --
    those are Swin-B image-classifier parameters, and the video frame model is
    EfficientNet-B0 -- while making the status endpoint advertise a video
    deepfake detector that abstains on every request.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["models"]["video"]["status"] == "published":
        pytest.skip("a video checkpoint has been published; this no longer applies")

    assert "video" not in _provisioned_modalities(env), (
        "PRAMAAN_WEIGHTS_MODALITIES asks for a video checkpoint that does not exist"
    )
    for key in ("PRAMAAN_VIDEO_MODEL_PATH", "PRAMAAN_VIDEO_WEIGHTS_URL"):
        assert not str(env.get(key, "")).strip(), (
            f"{key} is set, but no video checkpoint is published. Leave it unset: "
            "the video modality then reports available: false with its reason, and "
            "video analysis returns INSUFFICIENT_EVIDENCE with a null score"
        )


def test_every_variable_is_one_that_something_actually_reads(
    env: dict[str, object]
) -> None:
    """A misspelt key is silently ignored, which is the whole problem.

    ``PRAMAAN_DATA_DIRECTORY`` would not raise: it would be accepted, ignored,
    and the service would quietly write evidence to the ephemeral default. So
    every key must be either a real ``Settings`` field or one of the two the
    process environment is read for directly -- and for those, the file that
    reads it must still mention it.
    """
    settings_names = {f"PRAMAAN_{name.upper()}" for name in Settings.model_fields}
    for key in env:
        if not key.startswith("PRAMAAN_"):
            continue
        if key in settings_names:
            continue
        reader = PROCESS_ENV_ONLY.get(key)
        assert reader is not None, (
            f"{key} is not a Settings field and is not documented as read from the "
            f"process environment, so nothing would consume it"
        )
        assert key in reader.read_text(encoding="utf-8"), (
            f"{key} is exempted as process-env-only because {reader.name} reads it, "
            "but that file no longer mentions it"
        )


def test_values_are_strings_so_render_does_not_have_to_guess(
    service: dict, env: dict[str, object]
) -> None:
    """``value: true`` is a YAML boolean; ``value: 1`` is an integer.

    Render's blueprint schema takes strings, and an unquoted numeric or boolean
    is the classic way an env var arrives as something the platform rejects or
    coerces. Quoting them is not cosmetic.
    """
    for key, value in env.items():
        assert isinstance(value, str), (
            f"{key} parsed as {type(value).__name__} ({value!r}); quote it"
        )
    assert isinstance(service["plan"], str), service["plan"]
    assert isinstance(service["disk"]["sizeGB"], int), "sizeGB is a number, not a string"


def test_the_cors_origins_are_an_exact_allow_list(env: dict[str, object]) -> None:
    """``*`` here would let any site drive an evidence system with a browser."""
    raw = str(env["PRAMAAN_CORS_ALLOW_ORIGINS"])
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    assert origins, "no origins listed; the frontend could not call the API"
    assert "*" not in origins, (
        "a wildcard origin was allowed. List the deployed frontend origins exactly"
    )
    for origin in origins:
        assert re.fullmatch(r"https?://[\w.\-]+(:\d+)?", origin), (
            f"{origin!r} is not a bare scheme://host[:port] origin; a path or a "
            "trailing slash makes the browser's comparison fail silently"
        )
        assert origin.startswith("https://") or "localhost" in origin or "127.0.0.1" in origin, (
            f"{origin!r} is plain HTTP and not local"
        )


def test_the_health_check_path_is_a_route_the_app_serves(service: dict) -> None:
    """A health check pointed at a 404 makes every deploy fail, or -- worse on a
    path that happens to exist but is expensive -- makes Render restart a service
    that is merely busy. The route table is read from the app itself."""
    path = service["healthCheckPath"]
    assert path == "/health", (
        f"healthCheckPath is {path!r}; the app serves /health, and a probe on a "
        "path that 404s makes every deploy fail"
    )

    from app.main import app  # imported here: it builds the whole application

    routes = {
        getattr(route, "path", None): getattr(route, "methods", set()) for route in app.routes
    }
    assert path in routes, f"{path} is not a route; serving paths: {sorted(k for k in routes if k)}"
    assert "GET" in routes[path], routes[path]
    assert (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8").count(
        path
    ) >= 1, "the image HEALTHCHECK should probe the same path Render probes"


def test_the_documented_cheaper_profile_is_itself_coherent(blueprint: dict) -> None:
    """The header comments offer a three-line downgrade. Comments rot.

    So the documented alternative is parsed out and put through the same
    invariants as the live configuration: the plan must exist, must still support
    the disk, and the toggle must still agree with the provisioning. A downgrade
    that no longer applies is worse than none, because someone will apply it.
    """
    header = BLUEPRINT.read_text(encoding="utf-8").split("services:", 1)[0]
    plans = re.findall(r"^#\s+plan:\s*(\S+)", header, re.MULTILINE)
    assert plans, "the header no longer documents an alternative plan"

    documented = dict(
        re.findall(r"^#\s+(PRAMAAN_\w+):\s*\"?([\w,.\-]+)\"?", header, re.MULTILINE)
    )
    assert "PRAMAAN_ENABLE_AI_DETECTOR" in documented, documented
    assert "PRAMAAN_WEIGHTS_MODALITIES" in documented, documented

    for plan in plans:
        memory_mb = _plan_memory_mb(plan)
        if memory_mb < IMAGE_DETECTOR_FOOTPRINT_MB:
            assert not _ai_enabled(documented), (
                f"the header offers {plan} ({memory_mb} MB) without also turning "
                "the detector off"
            )
            assert not _provisioned_modalities(documented), (
                f"the header offers {plan} but still provisions "
                f"{documented['PRAMAAN_WEIGHTS_MODALITIES']!r}, which cannot load there"
            )
        if plan in NO_DISK_PLANS:
            assert "disk" in header.lower(), (
                f"{plan} cannot mount a disk, and the header does not say that "
                "evidence would stop surviving restarts"
            )


def test_the_deployment_guide_describes_this_blueprint(service: dict) -> None:
    """``DEPLOYMENT.md`` is what a judge or an operator reads instead of the YAML.

    Only the load-bearing facts are checked -- the plan, the mount and the disk --
    because those are the ones whose drift would send someone to the dashboard to
    configure a service that cannot persist evidence.
    """
    guide = (PROJECT_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    disk = service["disk"]
    for claim in (service["plan"], disk["name"], disk["mountPath"], f"{disk['sizeGB']} GB"):
        assert claim in guide, f"DEPLOYMENT.md does not mention {claim!r}"
    assert "gunicorn" in guide, (
        "the guide should keep explaining why there is no start command; that is "
        "the defect most likely to be reintroduced by hand in the dashboard"
    )
