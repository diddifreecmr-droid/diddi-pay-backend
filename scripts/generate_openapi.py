"""Generate the versioned OpenAPI snapshot from the FastAPI application."""

from __future__ import annotations

import json
from pathlib import Path

from payfund_app.main import app


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "api" / "openapi.json"


def main() -> None:
    TARGET.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {TARGET}")


if __name__ == "__main__":
    main()
