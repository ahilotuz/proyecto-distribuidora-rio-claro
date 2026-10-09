"""Database configuration and SQLAlchemy session dependency."""

import os
from collections.abc import Generator
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

load_dotenv()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create and cache the PostgreSQL engine from required environment values."""
    required = (
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing required database configuration: {', '.join(missing)}"
        )

    url = URL.create(
        drivername="postgresql+psycopg",
        username=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"],
        host=os.environ["DATABASE_HOST"],
        port=int(os.environ["DATABASE_PORT"]),
        database=os.environ["DATABASE_NAME"],
    )
    return create_engine(url, pool_pre_ping=True)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it after the request."""
    with Session(get_engine()) as session:
        yield session
