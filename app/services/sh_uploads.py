import os
import uuid
from io import BytesIO
from pathlib import Path

from flask import current_app, send_file, url_for
from werkzeug.utils import secure_filename

ALLOWED_PAYMENT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}
MAX_PAYMENT_FILE_BYTES = 10 * 1024 * 1024

UPLOAD_SUBDIR = Path("uploads") / "sh_traders" / "payments"

MIMETYPE_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def _upload_root() -> Path:
    return Path(current_app.root_path) / "static" / UPLOAD_SUBDIR


def allowed_payment_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_PAYMENT_EXTENSIONS


def _mimetype_for_ext(ext: str) -> str:
    return MIMETYPE_BY_EXT.get(ext.lower(), "application/octet-stream")


def prepare_payment_screenshot(file_storage) -> dict:
    """Validate upload and return path, bytes, and mimetype for DB + disk storage."""
    if not file_storage or not file_storage.filename:
        raise ValueError("Screenshot file is required.")

    if not allowed_payment_file(file_storage.filename):
        raise ValueError("Allowed file types: PNG, JPG, JPEG, GIF, WEBP, PDF.")

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_PAYMENT_FILE_BYTES:
        raise ValueError("File is too large. Maximum size is 10 MB.")

    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_bytes = file_storage.read()

    return {
        "relative_path": str(UPLOAD_SUBDIR / stored_name).replace("\\", "/"),
        "data": file_bytes,
        "mimetype": _mimetype_for_ext(ext),
    }


def save_payment_screenshot(file_storage) -> dict:
    prepared = prepare_payment_screenshot(file_storage)
    target_dir = _upload_root()
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / Path(prepared["relative_path"]).name
    file_path.write_bytes(prepared["data"])
    return prepared


def apply_payment_screenshot(record, prepared: dict) -> None:
    record.screenshot_filename = prepared["relative_path"]
    record.screenshot_data = prepared["data"]
    record.screenshot_mimetype = prepared["mimetype"]


def delete_payment_screenshot(relative_path: str) -> None:
    if not relative_path:
        return
    file_path = Path(current_app.root_path) / "static" / relative_path.replace("/", os.sep)
    if file_path.exists() and file_path.is_file():
        file_path.unlink()


def payment_screenshot_view_url(record_id: int) -> str:
    return url_for("sh_main.view_payment_screenshot", record_id=record_id)


def resolve_payment_screenshot_file(record):
    """Return a Flask response for the screenshot, from DB or disk."""
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
            return send_file(file_path, mimetype=record.screenshot_mimetype or None)

    return None
