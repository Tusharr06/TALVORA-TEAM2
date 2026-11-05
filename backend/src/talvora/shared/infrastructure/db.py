"""Database Infrastructure & Session Factory.

Provides SQLAlchemy 2.0 connection lifecycle management, synchronous and asynchronous
session factories, health verification, and graceful pool disposal.
"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from talvora.config import Settings
from talvora.shared.application.ports import HealthChecker


class DatabaseManager:
    """Manages SQLAlchemy async and sync engines and session factories."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._async_engine: AsyncEngine | None = None
        self._async_session_maker: async_sessionmaker[AsyncSession] | None = None
        self._sync_engine: Engine | None = None
        self._sync_session_maker: sessionmaker[Session] | None = None

    @property
    def sync_engine(self) -> Engine:
        if self._sync_engine is None:
            db_url = self.settings.database_url
            if "+aiosqlite" in db_url:
                db_url = db_url.replace("+aiosqlite", "")

            kwargs: dict[str, Any] = {
                "pool_pre_ping": True,
                "echo": self.settings.debug,
            }
            if not db_url.startswith("sqlite"):
                kwargs["pool_size"] = self.settings.db_pool_size
                kwargs["max_overflow"] = self.settings.db_max_overflow
                kwargs["pool_timeout"] = self.settings.db_timeout_seconds
            else:
                kwargs["execution_options"] = {"schema_translate_map": {"identity": None}}

            self._sync_engine = create_engine(db_url, **kwargs)
            self._sync_session_maker = sessionmaker(
                bind=self._sync_engine,
                expire_on_commit=False,
                autocommit=False,
                autoflush=False,
            )
        return self._sync_engine

    @property
    def session_maker(self) -> sessionmaker[Session]:
        if self._sync_session_maker is None:
            _ = self.sync_engine
            assert self._sync_session_maker is not None
        return self._sync_session_maker

    @property
    def async_engine(self) -> AsyncEngine:
        if self._async_engine is None:
            db_url = self.settings.database_url
            if db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

            kwargs: dict[str, Any] = {
                "pool_pre_ping": True,
                "echo": self.settings.debug,
            }
            if not db_url.startswith("sqlite"):
                kwargs["pool_size"] = self.settings.db_pool_size
                kwargs["max_overflow"] = self.settings.db_max_overflow
                kwargs["pool_timeout"] = self.settings.db_timeout_seconds
            else:
                kwargs["execution_options"] = {"schema_translate_map": {"identity": None}}

            self._async_engine = create_async_engine(db_url, **kwargs)
            self._async_session_maker = async_sessionmaker(
                bind=self._async_engine,
                expire_on_commit=False,
                autocommit=False,
                autoflush=False,
            )
        return self._async_engine

    @property
    def async_session_maker(self) -> async_sessionmaker[AsyncSession]:
        if self._async_session_maker is None:
            _ = self.async_engine
            assert self._async_session_maker is not None
        return self._async_session_maker

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        """Provide an asynchronous transactional session."""
        session: AsyncSession = self.async_session_maker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """Gracefully dispose connection pools on application shutdown."""
        if self._async_engine is not None:
            await self._async_engine.dispose()
            self._async_engine = None
            self._async_session_maker = None
        if self._sync_engine is not None:
            self._sync_engine.dispose()
            self._sync_engine = None
            self._sync_session_maker = None


class DatabaseHealthChecker(HealthChecker):
    """Verifies relational database connectivity via bounded SELECT 1 ping."""

    name: str = "database"

    def __init__(self, db_manager: DatabaseManager, timeout: float = 3.0) -> None:
        self.db_manager = db_manager
        self.timeout = timeout

    async def check(self) -> tuple[bool, str, float]:
        start = time.perf_counter()
        try:
            async with self.db_manager.async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            latency = time.perf_counter() - start
            return True, "connected", latency
        except Exception as exc:
            latency = time.perf_counter() - start
            return False, f"connection_failed: {exc.__class__.__name__}", latency
