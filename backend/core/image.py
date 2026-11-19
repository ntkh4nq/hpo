"""Images sent to gateway models as base64 data URLs (describe_image, embed_image)."""

import base64
import io
from pathlib import Path

from PIL import Image

CONVERT_TO_PNG = {".gif", ".tif", ".tiff"}  # formats the models do not take directly
MIME = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png"}


def data_url(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in CONVERT_TO_PNG:
        buf = io.BytesIO()
        with Image.open(path) as im:
            im.convert("RGB").save(buf, format="PNG")
        data, mime = buf.getvalue(), "png"
    else:
        data, mime = path.read_bytes(), MIME[suffix]
    return f"data:image/{mime};base64,{base64.b64encode(data).decode()}"
