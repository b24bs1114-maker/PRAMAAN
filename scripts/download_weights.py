"""PRAMAAN model-asset provisioning: download, verify, place.

The one job of this script is to make ``pramaan-detector/weights/`` contain
exactly the checkpoints declared in ``model_manifest.json``, byte for byte,
whether it runs on a laptop that already has them or in a build container that
has nothing.

    manifest (filename + size + sha256 + release tag)
      -> resolve URL            (per-modality env override, else release asset)
      -> download to <name>.part (never the final path, so a failed or partial
                                  transfer can never be mistaken for a model)
      -> verify size and SHA-256
      -> atomic rename into the deterministic weights directory

The digest check is not advisory. A file whose SHA-256 does not match the
manifest is rejected -- a silently truncated or wrong-revision checkpoint
produces confident, wrong forensic verdicts, which is worse than no detector at
all. Set ``PRAMAAN_ALLOW_UNVERIFIED_WEIGHTS=1`` only for deliberate local
experiments with a checkpoint you built yourself.

Environment
-----------
``PRAMAAN_WEIGHTS_MODALITIES``      csv of image,video,audio. Default ``image``.
                                    ``none`` provisions nothing (for an instance
                                    too small to load any checkpoint).
``PRAMAAN_IMAGE_WEIGHTS_URL``       explicit URL, overrides the release asset.
``PRAMAAN_VIDEO_WEIGHTS_URL``       (no video checkpoint is published; see manifest)
``PRAMAAN_AUDIO_WEIGHTS_URL``
``PRAMAAN_WEIGHTS_RELEASE_TAG``     release tag to pull assets from.
``PRAMAAN_WEIGHTS_RELEASE_REPO``    ``owner/repo`` holding the release.
``PRAMAAN_WEIGHTS_DIR``             destination directory.
``PRAMAAN_FAIL_ON_MISSING_WEIGHTS`` ``1`` -> exit non-zero if any asset is absent.
``PRAMAAN_ALLOW_UNVERIFIED_WEIGHTS`` ``1`` -> warn instead of fail on digest mismatch.

Usage
-----
    python scripts/download_weights.py                     # provision image
    python scripts/download_weights.py --modality audio    # just audio
    python scripts/download_weights.py --all --strict       # everything, must succeed
    python scripts/download_weights.py --verify-only        # check what is here
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_WEIGHTS_DIR = PROJECT_ROOT / "pramaan-detector" / "weights"


def _weights_dir() -> Path:
    """Destination directory. A relative override resolves against the repo root
    (not the process CWD) so a build step and a start command agree."""
    override = os.getenv("PRAMAAN_WEIGHTS_DIR", "").strip()
    if not override:
        return REPO_WEIGHTS_DIR
    path = Path(override).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


WEIGHTS_DIR = _weights_dir()
#: The manifest ships with the source tree, so read it from there even when the
#: weights themselves are provisioned into a different (e.g. mounted) directory.
MANIFEST_PATH = REPO_WEIGHTS_DIR / "model_manifest.json"

ALL_MODALITIES = ("image", "video", "audio")
DEFAULT_MODALITIES = ("image",)
#: Values of ``PRAMAAN_WEIGHTS_MODALITIES`` that mean "provision nothing".
#: An empty string cannot mean that -- unset and empty are indistinguishable in a
#: build environment, and the default has to stay ``image`` -- so a deployment
#: that deliberately ships no checkpoints (a 512 MB instance cannot load either
#: model) needs a word for it. Without one, such a build either downloads 347 MB
#: it can never load or fails on ``modalities: none  not declared in the
#: manifest``.
NO_MODALITIES = ("none", "off", "disabled")

URL_ENV = {
    "image": "PRAMAAN_IMAGE_WEIGHTS_URL",
    "video": "PRAMAAN_VIDEO_WEIGHTS_URL",
    "audio": "PRAMAAN_AUDIO_WEIGHTS_URL",
}

CHUNK_BYTES = 1024 * 1024


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, read in 1 MiB chunks (checkpoints are up to 1.3 GB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        raise SystemExit(f"[ERROR] model manifest not found: {MANIFEST_PATH}")
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"[ERROR] model manifest is not valid JSON: {exc}") from exc


def resolve_url(modality: str, entry: dict, release: dict) -> str:
    """The URL this modality's checkpoint comes from, or ``""`` if undeclared.

    An explicit per-modality env var wins, so a deployment can point at its own
    mirror or a signed URL without editing the manifest.
    """
    explicit = os.getenv(URL_ENV[modality], "").strip()
    if explicit:
        return explicit
    asset = entry.get("release_asset") or entry.get("checkpoint_filename")
    template = release.get("asset_url_template", "")
    repo = os.getenv("PRAMAAN_WEIGHTS_RELEASE_REPO", "").strip() or release.get("repo", "")
    tag = os.getenv("PRAMAAN_WEIGHTS_RELEASE_TAG", "").strip() or release.get("tag", "")
    if not (asset and template and repo and tag):
        return ""
    return template.format(repo=repo, tag=tag, asset=asset)


def verify_file(path: Path, entry: dict, *, check_digest: bool = True) -> tuple[bool, str]:
    """Check an existing file against the manifest's size and digest."""
    size = path.stat().st_size
    expected_size = entry.get("weights_size_bytes")
    if expected_size is not None and size != expected_size:
        return False, (
            f"size {size} bytes does not match the manifest's {expected_size} "
            "(truncated or wrong revision)"
        )
    if not check_digest:
        return True, f"size {size} bytes matches the manifest (digest not checked)"
    expected_digest = (entry.get("weights_sha256") or "").strip().lower()
    if not expected_digest:
        return True, f"size {size} bytes; manifest declares no digest to check"
    digest = sha256_of(path)
    if digest != expected_digest:
        return False, f"SHA-256 {digest} does not match the manifest's {expected_digest}"
    return True, f"SHA-256 {digest} matches the manifest"


def download(url: str, dest: Path, entry: dict) -> tuple[bool, str]:
    """Fetch ``url`` into ``dest``, verifying before it takes the final name.

    The transfer goes to ``<dest>.part``. Only a file that passes the size and
    digest check is renamed onto ``dest``, so an interrupted build can never
    leave something that looks like a usable checkpoint.
    """
    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)

    print(f"    downloading {url}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "pramaan-weights/2"})
        with urllib.request.urlopen(request) as response, open(part, "wb") as handle:
            declared = response.headers.get("Content-Length")
            if declared:
                print(f"    Content-Length: {int(declared)} bytes")
            transferred = 0
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                handle.write(chunk)
                transferred += len(chunk)
        print(f"    received {transferred} bytes ({transferred / 1e6:.1f} MB)")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        part.unlink(missing_ok=True)
        return False, f"download failed: {type(exc).__name__}: {exc}"

    ok, detail = verify_file(part, entry)
    if not ok:
        part.unlink(missing_ok=True)
        return False, f"downloaded file rejected -- {detail}"

    part.replace(dest)
    # World-readable: the container runs as a non-root user that must be able to
    # read the checkpoint, and a 0600 file provisioned during build cannot be.
    dest.chmod(0o644)
    return True, detail


def provision(modality: str, entry: dict, release: dict, *, args) -> tuple[str, str]:
    """Return ``(status, detail)`` for one modality.

    Status is ok / missing / unpublished / failed. ``unpublished`` is a declared
    state, not an error: the manifest says no checkpoint exists for this
    modality, so there is nothing to download and nothing to verify. Video was
    previously declared with the image checkpoint's filename and digest, so this
    script downloaded 347 MB of Swin-B image weights "for video" and reported it
    provisioned.
    """
    if str(entry.get("status", "published")).lower() == "unpublished":
        print(f"\n--- {modality} ---")
        print("    [UNPUBLISHED] the manifest declares no checkpoint for this modality")
        detail = str(entry.get("status_detail") or "").strip()
        if detail:
            print(f"    {detail}")
        print(f"    Nothing downloaded. The {modality} detector will report "
              f"INSUFFICIENT_EVIDENCE.")
        return "unpublished", "no checkpoint is published for this modality"

    filename = entry.get("checkpoint_filename")
    if not filename:
        return "failed", "manifest entry declares no checkpoint_filename"
    dest = WEIGHTS_DIR / filename
    print(f"\n--- {modality}: {filename} ---")
    print(f"    target: {dest}")

    if dest.is_file():
        ok, detail = verify_file(dest, entry, check_digest=not args.no_hash)
        if ok:
            print(f"    [OK] present locally; {detail}")
            return "ok", detail
        if _flag("PRAMAAN_ALLOW_UNVERIFIED_WEIGHTS"):
            print(f"    [WARN] {detail}")
            print("    [WARN] accepted anyway: PRAMAAN_ALLOW_UNVERIFIED_WEIGHTS=1")
            return "ok", f"unverified: {detail}"
        print(f"    [FAIL] {detail}")
        print("    Refusing to use it. Delete the file to re-download, or set")
        print("    PRAMAAN_ALLOW_UNVERIFIED_WEIGHTS=1 if this checkpoint is yours.")
        return "failed", detail

    if args.verify_only:
        print("    [MISSING] not present (--verify-only, not downloading)")
        return "missing", "not present"

    url = resolve_url(modality, entry, release)
    if not url:
        print(f"    [MISSING] not present and no URL: set {URL_ENV[modality]} or a")
        print("    release repo/tag in the manifest.")
        return "missing", "no URL resolved"

    ok, detail = download(url, dest, entry)
    if ok:
        print(f"    [OK] downloaded and verified; {detail}")
        return "ok", detail
    print(f"    [FAIL] {detail}")
    return "failed", detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modality", action="append", choices=ALL_MODALITIES,
        help="Provision only these modalities (repeatable).",
    )
    parser.add_argument("--all", action="store_true", help="Provision every declared modality.")
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Report on what is present; never download.",
    )
    parser.add_argument(
        "--no-hash", action="store_true",
        help="Skip the digest read for files already present (size check only).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if any requested asset is missing. Implied by "
             "PRAMAAN_FAIL_ON_MISSING_WEIGHTS=1.",
    )
    args = parser.parse_args()

    strict = args.strict or _flag("PRAMAAN_FAIL_ON_MISSING_WEIGHTS")

    manifest = load_manifest()
    models = manifest.get("models", {})
    release = manifest.get("release", {})

    if args.all:
        wanted = [m for m in ALL_MODALITIES if m in models]
    elif args.modality:
        wanted = list(dict.fromkeys(args.modality))
    else:
        env_list = [
            item.strip().lower()
            for item in os.getenv("PRAMAAN_WEIGHTS_MODALITIES", "").split(",")
            if item.strip()
        ]
        if env_list and all(item in NO_MODALITIES for item in env_list):
            print("=== PRAMAAN model asset provisioning ===")
            print(f"modalities  : none (PRAMAAN_WEIGHTS_MODALITIES={env_list[0]})")
            print("[NOTE] no checkpoints requested, so none were downloaded. Every")
            print("[NOTE] modality reports UNAVAILABLE and is excluded from fusion.")
            return 0
        wanted = env_list or list(DEFAULT_MODALITIES)

    print("=== PRAMAAN model asset provisioning ===")
    print(f"manifest    : {MANIFEST_PATH} (v{manifest.get('manifest_version', '?')})")
    print(f"weights dir : {WEIGHTS_DIR}")
    print(f"modalities  : {', '.join(wanted)}")
    print(f"strict      : {strict}")
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    # Each distinct file is provisioned once. (Nothing shares a checkpoint today:
    # video used to declare the image one, which is why this de-duplication
    # exists. A modality with no checkpoint_filename is never a sharer.)
    results: dict[str, tuple[str, str]] = {}
    by_filename: dict[str, tuple[str, str]] = {}
    for modality in wanted:
        entry = models.get(modality)
        if entry is None:
            results[modality] = ("failed", "not declared in the manifest")
            print(f"\n--- {modality} ---\n    [FAIL] not declared in the manifest")
            continue
        filename = entry.get("checkpoint_filename") or ""
        if filename and filename in by_filename:
            status, detail = by_filename[filename]
            print(f"\n--- {modality}: {filename} ---")
            print(f"    [OK] shares this checkpoint with an earlier modality ({status})")
            results[modality] = (status, detail)
            continue
        outcome = provision(modality, entry, release, args=args)
        results[modality] = outcome
        if filename:
            by_filename[filename] = outcome

    print("\n=== summary ===")
    for modality, (status, detail) in results.items():
        print(f"  {modality:<6} {status:<12} {detail}")

    failed = [m for m, (s, _) in results.items() if s == "failed"]
    missing = [m for m, (s, _) in results.items() if s == "missing"]
    absent = [m for m, (s, _) in results.items() if s == "unpublished"]
    if failed:
        print(f"\n[ERROR] verification/download failed for: {', '.join(failed)}")
        return 1
    if missing and strict:
        print(f"\n[ERROR] strict mode: assets missing for: {', '.join(missing)}")
        return 1
    if missing:
        print(f"\n[WARN] assets missing for: {', '.join(missing)}.")
        print("[WARN] the detector will report UNAVAILABLE for those modalities.")
    if absent:
        # Not a warning about this run: there is no asset to fetch, by design.
        # Strict mode does not fail on it either -- a build cannot provision a
        # checkpoint that has never been published.
        print(f"\n[NOTE] no checkpoint is published for: {', '.join(absent)}.")
        print("[NOTE] those modalities report INSUFFICIENT_EVIDENCE for every file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
