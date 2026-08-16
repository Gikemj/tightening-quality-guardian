"""Start the local TorqueGuard administrator API and desktop console."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from torque_guard.admin_api import serve  # noqa: E402


if __name__ == "__main__":
    serve()
