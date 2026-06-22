import os
import uuid
from io import BytesIO
from pathlib import Path

from flask import current_app, render_template, send_file
from werkzeug.utils import secure_filename

ALLOWED_RECEIPT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}
MAX_RECEIPT_FILE_BYTES = 10 * 1024 * 1024

MIMETYPE_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def allowed_receipt_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_RECEIPT_EXTENSIONS


def _mimetype_for_ext(ext: str) -> str:
    return MIMETYPE_BY_EXT.get(ext.lower(), "application/octet-stream")


def _upload_root(relative_subdir: Path) -> Path:
    return Path(current_app.root_path) / "static" / relative_subdir


def prepare_receipt_upload(file_storage, relative_subdir: Path) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("Receipt file is required.")

    if not allowed_receipt_file(file_storage.filename):
        raise ValueError("Allowed file types: PNG, JPG, JPEG, GIF, WEBP, PDF.")

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_RECEIPT_FILE_BYTES:
        raise ValueError("File is too large. Maximum size is 10 MB.")

    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_bytes = file_storage.read()

    return {
        "relative_path": str(relative_subdir / stored_name).replace("\\", "/"),
        "data": file_bytes,
        "mimetype": _mimetype_for_ext(ext),
    }


def save_receipt_upload(file_storage, relative_subdir: Path) -> dict:
    prepared = prepare_receipt_upload(file_storage, relative_subdir)
    target_dir = _upload_root(relative_subdir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / Path(prepared["relative_path"]).name
    file_path.write_bytes(prepared["data"])
    return prepared


def apply_receipt_file(record, prepared: dict) -> None:
    record.screenshot_filename = prepared["relative_path"]
    record.screenshot_data = prepared["data"]
    record.screenshot_mimetype = prepared["mimetype"]


def delete_receipt_file(relative_path: str) -> None:
    if not relative_path:
        return
    file_path = Path(current_app.root_path) / "static" / relative_path.replace("/", os.sep)
    if file_path.exists() and file_path.is_file():
        file_path.unlink()


def resolve_receipt_file(record, *, backfill=None):
    """Return send_file response, or missing-receipt HTML. Optional backfill(record, data, mimetype)."""
    if record.screenshot_data:
        return send_file(
            BytesIO(record.screenshot_data),
            mimetype=record.screenshot_mimetype or "application/octet-stream",
            download_name=Path(record.screenshot_filename).name,
        )

    if record.screenshot_filename:
        file_path = (
            Path(current_app.root_path) / "static" / record.screenshot_filename.replace("/", os.sep)
        )
        if file_path.exists() and file_path.is_file():
            file_bytes = file_path.read_bytes()
            mimetype = record.screenshot_mimetype or _mimetype_for_ext(
                Path(record.screenshot_filename).suffix.lstrip(".")
            )
            if backfill:
                backfill(record, file_bytes, mimetype)
            return send_file(
                BytesIO(file_bytes),
                mimetype=mimetype,
                download_name=Path(record.screenshot_filename).name,
            )

    return (
        render_template(
            "shared/missing_receipt.html",
            title="Receipt Not Available",
            message="This receipt file is missing. Please edit the record and upload the file again.",
        ),
        404,
    )
