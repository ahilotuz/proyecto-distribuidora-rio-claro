"""Shared isolated fixtures for application tests."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def database_engine(tmp_path: Path) -> Generator[Engine, None, None]:
    """Provide an isolated in-memory SQLite engine for each test."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(database_engine: Engine) -> Generator[Session, None, None]:
    """Provide a direct database session for setup and persistence assertions."""
    with Session(database_engine) as session:
        yield session


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
) -> Generator[TestClient, None, None]:
    """Provide a FastAPI client backed by isolated database and file storage."""
    monkeypatch.setenv("INVOICE_STORAGE_DIR", str(tmp_path / "invoice-documents"))

    def override_get_db() -> Generator[Session, None, None]:
        with Session(database_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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


@pytest.fixture
def valid_invoice_upload() -> tuple[str, bytes, str]:
    """Return a small PDF-like test payload with a valid PDF signature."""
    return (
        "factura-demo.pdf",
        b"%PDF-1.7\ncontenido de prueba\n%%EOF",
        "application/pdf",
    )
