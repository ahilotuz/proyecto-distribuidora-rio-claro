"""FastAPI web application for local supplier invoice review."""

from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Invoice, InvoiceStatus
from app.schemas import InvoiceCreate

app = FastAPI(title="Distribuidora Río Claro - Revisión de facturas")
templates = Jinja2Templates(directory="app/templates")


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

    invoice = Invoice(**invoice_data.model_dump())
    db.add(invoice)
    try:
        db.commit()
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
