#!/usr/bin/env python3
"""Thin wrapper for package triage runner."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_repo_intel.run_triage import main


if __name__ == "__main__":
    main()
