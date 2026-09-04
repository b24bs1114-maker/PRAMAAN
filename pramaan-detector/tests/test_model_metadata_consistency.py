"""Model-metadata consistency: the manifest, the sidecars and the code must agree.

None of these are accuracy tests. They lock the *bookkeeping* that decides
whether a reported number means what the report says it means:

* which class index counts as "manipulated" (getting this wrong inverts every
  verdict silently, in both directions);
* which modalities have a published checkpoint at all;
* which checkpoints each detector will accept;
* that the manifest does not claim validation nobody performed.

Each test names the defect it locks out.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEIGHTS = REPO / "weights"
MANIFEST_PATH = WEIGHTS / "model_manifest.json"

IMAGE_CKPT = WEIGHTS / "image_detector.pt"
AUDIO_CKPT = WEIGHTS / "audio_detector.pt"
VIDEO_CKPT = WEIGHTS / "video_detector.pt"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sidecar(checkpoint: Path) -> dict:
    return json.loads(checkpoint.with_suffix(".pt.json").read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. The manifest, the sidecar spec and the module constant agree on direction
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelDirectionIsDeclaredConsistently:
    """``positive_index`` appears in three places and must be one number."""

    def test_image_declares_class_one_in_manifest_and_sidecar(self, manifest):
        entry = manifest["models"]["image"]
        assert entry["positive_index"] == 1
        assert _sidecar(IMAGE_CKPT)["positive_index"] == 1
        assert "MANIPULATED" in entry["label_mapping"]["1"].upper()
        assert "AUTHENTIC" in entry["label_mapping"]["0"].upper()

    def test_audio_declares_class_zero_everywhere(self, manifest):
        from pramaan.detectors import audio_detector

        entry = manifest["models"]["audio"]
        assert entry["positive_index"] == 0
        assert _sidecar(AUDIO_CKPT)["positive_index"] == 0
        assert audio_detector.POSITIVE_INDEX == 0
        assert "MANIPULATED" in entry["label_mapping"]["0"].upper()
        assert "AUTHENTIC" in entry["label_mapping"]["1"].upper()

    def test_video_declares_class_one_everywhere(self, manifest):
        from pramaan.detectors import video_detector

        entry = manifest["models"]["video"]
        assert entry["positive_index"] == 1
        assert _sidecar(VIDEO_CKPT)["positive_index"] == 1
        assert video_detector.POSITIVE_INDEX == 1
        assert "MANIPULATED" in entry["label_mapping"]["1"].upper()
        assert "AUTHENTIC" in entry["label_mapping"]["0"].upper()

    def test_sidecar_names_match_the_manifest(self, manifest):
        for modality, checkpoint in (
            ("image", IMAGE_CKPT),
            ("audio", AUDIO_CKPT),
            ("video", VIDEO_CKPT),
        ):
            entry = manifest["models"][modality]
            spec = _sidecar(checkpoint)
            assert spec["model_name"] == entry["model_name"], modality
            assert spec["model_version"] == entry["model_version"], modality


# ══════════════════════════════════════════════════════════════════════════════
# 2. verify_label_direction refuses a config that contradicts POSITIVE_INDEX
# ══════════════════════════════════════════════════════════════════════════════

class TestAudioLabelDirectionIsCheckedNotAssumed:
    def test_the_upstream_mapping_is_accepted(self):
        from pramaan.detectors.audio_detector import verify_label_direction

        verify_label_direction({0: "fake", 1: "real"})
        verify_label_direction({"0": "SPOOF", "1": "BONAFIDE"})

    def test_an_inverted_mapping_is_refused(self):
        from pramaan.detectors.audio_detector import verify_label_direction

        with pytest.raises(RuntimeError, match="contradicts POSITIVE_INDEX"):
            verify_label_direction({0: "real", 1: "fake"})

    def test_a_missing_mapping_is_tolerated(self):
        from pramaan.detectors.audio_detector import verify_label_direction

        verify_label_direction(None)
        verify_label_direction({})


# ══════════════════════════════════════════════════════════════════════════════
# 3. All three published modalities declare their own assets without collision
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishedModalitiesDeclareOwnAssets:
    def test_all_modalities_are_published(self, manifest):
        for modality in ("image", "audio", "video"):
            entry = manifest["models"][modality]
            assert entry["status"] == "published"
            assert entry["checkpoint_filename"] is not None
            assert isinstance(entry["weights_size_bytes"], int)
            assert len(entry["weights_sha256"]) == 64

    def test_modalities_do_not_borrow_identity(self, manifest):
        models = manifest["models"]
        for m1 in ("image", "audio", "video"):
            for m2 in ("image", "audio", "video"):
                if m1 == m2:
                    continue
                assert models[m1]["weights_sha256"] != models[m2]["weights_sha256"]
                assert models[m1]["model_name"] != models[m2]["model_name"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. Readiness checks for missing checkpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestReadinessReportsMissingCheckpointsTruthfully:
    @pytest.mark.parametrize("module", ["image_detector", "video_detector", "audio_detector"])
    def test_an_absent_checkpoint_is_not_a_finding_about_the_media(self, tmp_path, module):
        import importlib

        readiness = getattr(
            importlib.import_module(f"pramaan.detectors.{module}"),
            "checkpoint_readiness",
        )
        ready, reason = readiness(str(tmp_path / "does_not_exist.pt"))
        assert ready is False
        assert "NOT a finding of authenticity" in reason
        assert "NOT a finding of manipulation" in reason
        assert "excluded from fusion" in reason

    @pytest.mark.parametrize(
        "module,ckpt",
        [
            ("image_detector", IMAGE_CKPT),
            ("audio_detector", AUDIO_CKPT),
            ("video_detector", VIDEO_CKPT),
        ],
    )
    def test_existing_checkpoint_passes_readiness(self, module, ckpt):
        import importlib

        readiness = getattr(
            importlib.import_module(f"pramaan.detectors.{module}"),
            "checkpoint_readiness",
        )
        ready, reason = readiness(str(ckpt))
        assert ready is True, reason
        assert reason is None


# ══════════════════════════════════════════════════════════════════════════════
# 5. Manifest accuracy and limits
# ══════════════════════════════════════════════════════════════════════════════

class TestManifestMakesNoUnsupportedAccuracyClaim:
    def test_models_declare_empirically_tested_with_caveat(self, manifest):
        for modality in ("image", "audio", "video"):
            status = manifest["models"][modality]["validation_status"]
            assert "EMPIRICALLY TESTED" in status
            assert "not a" in status or "uncalibrated" in status or "not calibrated" in status or "Score is model output" in status

    def test_no_entry_advertises_false_legal_proof(self, manifest):
        blob = json.dumps(manifest)
        for phrase in ("state of the art", "state-of-the-art", "court admissible", "court-admissible"):
            assert phrase not in blob.lower(), f"manifest claims {phrase!r}"
