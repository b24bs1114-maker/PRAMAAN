"""PRAMAAN multi-modal detector package.

``DetectorService`` is exported lazily. It constructs all three modality
detectors, so importing it eagerly here made ``import pramaan.anything`` pull in
torch, transformers, librosa and OpenCV -- even for a submodule that needs none
of them. ``schema`` is dependency-free and stays eager.
"""

from typing import TYPE_CHECKING, Any

from pramaan.schema import DetectionResult, make_result

__all__ = ["DetectorService", "DetectionResult", "make_result"]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pramaan.service import DetectorService


def __getattr__(name: str) -> Any:
    if name == "DetectorService":
        import importlib

        return importlib.import_module("pramaan.service").DetectorService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
