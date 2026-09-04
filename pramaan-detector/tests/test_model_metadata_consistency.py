"""Model-metadata consistency: the manifest, the sidecars and the code must agree.

None of these are accuracy tests. They lock the *bookkeeping* that decides
whether a reported number means what the report says it means:

* which class index counts as "manipulated" (getting this wrong inverts every
  verdict silently, in both directions);
* which modalities have a published checkpoint at all (an unpublished one must
  not borrow another's filename, digest or model name to look provisioned);
* which checkpoints each detector will accept (an allowlist, so a wrong file
  cannot pass by not being on a denylist);
* that the manifest does not claim validation nobody performed.

Each test names the defect it locks out.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEIGHTS = REPO / "weights"
MANIFEST_PATH = WEIGHTS / "model_manifest.json"

IMAGE_CKPT = WEIGHTS / "image_detector.pt"
AUDIO_CKPT = WEIGHTS / "audio_detector.pt"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sidecar(checkpoint: Path) -> dict:
    return json.loads(checkpoint.with_suffix(".pt.json").read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. The manifest, the sidecar spec and the module constant agree on direction
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelDirectionIsDeclaredConsistently:
    """``positive_index`` appears in three places and must be one number.

    The sidecar is what ``backend.app.services.detector.postprocess`` actually
    reads, the module constant is what the plug-in reads, and the manifest is what
    a reader of the documentation believes. If they disagree, one of the three is
    describing a detector that does not exist -- and the failure is silent, because
    an inverted score is still a number in [0, 1].
    """

    def test_image_declares_class_zero_in_manifest_and_sidecar(self, manifest):
        entry = manifest["models"]["image"]
        assert entry["positive_index"] == 0
        assert _sidecar(IMAGE_CKPT)["positive_index"] == 0
        # The mapping the index refers to, so a future edit cannot renumber one
        # without the other.
        assert "SYNTHETIC" in entry["label_mapping"]["0"].upper()
        assert "AUTHENTIC" in entry["label_mapping"]["1"].upper()

    def test_audio_declares_class_one_everywhere(self, manifest):
        from pramaan.detectors import audio_detector

        entry = manifest["models"]["audio"]
        assert entry["positive_index"] == 1
        assert _sidecar(AUDIO_CKPT)["positive_index"] == 1
        assert audio_detector.POSITIVE_INDEX == 1
        assert "MANIPULATED" in entry["label_mapping"]["1"].upper()
        assert "AUTHENTIC" in entry["label_mapping"]["0"].upper()

    def test_sidecar_names_match_the_manifest(self, manifest):
        for modality, checkpoint in (("image", IMAGE_CKPT), ("audio", AUDIO_CKPT)):
            entry = manifest["models"][modality]
            spec = _sidecar(checkpoint)
            assert spec["model_name"] == entry["model_name"], modality
            assert spec["model_version"] == entry["model_version"], modality


# ══════════════════════════════════════════════════════════════════════════════
# 2. verify_label_direction refuses a config that contradicts POSITIVE_INDEX
# ══════════════════════════════════════════════════════════════════════════════

class TestAudioLabelDirectionIsCheckedNotAssumed:
    """The direction used to rest on a comment beside ``probs[1]``.

    A checkpoint whose config labels index 1 "real" would have been read as if it
    labelled it "fake": authentic speech reported as a clone and a clone reported
    as authentic, with nothing in the output to show it happened. So the config is
    checked against the constant before any tensor is read.
    """

    def test_the_upstream_mapping_is_accepted(self):
        from pramaan.detectors.audio_detector import verify_label_direction

        verify_label_direction({0: "real", 1: "fake"})
        verify_label_direction({"0": "REAL", "1": "FAKE"})

    def test_an_inverted_mapping_is_refused(self):
        from pramaan.detectors.audio_detector import verify_label_direction

        with pytest.raises(RuntimeError, match="invert every verdict"):
            verify_label_direction({0: "fake", 1: "real"})

    def test_a_missing_mapping_is_tolerated(self):
        """No config labels means nothing contradicts the constant.

        Refusing here would reject a valid checkpoint for lacking optional
        metadata; the sidecar's ``positive_index`` still governs.
        """
        from pramaan.detectors.audio_detector import verify_label_direction

        verify_label_direction(None)
        verify_label_direction({})

    def test_an_unrecognisable_mapping_is_tolerated_not_guessed(self):
        """Two synthetic-looking labels identify no single index, so no claim is made."""
        from pramaan.detectors.audio_detector import verify_label_direction

        verify_label_direction({0: "fake_a", 1: "synthetic_b"})
        verify_label_direction({0: "LABEL_0", 1: "LABEL_1"})


# ══════════════════════════════════════════════════════════════════════════════
# 3. An unpublished modality declares nothing and borrows nothing
# ══════════════════════════════════════════════════════════════════════════════

class TestVideoIsDeclaredAbsentNotProvisioned:
    """Video was declared with the *image* checkpoint's filename and digest.

    ``scripts/verify_model_assets.py`` therefore printed ``[OK]`` for video and a
    build log appeared to show three verified detectors where two exist. The
    manifest is the source both scripts read, so the absence has to be declared
    there, in a form a script can act on.
    """

    def test_video_declares_no_asset(self, manifest):
        entry = manifest["models"]["video"]
        assert entry["status"] == "unpublished"
        for field in ("checkpoint_filename", "release_asset",
                      "weights_size_bytes", "weights_sha256", "model_name",
                      "model_version"):
            assert entry[field] is None, f"video still declares {field}={entry[field]!r}"

    def test_video_borrows_no_other_modality_identity(self, manifest):
        """Not just the asset fields: no field anywhere in the entry may carry
        another modality's filename, digest or model name."""
        models = manifest["models"]
        blob = json.dumps(models["video"])
        for other in ("image", "audio"):
            digest = models[other]["weights_sha256"]
            filename = models[other]["checkpoint_filename"]
            name = models[other]["model_name"]
            assert digest not in blob, f"video entry carries the {other} digest"
            assert name not in blob, f"video entry names the {other} model"
            # The filename may only appear as prose explaining why it is *not*
            # the video checkpoint, never as a declared value.
            for key, value in models["video"].items():
                if key in ("why_no_checkpoint", "to_enable_video", "status_detail"):
                    continue
                assert filename != value, f"video declares {key}={filename}"

    def test_the_published_modalities_do_declare_their_assets(self, manifest):
        """The converse, so "declare nothing" cannot be satisfied by emptying
        every entry."""
        for modality, checkpoint in (("image", IMAGE_CKPT), ("audio", AUDIO_CKPT)):
            entry = manifest["models"][modality]
            assert entry["status"] == "published"
            assert entry["checkpoint_filename"] == checkpoint.name
            assert isinstance(entry["weights_size_bytes"], int)
            assert len(entry["weights_sha256"]) == 64


# ══════════════════════════════════════════════════════════════════════════════
# 4. Readiness is an allowlist: the wrong checkpoint cannot pass
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not IMAGE_CKPT.is_file(), reason="image checkpoint not provisioned")
class TestReadinessRefusesTheWrongCheckpoint:
    """The video check was a denylist that knew only the ``swin.`` prefix.

    So ``video.checkpoint_readiness("audio_detector.pt")`` returned ``True`` --
    the wav2vec2 speech checkpoint contains the string ``classifier.`` -- and
    since ``.env.example`` lists the audio and video path lines adjacently, one
    copy-paste made the status endpoint advertise an available video deepfake
    detector that abstained on every request.
    """

    def test_video_refuses_the_image_checkpoint(self):
        from pramaan.detectors.video_detector import checkpoint_readiness

        ready, reason = checkpoint_readiness(str(IMAGE_CKPT))
        assert ready is False
        assert "features." in reason or "classifier.1." in reason
        assert "NOT a finding of authenticity" in reason

    @pytest.mark.skipif(not AUDIO_CKPT.is_file(), reason="audio checkpoint not provisioned")
    def test_video_refuses_the_audio_checkpoint(self):
        from pramaan.detectors.video_detector import checkpoint_readiness

        ready, reason = checkpoint_readiness(str(AUDIO_CKPT))
        assert ready is False, "the wav2vec2 speech checkpoint is not a video model"
        assert "EfficientNet-B0" in reason

    def test_audio_refuses_the_image_checkpoint(self):
        from pramaan.detectors.audio_detector import checkpoint_readiness

        ready, reason = checkpoint_readiness(str(IMAGE_CKPT))
        assert ready is False
        assert "wav2vec2." in reason

    @pytest.mark.skipif(not AUDIO_CKPT.is_file(), reason="audio checkpoint not provisioned")
    def test_audio_accepts_its_own_checkpoint(self, monkeypatch):
        """The converse: a correct checkpoint must still pass, or "refuses
        everything" would satisfy the tests above."""
        from pramaan.detectors.audio_detector import checkpoint_readiness

        for name in ("PRAMAAN_DETECTOR_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            monkeypatch.delenv(name, raising=False)
        ready, reason = checkpoint_readiness(str(AUDIO_CKPT))
        assert ready is True, reason
        assert reason is None


# ══════════════════════════════════════════════════════════════════════════════
# 5. What cannot be established is reported as unknown, never as ready
# ══════════════════════════════════════════════════════════════════════════════

def _archive(tmp_path: Path, name: str, member: str, body: str) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, body)
    return path


class TestReadinessDoesNotGuessWhenItCannotTell:
    """These run without any checkpoint provisioned, which is the point.

    The two regressions being locked -- a denylist accepting a foreign
    checkpoint, and a zip-header check reported as a readiness result -- are
    properties of the *check*, so they are tested against small archives rather
    than against 1.6 GB of tensors that a clean clone does not have.
    """

    @pytest.mark.parametrize("module", ["video_detector", "audio_detector"])
    def test_an_absent_checkpoint_is_not_a_finding_about_the_media(self, tmp_path, module):
        import importlib

        readiness = getattr(importlib.import_module(f"pramaan.detectors.{module}"),
                            "checkpoint_readiness")
        ready, reason = readiness(str(tmp_path / "does_not_exist.pt"))
        assert ready is False
        assert "NOT a finding of authenticity" in reason
        assert "NOT a finding of manipulation" in reason
        assert "excluded from fusion" in reason

    @pytest.mark.parametrize("module", ["video_detector", "audio_detector"])
    def test_a_file_that_is_not_a_torch_archive_is_refused(self, tmp_path, module):
        """A zip that happens to open is not evidence of an architecture.

        The video check once answered from the zip header alone, so any archive
        counted as a video detector.
        """
        import importlib

        readiness = getattr(importlib.import_module(f"pramaan.detectors.{module}"),
                            "checkpoint_readiness")
        junk = _archive(tmp_path, f"{module}_junk.pt", "junk/version", "3")
        ready, reason = readiness(str(junk))
        assert ready is False
        assert "not a torch.save archive" in reason

    @pytest.mark.parametrize("module", ["video_detector", "audio_detector"])
    def test_a_truncated_download_is_refused(self, tmp_path, module):
        import importlib

        readiness = getattr(importlib.import_module(f"pramaan.detectors.{module}"),
                            "checkpoint_readiness")
        stub = tmp_path / f"{module}_truncated.pt"
        stub.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        ready, reason = readiness(str(stub))
        assert ready is False
        assert "could not be read as a checkpoint archive" in reason

    def test_video_refuses_a_checkpoint_that_only_looks_close(self, tmp_path):
        """The wav2vec2 regression in miniature.

        ``classifier.`` present, ``features.`` absent: the string the old
        denylist keyed on is there, the parameters this frame model needs are
        not. This is the shape of the file that made the status endpoint
        advertise a video deepfake detector.
        """
        from pramaan.detectors.video_detector import checkpoint_readiness

        decoy = _archive(
            tmp_path, "decoy_video.pt", "decoy/data.pkl",
            "wav2vec2.encoder.layers.0.attention.k_proj.weight projector.bias "
            "classifier.weight classifier.bias",
        )
        ready, reason = checkpoint_readiness(str(decoy))
        assert ready is False
        assert "features." in reason
        assert "EfficientNet-B0" in reason


# ══════════════════════════════════════════════════════════════════════════════
# 6. Audio: an unreachable architecture config is an honest "not ready"
# ══════════════════════════════════════════════════════════════════════════════

_HF_SLUG = "models--garystafford--wav2vec2-deepfake-voice-detector"


def _wav2vec2_shaped_archive(tmp_path: Path) -> Path:
    """An archive carrying the three parameter fragments the allowlist requires.

    Stands in for the real 1.26 GB checkpoint so the offline-config branch can be
    exercised on a machine that has not provisioned audio. It is a metadata
    fixture only -- nothing loads it.
    """
    return _archive(
        tmp_path, "audio_shaped.pt", "audio/data.pkl",
        "wav2vec2.encoder.layers.0.attention.k_proj.weight projector.weight "
        "classifier.weight classifier.bias",
    )

@pytest.fixture
def cold_hf_cache(tmp_path, monkeypatch) -> Path:
    """Point every Hugging Face cache root this module consults at an empty dir.

    ``_cached_config_paths()`` always globs ``~/.cache/huggingface/hub`` in
    addition to ``HF_HUB_CACHE`` and ``$HF_HOME/hub``, so ``HOME`` has to be
    isolated too or the developer's own warm cache decides the result.
    """
    root = tmp_path / "hf"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("HF_HOME", str(root / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(root / "hub"))
    return root


class TestAudioReadinessAccountsForTheArchitectureConfig:
    """Offline, the config must already be cached or the model cannot be built.

    Without this the status endpoint reported an available audio detector on any
    machine where the checkpoint existed -- and then every request abstained,
    because ``AutoConfig.from_pretrained`` had no cache to read and no hub to
    reach. An image that ships a 1.26 GB checkpoint and scores nothing is the
    worst of both.
    """

    def test_offline_without_a_cached_config_is_not_ready(self, tmp_path, monkeypatch, cold_hf_cache):
        from pramaan.detectors.audio_detector import checkpoint_readiness

        monkeypatch.setenv("PRAMAAN_DETECTOR_OFFLINE", "1")
        ready, reason = checkpoint_readiness(str(_wav2vec2_shaped_archive(tmp_path)))
        assert ready is False
        assert "Hugging Face cache" in reason
        assert "PRAMAAN_DETECTOR_OFFLINE" in reason
        # Still an abstention, not a verdict.
        assert "NOT a finding of authenticity" in reason

    def test_a_warm_cache_satisfies_the_offline_requirement(self, tmp_path, monkeypatch, cold_hf_cache):
        from pramaan.detectors.audio_detector import checkpoint_readiness

        monkeypatch.setenv("PRAMAAN_DETECTOR_OFFLINE", "1")
        snapshot = cold_hf_cache / "home" / "hub" / _HF_SLUG / "snapshots" / "abc123"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")

        ready, reason = checkpoint_readiness(str(_wav2vec2_shaped_archive(tmp_path)))
        assert ready is True, reason
        assert reason is None

    def test_online_needs_no_warm_cache(self, tmp_path, monkeypatch, cold_hf_cache):
        """The cache requirement is a consequence of the offline flag, not a
        second architecture check. A process allowed to fetch config.json is
        ready without one."""
        from pramaan.detectors.audio_detector import checkpoint_readiness

        for name in ("PRAMAAN_DETECTOR_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            monkeypatch.delenv(name, raising=False)
        ready, reason = checkpoint_readiness(str(_wav2vec2_shaped_archive(tmp_path)))
        assert ready is True, reason

    @pytest.mark.parametrize("flag", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
    def test_the_transformers_offline_flags_count_as_offline(self, tmp_path, monkeypatch, cold_hf_cache, flag):
        """``transformers`` obeys its own env vars. A deployment that sets one of
        those and not PRAMAAN_DETECTOR_OFFLINE is just as unable to fetch."""
        from pramaan.detectors.audio_detector import checkpoint_readiness

        monkeypatch.delenv("PRAMAAN_DETECTOR_OFFLINE", raising=False)
        monkeypatch.setenv(flag, "1")
        ready, reason = checkpoint_readiness(str(_wav2vec2_shaped_archive(tmp_path)))
        assert ready is False
        assert "Hugging Face cache" in reason


# ══════════════════════════════════════════════════════════════════════════════
# 7. The manifest claims no validation anybody would have to have performed
# ══════════════════════════════════════════════════════════════════════════════

class TestManifestMakesNoUnsupportedAccuracyClaim:
    """A checkpoint that loads is not a checkpoint that has been evaluated.

    Neither of the two published models has been measured against ground truth
    in this repository, so the manifest -- which is what a report's model
    provenance section is written from -- has to say so in the field a reader
    looks at.
    """

    def test_image_declares_itself_uncalibrated(self, manifest):
        status = manifest["models"]["image"]["validation_status"]
        assert "NOT CALIBRATED" in status
        assert "not a probability" in status
        # The thresholds are defaults, not operating points derived from data.
        assert "demonstration defaults" in status

    def test_audio_declares_itself_unvalidated(self, manifest):
        status = manifest["models"]["audio"]["validation_status"]
        assert "NOT EMPIRICALLY VALIDATED" in status
        assert "No accuracy, error rate, or operating threshold has been measured" in status

    def test_no_entry_advertises_a_number_nothing_here_measured(self, manifest):
        """No accuracy figure anywhere in the manifest.

        Nothing in this repository produces one, so a percentage here could only
        have been copied from an upstream model card -- where it was measured on
        that project's own test split -- and would be read as PRAMAAN's result.
        """
        blob = json.dumps(manifest)
        claims = re.findall(
            r"\d{1,3}(?:\.\d+)?\s*%\s*(?:accuracy|accurate|AUC|EER|F1|precision|recall)",
            blob, re.IGNORECASE,
        )
        assert claims == [], f"manifest states measured performance: {claims}"
        for phrase in ("state of the art", "state-of-the-art", "highly accurate",
                       "forensically proven", "court admissible", "court-admissible"):
            assert phrase not in blob.lower(), f"manifest claims {phrase!r}"

    def test_the_declared_peak_memory_matches_the_module_constant(self, manifest):
        """The number that decides whether a deployment can run audio at all."""
        from pramaan.detectors import audio_detector

        entry = manifest["models"]["audio"]
        assert entry["peak_memory_bytes"] == audio_detector.PEAK_MEMORY_BYTES
        assert "512 MB" in entry["deployment_note"]
        assert "INSUFFICIENT_EVIDENCE" in entry["deployment_note"]


# ══════════════════════════════════════════════════════════════════════════════
# 8. The published contract is the code's contract
# ══════════════════════════════════════════════════════════════════════════════

AUDIO_DOC = REPO.parent / "docs" / "AUDIO_READINESS.md"


@pytest.mark.skipif(not AUDIO_DOC.is_file(), reason="docs/ not present beside the package")
class TestTheDocumentedContractMatchesTheCode:
    """``docs/AUDIO_READINESS.md`` quotes constants rather than paraphrasing them,
    so a change to one without the other is caught here instead of by a reader
    who trusted the document."""

    def test_the_memory_figure_is_the_measured_constant(self):
        from pramaan.detectors.audio_detector import PEAK_MEMORY_BYTES

        text = AUDIO_DOC.read_text(encoding="utf-8")
        assert f"{PEAK_MEMORY_BYTES:_}" in text
        assert "512 MB" in text

    def test_the_doc_states_the_direction_the_code_reads(self):
        from pramaan.detectors.audio_detector import POSITIVE_INDEX

        text = AUDIO_DOC.read_text(encoding="utf-8")
        assert f"positive_index: {POSITIVE_INDEX}" in text

    def test_the_doc_claims_no_validation(self):
        text = AUDIO_DOC.read_text(encoding="utf-8")
        assert "has not been empirically validated" in text.lower()
        # The sine-wave fixtures must not be presented as deepfake-speech evidence.
        assert "not speech" in text
