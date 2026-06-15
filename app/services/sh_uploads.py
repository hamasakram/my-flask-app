import os
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_PAYMENT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}
MAX_PAYMENT_FILE_BYTES = 10 * 1024 * 1024

UPLOAD_SUBDIR = Path("uploads") / "sh_traders" / "payments"


def _upload_root() -> Path:
    return Path(current_app.root_path) / "static" / UPLOAD_SUBDIR


def allowed_payment_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_PAYMENT_EXTENSIONS


def save_payment_screenshot(file_storage) -> str:
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

    target_dir = _upload_root()
    target_dir.mkdir(parents=True, exist_ok=True)
    file_storage.save(target_dir / stored_name)

    return str(UPLOAD_SUBDIR / stored_name).replace("\\", "/")


def delete_payment_screenshot(relative_path: str) -> None:
    if not relative_path:
        return
    file_path = Path(current_app.root_path) / "static" / relative_path.replace("/", os.sep)
    if file_path.exists() and file_path.is_file():
        file_path.unlink()


def payment_screenshot_url(relative_path: str) -> str:
    return relative_path.replace("\\", "/")
