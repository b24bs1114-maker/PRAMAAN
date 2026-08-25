"""Application configuration and logging setup for the PRAMAAN backend.

Configuration is environment driven with development-friendly defaults, so the
service runs offline / on-premise with no external dependencies. Every setting
can be overridden with a ``PRAMAAN_``-prefixed environment variable or an entry
in a ``.env`` file (see ``.env.example``).

Logging note: settings values are only logged individually and deliberately.
Never dump the whole environment or a full ``Settings`` object into the log --
future secrets (signing keys, API tokens) must be declared as
``pydantic.SecretStr`` so they cannot be printed by accident.
"""

from __future__ import annotations

import logging
import logging.config
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor every default path to the repository layout rather than the process
# working directory, so the API behaves the same however it is launched.
APP_DIR = Path(__file__).resolve().parent          # backend/app
BACKEND_DIR = APP_DIR.parent                       # backend
PROJECT_ROOT = BACKEND_DIR.parent                  # PRAMAAN

LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def _split_csv(raw: str) -> list[str]:
    """Parse a comma-separated setting into a list of trimmed values."""
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings(BaseSettings):
    """Runtime configuration for the PRAMAAN backend."""

    model_config = SettingsConfigDict(
        # Project root first, backend/ second: the backend-local file wins.
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="PRAMAAN_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application identity -------------------------------------------------
    app_name: str = "PRAMAAN"
    app_version: str = "0.1.0"
    app_description: str = (
        "Offline-first digital forensic media provenance and authenticity "
        "analysis API. Prototype: thresholds and weights are not "
        "scientifically validated."
    )

    # --- Environment ----------------------------------------------------------
    environment: Literal["development", "testing", "production"] = "development"
    debug: bool = True

    # --- Server ---------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Logging --------------------------------------------------------------
    log_level: str = "INFO"
    log_access: bool = True

    # --- Storage paths --------------------------------------------------------
    # Relative overrides are resolved against the project root.
    data_dir: Path = BACKEND_DIR / "data"
    reports_dir: Path = BACKEND_DIR / "reports"
    corpus_dir: Path = PROJECT_ROOT / "corpus"

    # --- Database -------------------------------------------------------------
    # Empty means "<data_dir>/pramaan.db". SQLite only -- single portable case file.
    db_filename: str = "pramaan.db"
    db_echo: bool = False

    # --- Ingestion ------------------------------------------------------------
    max_upload_bytes: int = 64 * 1024 * 1024          # 64 MiB
    allowed_image_extensions: str = "jpg,jpeg,png,webp,tif,tiff,bmp,gif"
    allowed_video_extensions: str = "mp4,mov,m4v,avi,mkv,webm"
    allowed_audio_extensions: str = "wav,mp3,m4a,aac,flac,ogg"

    # --- Perceptual hashing / retrieval --------------------------------------
    hash_bits: int = 64                                # 8x8 grid -> 64 bit hashes
    retrieval_top_k: int = 25                          # candidates pulled from index
    near_duplicate_max_distance: int = 12              # Hamming, candidate cut-off
    strong_duplicate_max_distance: int = 6             # Hamming, high-confidence band

    # --- Fusion weights (transparent, configurable) ---------------------------
    # Relative weights; normalised across whichever signals are available.
    fusion_weight_ai_detection: float = 0.35
    fusion_weight_perceptual: float = 0.20
    fusion_weight_metadata: float = 0.20
    fusion_weight_provenance: float = 0.15
    fusion_weight_forensics: float = 0.10

    # --- Fusion thresholds (PROTOTYPE HEURISTICS -- not validated) ------------
    verdict_manipulated_threshold: float = 0.65
    verdict_authentic_threshold: float = 0.35
    fusion_min_effective_weight: float = 0.30

    # --- AI detector ----------------------------------------------------------
    # auto: use a local pretrained detector if one is installed, else report
    # UNAVAILABLE. null: always report UNAVAILABLE (never invents a score).
    detector_backend: Literal["auto", "null"] = "auto"
    detector_model_path: str = ""
    image_model_path: str = ""
    video_model_path: str = ""
    audio_model_path: str = ""
    image_weights_url: str = ""

    # Inference entrypoints, one per modality, as "module:callable".
    image_detector_entrypoint: str = ""
    video_detector_entrypoint: str = ""
    audio_detector_entrypoint: str = ""

    # --- Reporting ------------------------------------------------------------
    report_examiner: str = ""
    report_organisation: str = "PRAMAAN Prototype Deployment"

    # --- CORS -----------------------------------------------------------------
    # Comma-separated strings (not JSON lists) so a plain .env stays readable.
    # Defaults cover Vite (5173) and Create React App / Next.js (3000).
    cors_allow_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"
    cors_allow_credentials: bool = False

    # --- Validators -----------------------------------------------------------
    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> str:
        level = str(value).strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {sorted(LOG_LEVELS)}, got {level!r}"
            )
        return level

    @field_validator("data_dir", "reports_dir", "corpus_dir", mode="after")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("image_model_path", "video_model_path", "audio_model_path", mode="after")
    @classmethod
    def _absolute_str_path(cls, value: str) -> str:
        if not value:
            return ""
        path = Path(value)
        resolved = path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
        return str(resolved)

    # --- Derived values -------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"

    @property
    def corpus_manifest_path(self) -> Path:
        return self.corpus_dir / "manifest.json"

    @property
    def image_extensions(self) -> set[str]:
        return {e.lower().lstrip(".") for e in _split_csv(self.allowed_image_extensions)}

    @property
    def video_extensions(self) -> set[str]:
        return {e.lower().lstrip(".") for e in _split_csv(self.allowed_video_extensions)}

    @property
    def audio_extensions(self) -> set[str]:
        return {e.lower().lstrip(".") for e in _split_csv(self.allowed_audio_extensions)}

    @property
    def fusion_weights(self) -> dict[str, float]:
        """Declared weight per signal id (before availability normalisation)."""
        return {
            "ai_detection": self.fusion_weight_ai_detection,
            "perceptual_duplication": self.fusion_weight_perceptual,
            "metadata_integrity": self.fusion_weight_metadata,
            "provenance_c2pa": self.fusion_weight_provenance,
            "compression_forensics": self.fusion_weight_forensics,
        }

    @property
    def cors_origins(self) -> list[str]:
        return _split_csv(self.cors_allow_origins)

    @property
    def cors_methods(self) -> list[str]:
        return _split_csv(self.cors_allow_methods)

    @property
    def cors_headers(self) -> list[str]:
        return _split_csv(self.cors_allow_headers)

    @property
    def enable_docs(self) -> bool:
        """Interactive API docs are disabled in production deployments."""
        return not self.is_production

    def ensure_directories(self) -> None:
        """Create the writable working directories the service depends on."""
        for path in (
            self.data_dir,
            self.evidence_dir,
            self.index_dir,
            self.temp_dir,
            self.reports_dir,
            self.corpus_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()


def configure_logging(settings: Settings) -> None:
    """Install a single, readable console logging configuration.

    Records carry timestamp, level, logger name and message; request-scoped
    logs add a request id so a single call can be followed end to end.
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {
                    "format": (
                        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
                    ),
                    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "console",
                },
            },
            "root": {"handlers": ["console"], "level": "WARNING"},
            "loggers": {
                "pramaan": {"level": settings.log_level, "propagate": True},
                "uvicorn": {"level": settings.log_level, "propagate": True},
                "uvicorn.error": {"level": settings.log_level, "propagate": True},
                # Our own middleware emits access logs in a consistent format.
                "uvicorn.access": {"level": "WARNING", "propagate": True},
            },
        }
    )
