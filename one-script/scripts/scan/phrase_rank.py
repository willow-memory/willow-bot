#!/usr/bin/env python3
"""phrase_rank: the pile's rank at a terminal. The code is `onescript.pile`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import pile  # noqa: E402

if __name__ == "__main__":
    sys.exit(pile.main())
