"""
PRAMAAN Detector — full test suite.
Run with: pytest tests/ -v
"""
import io, wave, json
import numpy as np
import pytest
from pathlib import Path
from PIL import Image


# ══════════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_jpg(tmp_path):
    p = tmp_path / "test.jpg"
    Image.new("RGB", (120, 120), color=(128, 64, 32)).save(p)
    return str(p)


@pytest.fixture
def tmp_png(tmp_path):
    p = tmp_path / "test.png"
    Image.new("RGB", (200, 200), color=(10, 200, 50)).save(p)
    return str(p)


@pytest.fixture
def tmp_webp(tmp_path):
    p = tmp_path / "test.webp"
    Image.new("RGB", (150, 150), color=(200, 100, 50)).save(p, format="WEBP")
    return str(p)


@pytest.fixture
def tmp_wav(tmp_path):
    p = tmp_path / "test.wav"
    sr, duration = 16000, 3
    t = np.linspace(0, duration, sr * duration)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(p), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())
    return str(p)


@pytest.fixture
def tmp_short_wav(tmp_path):
    """WAV shorter than MIN_DURATION — should trigger abstention."""
    p = tmp_path / "short.wav"
    sr = 16000
    samples = np.zeros(100, dtype=np.int16)
    with wave.open(str(p), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())
    return str(p)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Schema / output contract
# ══════════════════════════════════════════════════════════════════════════════

class TestSchema:
    def test_manipulated_label(self):
        from pramaan.schema import make_result, LABEL_MANIPULATED
        r = make_result("image", 0.9, 0.8, "M", "1.0", "abc", 10.0, "test")
        assert r.label == LABEL_MANIPULATED
        assert not r.abstained
        assert r.manipulation_score == pytest.approx(0.9)

    def test_authentic_label(self):
        from pramaan.schema import make_result, LABEL_AUTHENTIC
        r = make_result("image", 0.1, 0.8, "M", "1.0", "abc", 10.0, "test")
        assert r.label == LABEL_AUTHENTIC
        assert not r.abstained

    def test_abstention_near_boundary(self):
        from pramaan.schema import make_result, LABEL_INSUFFICIENT
        r = make_result("image", 0.52, 0.5, "M", "1.0", "abc", 10.0, "test")
        assert r.label == LABEL_INSUFFICIENT
        assert r.abstained
        assert r.manipulation_score is None
        assert r.confidence is None

    def test_none_score_abstains(self):
        from pramaan.schema import make_result, LABEL_INSUFFICIENT
        r = make_result("image", None, None, "M", "1.0", "abc", 10.0, "test")
        assert r.label == LABEL_INSUFFICIENT
        assert r.abstained

    def test_to_dict_has_all_contract_fields(self):
        from pramaan.schema import make_result
        r = make_result("image", 0.9, 0.8, "M", "1.0", "abc", 10.0, "test")
        d = r.to_dict()
        required = {
            "media_type", "label", "manipulation_score", "confidence",
            "abstained", "model", "model_version", "weights_hash",
            "latency_ms", "explanation", "evidence",
            "heatmap_available", "regions", "timestamps",
        }
        assert required.issubset(d.keys())

    def test_to_dict_is_json_serialisable(self):
        from pramaan.schema import make_result
        r = make_result("audio", 0.8, 0.7, "A", "1.0", "xyz", 5.0, "ok",
                        evidence={"x": 1.0}, timestamps=[{"start_s": 0.0}])
        json.dumps(r.to_dict())  # must not raise

    def test_boundary_exact_0_5_abstains(self):
        from pramaan.schema import make_result, LABEL_INSUFFICIENT
        r = make_result("video", 0.5, 0.5, "V", "1.0", "h", 1.0, "x")
        assert r.label == LABEL_INSUFFICIENT

    def test_score_just_above_threshold_classifies(self):
        from pramaan.schema import make_result, LABEL_MANIPULATED
        # 0.5 + 0.15 + epsilon = 0.651 → should classify
        r = make_result("image", 0.651, 0.9, "M", "1.0", "h", 1.0, "x")
        assert r.label == LABEL_MANIPULATED

    def test_score_just_below_threshold_classifies(self):
        from pramaan.schema import make_result, LABEL_AUTHENTIC
        r = make_result("image", 0.349, 0.9, "M", "1.0", "h", 1.0, "x")
        assert r.label == LABEL_AUTHENTIC


# ══════════════════════════════════════════════════════════════════════════════
# 2. Service routing
# ══════════════════════════════════════════════════════════════════════════════

class TestServiceRouting:
    def test_routes_jpg_to_image(self, tmp_jpg):
        from pramaan.service import DetectorService
        r = DetectorService().detect(tmp_jpg)
        assert r.media_type == "image"

    def test_routes_png_to_image(self, tmp_png):
        from pramaan.service import DetectorService
        r = DetectorService().detect(tmp_png)
        assert r.media_type == "image"

    def test_routes_wav_to_audio(self, tmp_wav):
        from pramaan.service import DetectorService
        r = DetectorService().detect(tmp_wav)
        assert r.media_type == "audio"

    def test_unknown_extension_returns_insufficient(self, tmp_path):
        from pramaan.service import DetectorService
        from pramaan.schema import LABEL_INSUFFICIENT
        p = tmp_path / "file.xyz"
        p.write_text("data")
        r = DetectorService().detect(str(p))
        assert r.media_type == "unknown"
        assert r.label == LABEL_INSUFFICIENT
        assert r.abstained

    def test_result_has_latency(self, tmp_jpg):
        from pramaan.service import DetectorService
        r = DetectorService().detect(tmp_jpg)
        assert r.latency_ms > 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. Image detector
# ══════════════════════════════════════════════════════════════════════════════

class TestImageDetector:
    def test_jpg_returns_image_result(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_jpg)
        assert r.media_type == "image"
        assert r.latency_ms > 0
        assert "SwinB" in r.model or "EfficientNetB0" in r.model or r.model.startswith("ImageForensicNet")

    def test_png_supported(self, tmp_png):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_png)
        assert r.media_type == "image"

    def test_webp_supported(self, tmp_webp):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_webp)
        assert r.media_type == "image"

    def test_unsupported_ext_returns_insufficient(self, tmp_path):
        from pramaan.detectors.image_detector import ImageDetector
        from pramaan.schema import LABEL_INSUFFICIENT
        p = tmp_path / "file.bmp"
        p.write_bytes(b"\x00" * 100)
        r = ImageDetector().detect(str(p))
        assert r.label == LABEL_INSUFFICIENT

    def test_score_in_range(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_jpg)
        if r.manipulation_score is not None:
            assert 0.0 <= r.manipulation_score <= 1.0

    def test_confidence_in_range(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_jpg)
        if r.confidence is not None:
            assert 0.0 <= r.confidence <= 1.0

    def test_heatmap_returns_ndarray(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        cam = ImageDetector().get_heatmap(tmp_jpg)
        assert cam is not None
        assert isinstance(cam, np.ndarray)
        assert cam.ndim == 2
        assert cam.min() >= 0.0
        assert cam.max() <= 1.0

    def test_heatmap_available_flag(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_jpg)
        assert r.heatmap_available is True

    def test_evidence_contains_cam_shape(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_jpg)
        assert "cam_shape" in r.evidence

    def test_corrupt_file_returns_insufficient(self, tmp_path):
        from pramaan.detectors.image_detector import ImageDetector
        from pramaan.schema import LABEL_INSUFFICIENT
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"not an image")
        r = ImageDetector().detect(str(p))
        assert r.label == LABEL_INSUFFICIENT

    def test_explanation_is_string(self, tmp_jpg):
        from pramaan.detectors.image_detector import ImageDetector
        r = ImageDetector().detect(tmp_jpg)
        assert isinstance(r.explanation, str)
        assert len(r.explanation) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. Video detector
# ══════════════════════════════════════════════════════════════════════════════

class TestVideoDetector:
    def test_unsupported_ext_returns_insufficient(self, tmp_path):
        from pramaan.detectors.video_detector import VideoDetector
        from pramaan.schema import LABEL_INSUFFICIENT
        p = tmp_path / "file.avi"
        p.write_bytes(b"\x00" * 100)
        r = VideoDetector().detect(str(p))
        assert r.label == LABEL_INSUFFICIENT
        assert r.media_type == "video"

    def test_missing_file_returns_insufficient(self, tmp_path):
        from pramaan.detectors.video_detector import VideoDetector
        from pramaan.schema import LABEL_INSUFFICIENT
        r = VideoDetector().detect(str(tmp_path / "nonexistent.mp4"))
        assert r.label == LABEL_INSUFFICIENT

    def test_temporal_score_zero_for_single_frame(self):
        from pramaan.detectors.video_detector import _temporal_score
        assert _temporal_score([0.8]) == 0.0

    def test_temporal_score_high_for_variable_frames(self):
        from pramaan.detectors.video_detector import _temporal_score
        scores = [0.1, 0.9, 0.1, 0.9, 0.1, 0.9]
        assert _temporal_score(scores) > 0.5

    def test_temporal_score_low_for_consistent_frames(self):
        from pramaan.detectors.video_detector import _temporal_score
        scores = [0.8, 0.81, 0.79, 0.80, 0.82]
        assert _temporal_score(scores) < 0.2

    def test_aggregate_empty_frames(self):
        from pramaan.detectors.video_detector import _aggregate
        score, conf = _aggregate([], 0.0)
        assert score == pytest.approx(0.5)
        assert conf == pytest.approx(0.0)

    def test_aggregate_high_scores(self):
        from pramaan.detectors.video_detector import _aggregate
        score, conf = _aggregate([0.9, 0.85, 0.92], 0.3)
        assert score > 0.5

    def test_count_faces_returns_int(self, tmp_jpg):
        from pramaan.detectors.video_detector import _count_faces
        img = Image.open(tmp_jpg)
        n = _count_faces(img)
        assert isinstance(n, int)
        assert n >= 0


# ══════════════════════════════════════════════════════════════════════════════
# 5. Audio detector
# ══════════════════════════════════════════════════════════════════════════════

class TestAudioDetector:
    def test_wav_returns_audio_result(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        assert r.media_type == "audio"
        assert r.latency_ms > 0
        assert "Wav2Vec2" in r.model or r.model.startswith("AudioForensicNet")

    def test_score_in_range(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        if r.manipulation_score is not None:
            assert 0.0 <= r.manipulation_score <= 1.0

    def test_confidence_in_range(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        if r.confidence is not None:
            assert 0.0 <= r.confidence <= 1.0

    def test_unsupported_ext_returns_insufficient(self, tmp_path):
        from pramaan.detectors.audio_detector import AudioDetector
        from pramaan.schema import LABEL_INSUFFICIENT
        p = tmp_path / "file.ogg"
        p.write_bytes(b"\x00" * 100)
        r = AudioDetector().detect(str(p))
        assert r.label == LABEL_INSUFFICIENT

    def test_short_audio_abstains(self, tmp_short_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        from pramaan.schema import LABEL_INSUFFICIENT
        r = AudioDetector().detect(tmp_short_wav)
        assert r.label == LABEL_INSUFFICIENT

    def test_evidence_has_duration(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        assert "duration_s" in r.evidence
        assert r.evidence["duration_s"] > 0

    def test_evidence_has_chunk_scores(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        assert "chunk_scores" in r.evidence
        assert isinstance(r.evidence["chunk_scores"], list)

    def test_timestamps_are_list(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        assert isinstance(r.timestamps, list)

    def test_explanation_is_string(self, tmp_wav):
        from pramaan.detectors.audio_detector import AudioDetector
        r = AudioDetector().detect(tmp_wav)
        assert isinstance(r.explanation, str)
        assert len(r.explanation) > 0

    def test_aggregate_audio_empty(self):
        from pramaan.detectors.audio_detector import _aggregate_audio
        score, conf = _aggregate_audio([])
        assert score == pytest.approx(0.5)
        assert conf == pytest.approx(0.0)

    def test_aggregate_audio_high(self):
        from pramaan.detectors.audio_detector import _aggregate_audio
        score, conf = _aggregate_audio([0.9, 0.85, 0.88, 0.91, 0.87])
        assert score > 0.5
        assert conf > 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 6. Evaluation metrics
# ══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_eer_perfect_separation(self):
        from pramaan.evaluation.metrics import eer
        assert eer([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2]) < 0.05

    def test_eer_worst_case(self):
        from pramaan.evaluation.metrics import eer
        # random scores → EER near 0.5
        rng = np.random.default_rng(42)
        labels = [1, 0] * 50
        scores = rng.uniform(0, 1, 100).tolist()
        assert eer(labels, scores) < 0.6

    def test_compute_metrics_all_keys_present(self):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.metrics import compute_metrics
        m = compute_metrics([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2])
        for key in ("accuracy", "auc", "eer", "precision", "recall", "f1",
                    "confusion_matrix", "abstention_rate", "n_samples"):
            assert key in m

    def test_compute_metrics_values_in_range(self):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.metrics import compute_metrics
        m = compute_metrics([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2])
        for key in ("accuracy", "auc", "eer", "precision", "recall", "f1"):
            assert 0.0 <= m[key] <= 1.0

    def test_compute_metrics_confusion_matrix_shape(self):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.metrics import compute_metrics
        m = compute_metrics([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2])
        cm = m["confusion_matrix"]
        assert len(cm) == 2
        assert len(cm[0]) == 2

    def test_abstention_rate_computed(self):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.metrics import compute_metrics
        abstained = [True, False, False, False]
        m = compute_metrics([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2], abstained)
        assert m["abstention_rate"] == pytest.approx(0.25)

    def test_n_samples_correct(self):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.metrics import compute_metrics
        m = compute_metrics([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2])
        assert m["n_samples"] == 4


# ══════════════════════════════════════════════════════════════════════════════
# 7. Audio loading
# ══════════════════════════════════════════════════════════════════════════════

class TestAudioLoading:
    def test_load_wav_returns_float32(self, tmp_wav):
        from pramaan.detectors.audio_detector import _load_audio
        wav, sr = _load_audio(tmp_wav)
        assert wav.dtype == np.float32
        assert sr == 16000
        assert len(wav) > 0

    def test_load_wav_mono(self, tmp_wav):
        from pramaan.detectors.audio_detector import _load_audio
        wav, sr = _load_audio(tmp_wav)
        assert wav.ndim == 1


# ══════════════════════════════════════════════════════════════════════════════
# 8. Benchmark runner
# ══════════════════════════════════════════════════════════════════════════════

class TestBenchmark:
    def test_benchmark_runs_and_returns_dict(self, tmp_jpg, tmp_wav, tmp_path):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.benchmark import run_benchmark

        csv_path = tmp_path / "test.csv"
        # need both classes for metrics to compute
        jpg2 = tmp_path / "test2.jpg"
        Image.new("RGB", (100, 100), color=(0, 0, 0)).save(jpg2)
        csv_path.write_text(f"{tmp_jpg},1\n{str(jpg2)},0\n")

        out = run_benchmark(str(csv_path))
        assert "overall_metrics" in out
        assert "per_modality_metrics" in out
        assert "results" in out

    def test_benchmark_per_modality_split(self, tmp_jpg, tmp_wav, tmp_path):
        pytest.importorskip("sklearn")
        from pramaan.evaluation.benchmark import run_benchmark

        jpg2 = tmp_path / "test2.jpg"
        Image.new("RGB", (100, 100)).save(jpg2)
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(f"{tmp_jpg},1\n{str(jpg2)},0\n")

        out = run_benchmark(str(csv_path))
        assert "image" in out["per_modality_metrics"]

    def test_benchmark_saves_json(self, tmp_jpg, tmp_path):
        from pramaan.evaluation.benchmark import run_benchmark

        jpg2 = tmp_path / "test2.jpg"
        Image.new("RGB", (100, 100)).save(jpg2)
        csv_path = tmp_path / "test.csv"
        out_path  = tmp_path / "results.json"
        csv_path.write_text(f"{tmp_jpg},1\n{str(jpg2)},0\n")

        run_benchmark(str(csv_path), output_json=str(out_path))
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert "results" in data

    def test_benchmark_results_have_latency(self, tmp_jpg, tmp_path):
        from pramaan.evaluation.benchmark import run_benchmark

        jpg2 = tmp_path / "test2.jpg"
        Image.new("RGB", (100, 100)).save(jpg2)
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(f"{tmp_jpg},1\n{str(jpg2)},0\n")

        out = run_benchmark(str(csv_path))
        for row in out["results"]:
            assert "latency_ms" in row
            assert row["latency_ms"] >= 0
