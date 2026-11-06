"""Export or Verify Frozen OpenAPI Schema at contracts/openapi.json."""

import argparse
import json
import sys
from pathlib import Path

# Add backend/src to path
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_SRC = ROOT_DIR / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

from talvora.bootstrap import create_app  # noqa: E402


def export_or_check_openapi(check_only: bool = False) -> int:
    """Exports deterministic OpenAPI schema or checks for drift."""
    app = create_app()
    schema = app.openapi()

    contracts_dir = ROOT_DIR / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    output_path = contracts_dir / "openapi.json"
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    if check_only:
        if not output_path.exists():
            sys.stderr.write(f"ERROR: {output_path} does not exist.\n")
            return 1
        current = output_path.read_text(encoding="utf-8")
        if current != rendered:
            sys.stderr.write(
                f"ERROR: {output_path} is out of sync with live FastAPI OpenAPI schema.\n"
            )
            return 1
        sys.stdout.write(f"Verified frozen OpenAPI schema at {output_path} (zero drift).\n")
        return 0

    output_path.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"Successfully exported OpenAPI schema to {output_path}\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or verify contracts/openapi.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify contracts/openapi.json matches live FastAPI schema without writing.",
    )
    args = parser.parse_args()
    raise SystemExit(export_or_check_openapi(check_only=args.check))


if __name__ == "__main__":
    main()
