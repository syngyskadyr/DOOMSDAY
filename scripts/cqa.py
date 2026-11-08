"""Existing CQA CLI with the same private .env loading as the backend."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.environment import load_environment
from cqa_eval.cli import main

if __name__ == "__main__":
    load_environment()
    raise SystemExit(main())
