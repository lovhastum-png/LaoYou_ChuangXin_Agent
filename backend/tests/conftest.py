from __future__ import annotations

import sys
import os
from pathlib import Path


# Tests are documented/run from the workspace root (`pytest backend/tests`).
# Put the backend package itself on the path so `app.*` imports match uvicorn.
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Import-only tests use no database connection. Keep app imports deterministic
# when a developer runs the pure suite before exporting the real DATABASE_URL.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1:55432/laoyou_test"
)
