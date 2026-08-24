"""
conftest.py — makes the repo root importable without pip install.
pytest picks this up automatically.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
