"""Integration tests for Alembic migrations, upgrade, and downgrade."""

import contextlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


@pytest.mark.integration
def test_alembic_migration_upgrade_and_downgrade() -> None:
    # Use SQLite file-based or in-memory DB for migration testing
    db_file = Path("test_migration.db")
    if db_file.exists():
        db_file.unlink()

    db_url = f"sqlite:///{db_file.resolve()}"
    alembic_ini_path = Path(__file__).resolve().parent.parent.parent / "alembic.ini"

    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    try:
        # 1. Run upgrade head
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(db_url)
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        # Verify all core tables created
        assert "organizations" in tables
        assert "memberships" in tables
        assert "job_operation" in tables
        assert "outbox_event" in tables
        assert "idempotency_record" in tables
        assert "audit_event" in tables

        # 2. Run downgrade base
        command.downgrade(alembic_cfg, "base")

        engine.dispose()
        fresh_engine = create_engine(db_url)
        fresh_inspector = inspect(fresh_engine)
        remaining_tables = fresh_inspector.get_table_names()
        # Verify tables dropped cleanly (only alembic_version remains)
        assert "organizations" not in remaining_tables
        assert "job_operation" not in remaining_tables
        fresh_engine.dispose()

    finally:
        if db_file.exists():
            with contextlib.suppress(Exception):
                db_file.unlink()
