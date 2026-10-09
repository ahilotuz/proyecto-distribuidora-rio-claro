"""Tests for manual invoice capture and review."""

from fastapi.testclient import TestClient


def test_invoice_list_shows_empty_state(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "No hay facturas registradas." in response.text


def test_create_invoice_starts_pending_and_shows_detail(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    response = client.post("/invoices", data=valid_invoice_form, follow_redirects=True)

    assert response.status_code == 200
    assert "ABC010203AB1" in response.text
    assert "FAC-001" in response.text
    assert "PENDIENTE_REVISION" in response.text


def test_invoice_appears_in_list_after_creation(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    client.post("/invoices", data=valid_invoice_form)

    response = client.get("/")

    assert response.status_code == 200
    assert "FAC-001" in response.text
    assert "116.00" in response.text


def test_duplicate_supplier_and_folio_is_rejected(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    client.post("/invoices", data=valid_invoice_form)

    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 409
    assert "Ya existe una factura con ese RFC y folio." in response.text


def test_total_must_equal_subtotal_plus_taxes(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    valid_invoice_form["total"] = "115.99"

    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 422
    assert "El total debe ser igual al subtotal más los impuestos." in response.text


def test_negative_amount_is_rejected(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    valid_invoice_form.update(subtotal="-1.00", total="15.00")

    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 422
    assert "El importe no puede ser negativo." in response.text


def test_amount_with_more_than_two_decimal_places_is_rejected(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    valid_invoice_form.update(subtotal="100.001", total="116.001")

    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 422
    assert "El importe admite como máximo dos decimales." in response.text


def test_amount_exceeding_database_precision_is_rejected(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    valid_invoice_form.update(
        subtotal="10000000000.00",
        taxes="0.00",
        total="10000000000.00",
    )

    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 422
    assert "El importe excede el máximo permitido." in response.text


def test_blank_rfc_is_rejected(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    valid_invoice_form["supplier_rfc"] = "   "

    response = client.post("/invoices", data=valid_invoice_form)

    assert response.status_code == 422
    assert "RFC del proveedor" in response.text


def test_review_status_can_be_changed_and_changed_back(
    client: TestClient, valid_invoice_form: dict[str, str]
) -> None:
    created = client.post("/invoices", data=valid_invoice_form, follow_redirects=False)
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
