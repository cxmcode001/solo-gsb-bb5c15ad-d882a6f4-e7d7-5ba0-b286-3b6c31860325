"""Application configuration.

Everything is optional: the service starts with sensible defaults and only
needs SQLite (bundled with Python).
"""
from __future__ import annotations

import os

# SQLite database file. The parent directory is created on startup.
DB_PATH = os.getenv("DB_PATH", os.path.join(os.getcwd(), "data", "cracks.db"))

# Allowed severity grade range (1 = minor ... 4 = severe by convention).
GRADE_MIN = int(os.getenv("GRADE_MIN", "1"))
GRADE_MAX = int(os.getenv("GRADE_MAX", "4"))

# Floating point tolerance for radial / clock comparisons.
EPS = 1e-9

# A full clock face.
CLOCK_HOURS = 12.0
