"""FastAPI web application for local supplier invoice review."""

import hashlib
import logging
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.documents import (
    MAX_DOCUMENT_SIZE,
    DocumentStorageError,
    DocumentValidationError,
    delete_stored_document,
    resolve_stored_document,
    store_document,
    validate_document,
)
from app.models import Invoice, InvoiceStatus
from app.schemas import InvoiceCreate

app = FastAPI(title="Distribuidora Río Claro - Revisión de facturas")
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _validation_messages(error: ValidationError) -> list[str]:
    """Translate invoice validation failures into clear Spanish form messages."""
    labels = {
        "supplier_rfc": "RFC del proveedor",
        "folio": "Folio",
        "invoice_date": "Fecha",
        "subtotal": "Subtotal",
        "taxes": "Impuestos",
        "total": "Total",
    }
    messages: list[str] = []
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else ""
        message = str(item["msg"]).removeprefix("Value error, ")
        if item["type"] == "missing":
            message = "El campo es obligatorio."
        messages.append(f"{labels.get(field, 'Factura')}: {message}")
    return messages


@app.get("/", response_class=HTMLResponse, name="invoice_list")
def invoice_list(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> HTMLResponse:
    """Show invoices ordered from newest to oldest."""
    invoices = db.scalars(select(Invoice).order_by(Invoice.created_at.desc())).all()
    return templates.TemplateResponse(
        request=request,
        name="invoices/list.html",
        context={"invoices": invoices},
    )


@app.get("/invoices/new", response_class=HTMLResponse, name="invoice_new")
def invoice_new(request: Request) -> HTMLResponse:
    """Show the manual invoice capture form."""
    return templates.TemplateResponse(
        request=request,
        name="invoices/new.html",
        context={"errors": [], "values": {}},
    )


@app.post("/invoices", response_class=HTMLResponse, name="invoice_create")
def invoice_create(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    supplier_rfc: Annotated[str, Form()],
    folio: Annotated[str, Form()],
    invoice_date: Annotated[str, Form()],
    subtotal: Annotated[str, Form()],
    taxes: Annotated[str, Form()],
    total: Annotated[str, Form()],
    source_file: Annotated[UploadFile, File()],
) -> Response:
    """Validate and persist a supplier invoice for human review."""
    values = {
        "supplier_rfc": supplier_rfc,
        "folio": folio,
        "invoice_date": invoice_date,
        "subtotal": subtotal,
        "taxes": taxes,
        "total": total,
    }
    try:
        invoice_data = InvoiceCreate.model_validate(values)
    except ValidationError as error:
        return templates.TemplateResponse(
            request=request,
            name="invoices/new.html",
            context={"errors": _validation_messages(error), "values": values},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        document = validate_document(source_file)
    except DocumentValidationError as error:
        return templates.TemplateResponse(
            request=request,
            name="invoices/new.html",
            context={"errors": [str(error)], "values": values},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        storage_key = store_document(document)
    except DocumentStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    invoice = Invoice(
        **invoice_data.model_dump(),
        document_key=storage_key,
        document_original_name=document.original_name,
        document_media_type=document.media_type,
        document_size_bytes=document.size_bytes,
        document_sha256=document.sha256,
    )
    committed = False
    try:
        db.add(invoice)
        db.commit()
        committed = True
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(
            select(Invoice.id).where(
                Invoice.supplier_rfc == invoice_data.supplier_rfc,
                Invoice.folio == invoice_data.folio,
            )
        )
        if duplicate is None:
            raise
        return templates.TemplateResponse(
            request=request,
            name="invoices/new.html",
            context={
                "errors": ["Ya existe una factura con ese RFC y folio."],
                "values": values,
            },
            status_code=status.HTTP_409_CONFLICT,
        )
    finally:
        if not committed:
            delete_stored_document(storage_key)

    return RedirectResponse(
        url=app.url_path_for("invoice_detail", invoice_id=invoice.id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get(
    "/invoices/{invoice_id}",
    response_class=HTMLResponse,
    name="invoice_detail",
)
def invoice_detail(
    invoice_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Show one invoice and its review controls."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada."
        )
    return templates.TemplateResponse(
        request=request,
        name="invoices/detail.html",
        context={"invoice": invoice, "statuses": list(InvoiceStatus)},
    )


@app.get(
    "/invoices/{invoice_id}/document",
    name="invoice_document_download",
)
def invoice_document_download(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Download the invoice original as an attachment, never render it inline."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada."
        )
    if invoice.document_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta factura histórica no tiene un documento adjunto.",
        )
    try:
        document_path = resolve_stored_document(invoice.document_key)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La referencia al documento de la factura no es válida.",
        ) from error
    if not document_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró el documento original de la factura.",
        )
    if invoice.document_size_bytes is None or invoice.document_sha256 is None:
        logger.error(
            "Invoice %s has incomplete document integrity metadata", invoice_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Los metadatos de integridad del documento están incompletos.",
        )
    try:
        with document_path.open("rb") as stored_document:
            document_contents = stored_document.read(MAX_DOCUMENT_SIZE + 1)
    except OSError as error:
        logger.exception("Unable to read invoice document %s", invoice_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo leer el documento original de la factura.",
        ) from error
    if (
        len(document_contents) > MAX_DOCUMENT_SIZE
        or len(document_contents) != invoice.document_size_bytes
        or hashlib.sha256(document_contents).hexdigest() != invoice.document_sha256
    ):
        logger.error(
            "Invoice document integrity check failed for invoice %s", invoice_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La integridad del documento original no pudo verificarse.",
        )
    return Response(
        content=document_contents,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="factura-{invoice.id}{document_path.suffix}"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post(
    "/invoices/{invoice_id}/status",
    name="invoice_update_status",
)
def invoice_update_status(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    new_status: Annotated[InvoiceStatus, Form(alias="status")],
) -> RedirectResponse:
    """Change an invoice review status without synchronizing it to an ERP."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada."
        )
    invoice.status = new_status
    db.commit()
    return RedirectResponse(
        url=app.url_path_for("invoice_detail", invoice_id=invoice.id),
        status_code=status.HTTP_303_SEE_OTHER,
    )
