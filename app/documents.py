"""Validation and private local storage for original invoice documents."""

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

logger = logging.getLogger(__name__)
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024
DOCUMENT_SIGNATURES: dict[str, tuple[bytes, str, str]] = {
    ".pdf": (b"%PDF-", "application/pdf", ".pdf"),
    ".jpg": (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    ".jpeg": (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    ".png": (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
}
STORAGE_KEY_PATTERN = re.compile(r"[0-9a-f]{32}\.(?:pdf|jpg|png)\Z")


class DocumentValidationError(ValueError):
    """Raised when an uploaded original document violates capture rules."""


class DocumentStorageError(OSError):
    """Raised when a document cannot be safely written to local storage."""


@dataclass(frozen=True)
class ValidatedDocument:
    """An accepted file with normalized metadata and its bounded contents."""

    original_name: str
    media_type: str
    extension: str
    size_bytes: int
    sha256: str
    contents: bytes


def get_storage_directory() -> Path:
    """Return the configured private document directory."""
    directory = Path(os.getenv("INVOICE_STORAGE_DIR", "var/invoices")).expanduser()
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    return directory


def validate_document(upload: UploadFile) -> ValidatedDocument:
    """Validate file size, extension, and file signature without trusting MIME."""
    filename = upload.filename or ""
    original_name = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
    if not original_name or len(original_name) > 255:
        raise DocumentValidationError("El nombre del archivo no es válido.")

    suffix = Path(original_name).suffix.casefold()
    signature_info = DOCUMENT_SIGNATURES.get(suffix)
    if signature_info is None:
        raise DocumentValidationError("El archivo debe ser PDF, JPG/JPEG o PNG.")

    contents = upload.file.read(MAX_DOCUMENT_SIZE + 1)
    if not contents:
        raise DocumentValidationError("El archivo no puede estar vacío.")
    if len(contents) > MAX_DOCUMENT_SIZE:
        raise DocumentValidationError("El archivo no debe superar 10 MiB.")

    signature, media_type, normalized_extension = signature_info
    if not contents.startswith(signature):
        raise DocumentValidationError(
            "El contenido del archivo no coincide con su formato."
        )

    return ValidatedDocument(
        original_name=original_name,
        media_type=media_type,
        extension=normalized_extension,
        size_bytes=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
        contents=contents,
    )


def store_document(document: ValidatedDocument) -> str:
    """Write an accepted document under a generated key in private storage."""
    directory = get_storage_directory()
    temporary_path: Path | None = None
    destination: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not directory.is_dir():
            raise OSError("El directorio de documentos debe ser un directorio real.")
        directory.chmod(0o700)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".upload-",
            dir=directory,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as stored_file:
            stored_file.write(document.contents)
            stored_file.flush()
            os.fsync(stored_file.fileno())

        storage_key = f"{uuid4().hex}{document.extension}"
        destination = directory / storage_key
        os.replace(temporary_path, destination)
        temporary_path = None
        destination.chmod(0o600)
        destination = None
        return storage_key
    except OSError as error:
        for incomplete_path in (temporary_path, destination):
            if incomplete_path is not None:
                try:
                    incomplete_path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Unable to clean incomplete invoice document")
        logger.exception("Unable to store uploaded invoice document")
        raise DocumentStorageError(
            "No se pudo guardar el documento de la factura."
        ) from error


def resolve_stored_document(storage_key: str) -> Path:
    """Resolve a generated storage key while rejecting unsafe database values."""
    if STORAGE_KEY_PATTERN.fullmatch(storage_key) is None:
        raise ValueError("Invalid invoice document storage key.")
    directory = get_storage_directory()
    path = directory / storage_key
    if path.parent != directory:
        raise ValueError("Invoice document path escaped its storage directory.")
    if path.is_symlink():
        raise ValueError("Invoice document storage key resolves to a symbolic link.")
    return path


def delete_stored_document(storage_key: str) -> None:
    """Remove a generated file after its invoice transaction fails."""
    path = resolve_stored_document(storage_key)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to remove uncommitted invoice document")
        raise
