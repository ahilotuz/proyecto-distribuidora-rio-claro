"""Shared isolated fixtures for application tests."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provide a FastAPI client backed by a fresh in-memory SQLite database."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_db() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def valid_invoice_form() -> dict[str, str]:
    """Return valid invoice form data for request tests."""
    return {
        "supplier_rfc": "ABC010203AB1",
        "folio": "FAC-001",
        "invoice_date": "2026-10-01",
        "subtotal": "100.00",
        "taxes": "16.00",
        "total": "116.00",
    }
