from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "rtsp_assist_gateway"
sys.path.insert(0, str(ADDON))
