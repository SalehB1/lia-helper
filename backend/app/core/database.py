"""Async SQLAlchemy engine, session factory and sqlite bootstrap."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.db")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _connection_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Set the per-connection pragmas every session in this app depends on.

    ``foreign_keys`` is what makes ``ondelete="CASCADE"`` real; without it a deleted session
    leaves its conversations behind.

    ``synchronous=NORMAL`` is the durability trade this app actually wants, and it is
    per-connection rather than stored in the file — unlike ``journal_mode`` — so it belongs
    here and nowhere else. SQLite's default is FULL, which fsyncs the WAL on **every**
    commit; the answer in flight is now checkpointed to its row as it streams, so that is
    several fsyncs per turn against a network-backed platform disk. In WAL mode NORMAL
    fsyncs at checkpoints instead, and a committed transaction is already in the OS page
    cache — which outlives the process. So it protects exactly what the checkpoints exist to
    protect: SIGKILL, an OOM kill, gunicorn killing a worker, a redeploy that outruns the
    drain. What it gives up is the last few commits on a kernel panic or a power cut. The
    WAL is never corrupted by this, only shortened.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session that commits on success.

    Yields:
        An open :class:`AsyncSession`. It is committed when the request handler
        returns normally, rolled back on any exception, and always closed.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _add_superuser_flag(conn: Any) -> None:
    """Move a ``users`` table created with a ``role`` string over to the boolean flag.

    Same rules as ``_add_branching_columns`` below: not a migration framework, exactly one
    known column, idempotent, and skipped entirely once the work is done. Two independent
    steps. The backfill reads the old text column so the operator who was already a superuser
    stays one — without it the only account able to manage users would silently be demoted on
    the next boot, and nobody could promote it back. Then the stale ``role`` column goes.

    Dropping it is not cosmetic tidying. ``role VARCHAR(16) NOT NULL`` carries no default and
    is no longer mapped, so every INSERT the ORM emits omits it and trips the NOT NULL
    constraint — which ``UserRepository.create`` catches as an ``IntegrityError`` and reports
    as "this username is taken". On such a database no operator can be created at all, by the
    panel or by registration, and the message points at the wrong thing entirely.

    Args:
        conn: An open async connection inside ``engine.begin()``.
    """
    rows = await conn.execute(text("PRAGMA table_info(users)"))
    columns = {row[1] for row in rows.all()}
    if not columns:
        return

    if "is_superuser" not in columns:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT 0")
        )
        if "role" in columns:
            await conn.execute(
                text("UPDATE users SET is_superuser = 1 WHERE role = 'superuser'")
            )
        logger.info("db_column_added", table="users", column="is_superuser")

    if "role" in columns:
        # Needs SQLite >= 3.35. A failure leaves a database on which no account can be
        # created, so it is logged at error level rather than swallowed — but it must not
        # stop the app booting, because chat and the existing accounts still work.
        try:
            await conn.execute(text("ALTER TABLE users DROP COLUMN role"))
            logger.info("db_column_dropped", table="users", column="role")
        except Exception as exc:
            logger.error(
                "db_column_drop_failed",
                table="users",
                column="role",
                error=type(exc).__name__,
            )


async def _add_session_user_column(conn: Any) -> None:
    """Give every session an owner, and delete the ones that never had one.

    Same rules as the helpers around it: one known column, idempotent, skipped entirely once
    the column exists.

    The purge is the destructive half and it lives inside the ALTER branch on purpose — run
    unconditionally it would eat live data on every restart. A session with no ``user_id``
    predates accounts: nobody can ever sign in as it, so its conversations are unreachable
    rows that would be kept forever. Deleting the session cascades to ``conversations`` and on
    to ``messages`` — both declare ``ON DELETE CASCADE`` and ``PRAGMA foreign_keys=ON`` comes
    from the connect event, so it applies to this connection too.

    Args:
        conn: An open async connection inside ``engine.begin()``.
    """
    rows = await conn.execute(text("PRAGMA table_info(sessions)"))
    columns = {row[1] for row in rows.all()}
    if not columns or "user_id" in columns:
        return
    await conn.execute(
        text(
            "ALTER TABLE sessions ADD COLUMN user_id INTEGER "
            "REFERENCES users(id) ON DELETE CASCADE"
        )
    )
    purged = await conn.execute(text("DELETE FROM sessions WHERE user_id IS NULL"))
    # After the purge, never before: the index is UNIQUE and the rows being dropped all share
    # a NULL user_id — harmless for UNIQUE, but building it over doomed rows is wasted work.
    await conn.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions (user_id)")
    )
    logger.info(
        "db_column_added", table="sessions", column="user_id", sessions_purged=purged.rowcount
    )


async def _add_branching_columns(conn: Any) -> None:
    """Add the two message-tree columns to a database created before branching existed.

    This is NOT a migration framework and must not grow into one: ``create_all`` creates
    new tables complete, but never alters an existing one, and there is no migration tool
    in this project. Exactly two known columns are added when absent, each followed by a
    one-shot backfill that reads the old linear history as a chain — otherwise every
    pre-existing conversation would render as a single message. Both steps are skipped
    entirely once the columns exist, so this is idempotent and costs one PRAGMA per boot.

    Args:
        conn: An open async connection inside ``engine.begin()``.
    """

    async def columns(table: str) -> set[str]:
        rows = await conn.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in rows.all()}

    if "parent_id" not in await columns("messages"):
        await conn.execute(
            text(
                "ALTER TABLE messages ADD COLUMN parent_id INTEGER "
                "REFERENCES messages(id) ON DELETE SET NULL"
            )
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_messages_parent_id ON messages (parent_id)")
        )
        await conn.execute(
            text(
                "UPDATE messages SET parent_id = ("
                "  SELECT MAX(prev.id) FROM messages AS prev"
                "  WHERE prev.conversation_id = messages.conversation_id"
                "    AND prev.id < messages.id)"
            )
        )
        logger.info("db_column_added", table="messages", column="parent_id")

    if "active_message_id" not in await columns("conversations"):
        await conn.execute(text("ALTER TABLE conversations ADD COLUMN active_message_id INTEGER"))
        await conn.execute(
            text(
                "UPDATE conversations SET active_message_id = ("
                "  SELECT MAX(m.id) FROM messages AS m"
                "  WHERE m.conversation_id = conversations.id)"
            )
        )
        logger.info("db_column_added", table="conversations", column="active_message_id")


async def init_db() -> None:
    """Create the sqlite file, its parent directory and every table.

    Also switches the database to WAL journalling, which keeps reads from
    blocking the single writer, and tops up the two branching columns on a
    database that predates them.
    """
    db_file = settings.database_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    # Imported here (not at module import) so the models package can import
    # nothing from core without creating a cycle, while still registering tables.
    from app.domain import models  # noqa: F401
    from app.domain.models.base import Base

    async with engine.begin() as conn:
        # SQLite silently keeps the old journal mode on a filesystem without shared-memory
        # locking, and Liara disks are network-backed — so report what was actually granted
        # rather than what was asked for.
        journal = await conn.execute(text("PRAGMA journal_mode=WAL"))
        journal_mode = journal.scalar()
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
        await _add_branching_columns(conn)
        await _add_superuser_flag(conn)
        await _add_session_user_column(conn)
    logger.info("db_ready", tables=len(Base.metadata.tables), journal_mode=journal_mode)


async def db_healthy() -> bool:
    """Return whether the database answers a trivial query."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # Health checks poll often: log compactly, never raise.
        logger.warning("db_unhealthy", error=type(exc).__name__)
        return False
