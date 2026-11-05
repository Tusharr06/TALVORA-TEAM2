"""Export OpenAPI Schema from FastAPI Application to contracts/openapi.json."""

import json
import sys
from pathlib import Path

# Add backend/src to path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
sys.path.insert(0, str(backend_src))

from talvora.bootstrap import create_app  # noqa: E402
from talvora.config import Settings  # noqa: E402


def export_openapi() -> None:
    settings = Settings(environment="local", debug=False)
    app = create_app(settings)
    schema = app.openapi()

    output_path = repo_root / "contracts" / "openapi.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    print(f"Successfully exported OpenAPI schema to {output_path}")


if __name__ == "__main__":
    export_openapi()
