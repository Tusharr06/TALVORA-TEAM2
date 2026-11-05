"""Cross-platform Task Runner for Talvora Development."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"


def run_command(cmd: list[str], cwd: Path = BACKEND_DIR) -> int:
    print(f"\n[Talvora Runner] Running: {' '.join(cmd)} in {cwd}")
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def lint_backend() -> int:
    rc = run_command(["uv", "run", "ruff", "check", "src/", "tests/"])
    if rc != 0:
        return rc
    return run_command(["uv", "run", "ruff", "format", "--check", "src/", "tests/"])


def typecheck_backend() -> int:
    return run_command(["uv", "run", "mypy", "src/", "tests/"])


def test_unit() -> int:
    return run_command(["uv", "run", "pytest", "-m", "not integration and not live", "-v"])


def test_integration() -> int:
    return run_command(["uv", "run", "pytest", "-m", "integration", "-v"])


def check_backend() -> int:
    for task_name, fn in [
        ("Linting", lint_backend),
        ("Type Checking", typecheck_backend),
        ("Unit Tests", test_unit),
        ("Integration Tests", test_integration),
    ]:
        print(f"\n=================== {task_name} ===================")
        rc = fn()
        if rc != 0:
            print(f"FAILED: {task_name} failed with code {rc}")
            return rc
    print("\nALL BACKEND CHECKS PASSED.")
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/dev.py [check-backend|lint|typecheck|test-unit|test-integration|openapi|dev]")
        sys.exit(1)

    target = sys.argv[1].lower()
    if target in ("check", "check-backend"):
        sys.exit(check_backend())
    elif target in ("lint", "lint-backend"):
        sys.exit(lint_backend())
    elif target in ("typecheck", "typecheck-backend"):
        sys.exit(typecheck_backend())
    elif target in ("test-unit", "unit"):
        sys.exit(test_unit())
    elif target in ("test-integration", "integration"):
        sys.exit(test_integration())
    elif target == "openapi":
        from export_openapi import export_openapi
        export_openapi()
    elif target == "dev":
        sys.exit(run_command(["uv", "run", "uvicorn", "talvora.main:app", "--reload", "--port", "8000"]))
    else:
        print(f"Unknown target: {target}")
        sys.exit(1)


if __name__ == "__main__":
    main()
