"""Tests for manual invoice capture and review."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice


def test_invoice_list_shows_empty_state(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "No hay facturas registradas." in response.text


def test_create_invoice_starts_pending_and_shows_detail(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "ABC010203AB1" in response.text
    assert "FAC-001" in response.text
    assert "PENDIENTE_REVISION" in response.text


def test_invoice_appears_in_list_after_creation(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "FAC-001" in response.text
    assert "116.00" in response.text


def test_duplicate_supplier_and_folio_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
    tmp_path: Path,
) -> None:
    client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    assert response.status_code == 409
    assert "Ya existe una factura con ese RFC y folio." in response.text
    assert len(list((tmp_path / "invoice-documents").iterdir())) == 1


def test_total_must_equal_subtotal_plus_taxes(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    valid_invoice_form["total"] = "115.99"

    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    assert response.status_code == 422
    assert "El total debe ser igual al subtotal más los impuestos." in response.text


def test_negative_amount_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    valid_invoice_form.update(subtotal="-1.00", total="15.00")

    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    assert response.status_code == 422
    assert "El importe no puede ser negativo." in response.text


def test_amount_with_more_than_two_decimal_places_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    valid_invoice_form.update(subtotal="100.001", total="116.001")

    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    assert response.status_code == 422
    assert "El importe admite como máximo dos decimales." in response.text


def test_amount_exceeding_database_precision_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    valid_invoice_form.update(
        subtotal="10000000000.00",
        taxes="0.00",
        total="10000000000.00",
    )

    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    assert response.status_code == 422
    assert "El importe excede el máximo permitido." in response.text


def test_blank_rfc_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    valid_invoice_form["supplier_rfc"] = "   "

    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
    )

    assert response.status_code == 422
    assert "RFC del proveedor" in response.text


def test_review_status_can_be_changed_and_changed_back(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
) -> None:
    created = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
        follow_redirects=False,
    )
    detail_url = created.headers["location"]

    validated = client.post(
        f"{detail_url}/status",
        data={"status": "VALIDADA"},
        follow_redirects=True,
    )
    assert "VALIDADA" in validated.text

    pending = client.post(
        f"{detail_url}/status",
        data={"status": "PENDIENTE_REVISION"},
        follow_redirects=True,
    )
    assert "PENDIENTE_REVISION" in pending.text


def test_unknown_invoice_returns_not_found(client: TestClient) -> None:
    response = client.get("/invoices/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Factura no encontrada."}


def test_invoice_document_is_downloaded_as_attachment(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    created = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
        follow_redirects=False,
    )
    invoice_id = created.headers["location"].rsplit("/", maxsplit=1)[-1]

    detail = client.get(f"/invoices/{invoice_id}")
    download = client.get(f"/invoices/{invoice_id}/document")

    assert "factura-demo.pdf" in detail.text
    assert "Descargar" in detail.text
    assert download.status_code == 200
    assert download.content == valid_invoice_upload[1]
    assert download.headers["content-disposition"] == (
        f'attachment; filename="factura-{invoice_id}.pdf"'
    )
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-type"] == "application/octet-stream"

    invoice = db_session.scalar(select(Invoice))
    saved_files = list((tmp_path / "invoice-documents").iterdir())
    assert invoice is not None
    assert invoice.document_original_name == "factura-demo.pdf"
    assert invoice.document_media_type == "application/pdf"
    assert invoice.document_size_bytes == len(valid_invoice_upload[1])
    assert invoice.document_sha256 is not None
    assert len(saved_files) == 1
    assert saved_files[0].name == invoice.document_key
    assert saved_files[0].name != "factura-demo.pdf"
    assert (tmp_path / "invoice-documents").stat().st_mode & 0o777 == 0o700
    assert saved_files[0].stat().st_mode & 0o777 == 0o600


def test_missing_document_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 422


def test_unsupported_extension_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": ("factura.txt", b"%PDF-1.7", "application/pdf")},
    )

    assert response.status_code == 422
    assert "PDF, JPG/JPEG o PNG" in response.text


def test_signature_must_match_extension(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": ("factura.pdf", b"\x89PNG\r\n\x1a\n", "application/pdf")},
    )

    assert response.status_code == 422
    assert "no coincide con su formato" in response.text


def test_oversized_document_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    from app.documents import MAX_DOCUMENT_SIZE

    contents = b"%PDF-" + b"0" * (MAX_DOCUMENT_SIZE - 4)
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": ("factura.pdf", contents, "application/pdf")},
    )

    assert response.status_code == 422
    assert "no debe superar 10 MiB" in response.text


def test_document_at_exact_size_limit_is_accepted(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    from app.documents import MAX_DOCUMENT_SIZE

    contents = b"%PDF-" + b"0" * (MAX_DOCUMENT_SIZE - 5)
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": ("factura.pdf", contents, "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_empty_document_is_rejected(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": ("factura.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 422
    assert "no puede estar vacío" in response.text


def test_supported_image_and_pdf_signatures(
    client: TestClient,
    valid_invoice_form: dict[str, str],
) -> None:
    supported_uploads = [
        ("factura.pdf", b"%PDF-1.7 archivo", "application/pdf"),
        ("factura.jpg", b"\xff\xd8\xff archivo", "image/jpeg"),
        ("factura.jpeg", b"\xff\xd8\xff archivo", "image/jpeg"),
        ("factura.png", b"\x89PNG\r\n\x1a\narchivo", "image/png"),
    ]

    for index, upload in enumerate(supported_uploads):
        form = {**valid_invoice_form, "folio": f"FAC-{index}"}
        response = client.post(
            "/invoices",
            data=form,
            files={"source_file": upload},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_historical_invoice_without_document_remains_available(
    client: TestClient,
    db_session: Session,
) -> None:
    invoice = Invoice(
        supplier_rfc="HIST010101AB1",
        folio="HIST-001",
        invoice_date=date(2026, 9, 1),
        subtotal=Decimal("100.00"),
        taxes=Decimal("16.00"),
        total=Decimal("116.00"),
    )
    db_session.add(invoice)
    db_session.commit()

    detail = client.get(f"/invoices/{invoice.id}")
    download = client.get(f"/invoices/{invoice.id}/document")

    assert detail.status_code == 200
    assert "no tiene un archivo adjunto" in detail.text
    assert download.status_code == 404
    assert download.json()["detail"] == (
        "Esta factura histórica no tiene un documento adjunto."
    )


def test_invalid_document_storage_key_is_not_downloaded(
    client: TestClient,
    db_session: Session,
) -> None:
    invoice = Invoice(
        supplier_rfc="PATH010101AB1",
        folio="PATH-001",
        invoice_date=date(2026, 9, 1),
        subtotal=Decimal("100.00"),
        taxes=Decimal("16.00"),
        total=Decimal("116.00"),
        document_key="../../README.md",
        document_original_name="outside.pdf",
        document_media_type="application/pdf",
        document_size_bytes=16,
        document_sha256="0" * 64,
    )
    db_session.add(invoice)
    db_session.commit()

    response = client.get(f"/invoices/{invoice.id}/document")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "La referencia al documento de la factura no es válida."
    }


def test_file_signature_is_used_instead_of_client_mime_type(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    db_session: Session,
) -> None:
    response = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={
            "source_file": (
                "factura.png",
                b"\x89PNG\r\n\x1a\ncontenido",
                "application/pdf",
            )
        },
        follow_redirects=False,
    )

    invoice = db_session.scalar(select(Invoice))
    assert response.status_code == 303
    assert invoice is not None
    assert invoice.document_media_type == "image/png"


def test_corrupted_stored_file_is_not_downloaded(
    client: TestClient,
    valid_invoice_form: dict[str, str],
    valid_invoice_upload: tuple[str, bytes, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    created = client.post(
        "/invoices",
        data=valid_invoice_form,
        files={"source_file": valid_invoice_upload},
        follow_redirects=False,
    )
    invoice = db_session.scalar(select(Invoice))
    assert invoice is not None
    assert invoice.document_key is not None
    stored_path = tmp_path / "invoice-documents" / invoice.document_key
    stored_path.write_bytes(b"tampered")

    invoice_id = created.headers["location"].rsplit("/", maxsplit=1)[-1]
    response = client.get(f"/invoices/{invoice_id}/document")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "La integridad del documento original no pudo verificarse."
    }
