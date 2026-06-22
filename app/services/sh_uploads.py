import os
import uuid
from io import BytesIO
from pathlib import Path

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from app.services.receipt_uploads import (
    apply_receipt_file,
    delete_receipt_file,
    prepare_receipt_upload,
    resolve_receipt_file,
    save_receipt_upload,
)

UPLOAD_SUBDIR = Path("uploads") / "sh_traders" / "payments"

prepare_payment_screenshot = lambda f: prepare_receipt_upload(f, UPLOAD_SUBDIR)
save_payment_screenshot = lambda f: save_receipt_upload(f, UPLOAD_SUBDIR)
apply_payment_screenshot = apply_receipt_file
delete_payment_screenshot = delete_receipt_file


def payment_screenshot_view_url(record_id: int) -> str:
    return url_for("sh_main.view_payment_screenshot", record_id=record_id)


def resolve_payment_screenshot_file(record, backfill_fn=None):
    return resolve_receipt_file(record, backfill=backfill_fn)
