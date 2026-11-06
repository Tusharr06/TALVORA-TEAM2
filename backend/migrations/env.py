"""Alembic Migration Environment."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

# Import all domain models so that Alembic knows about them
import talvora.modules.applications.domain.models  # noqa: F401
import talvora.modules.audit.domain.models  # noqa: F401
import talvora.modules.candidates.domain.models  # noqa: F401
import talvora.modules.documents.domain.models  # noqa: F401
import talvora.modules.evidence.domain.models  # noqa: F401
import talvora.modules.facts.domain.models  # noqa: F401
import talvora.modules.identity.domain.models  # noqa: F401
import talvora.modules.integrations.domain.models  # noqa: F401
import talvora.modules.jobs.domain.models  # noqa: F401
import talvora.modules.operations.domain.models  # noqa: F401
import talvora.modules.organizations.domain.models  # noqa: F401
import talvora.modules.scheduling.domain.models  # noqa: F401
import talvora.modules.screening.domain.models  # noqa: F401
import talvora.modules.summaries.domain.models  # noqa: F401
from talvora.config import get_settings
from talvora.shared.infrastructure.models import Base

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Inject database URL from settings only if not explicitly provided
if not config.get_main_option("sqlalchemy.url"):
    settings = get_settings()
    db_url = settings.database_url
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    config.set_main_option("sqlalchemy.url", db_url)
else:
    db_url = config.get_main_option("sqlalchemy.url")
    if db_url and db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
