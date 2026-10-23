"""Keep the committed OpenAPI snapshot synchronized with the executable API."""

import json
from pathlib import Path

from payfund_app.main import app


def test_openapi_snapshot_matches_application() -> None:
    snapshot_path = Path(__file__).parents[1] / "docs" / "api" / "openapi.json"
    assert snapshot_path.exists(), "Run python scripts/generate_openapi.py"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot == app.openapi()
