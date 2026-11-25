#!/usr/bin/env python3
"""Run the standalone membership learner from a repository checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fuzzyresf.membership_training import main

if __name__ == "__main__":
    raise SystemExit(main())
