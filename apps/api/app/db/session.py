"""Async engine, session factory, and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def commit_before_response(session: AsyncSession = Depends(get_session)) -> AsyncIterator[None]:
    """Commit the request's work BEFORE the response is sent.

    `get_session` is a request-scoped yield dependency, and FastAPI runs those after the response has gone
    out. Its own commit therefore races the client's next call: signup answered 200 with a token, the client
    called /v1/orgs at once, and when the commit was slow (a loaded runner, this host's disk) the user did not
    exist yet, so the valid token got `401 auth.invalid_token`. Registered app-wide as a *function*-scoped
    dependency, this exits right after the endpoint returns and before the response is sent. An endpoint that
    raises skips the commit (the exception is thrown in at the `yield`) and `get_session` rolls back as before.
    `get_session` keeps its own commit for work done later in the request (streaming responses).
    """
    yield
    await session.commit()
