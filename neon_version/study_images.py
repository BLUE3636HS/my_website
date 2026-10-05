"""Validation, normalization, and safe paths for template submission images."""
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_SIDE = 2400
ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png"}
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png"}


def normalize_study_image(upload):
    """Return normalized bytes, format, dimensions, suffix, and original basename."""
    original = Path((upload.filename or "").replace("\\", "/")).name
    if Path(original).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("JPEGまたはPNG画像を選択してください。")
    upload.file.seek(0)
    contents = upload.file.read(MAX_IMAGE_BYTES + 1)
    upload.file.seek(0)
    if not contents:
        raise ValueError("画像ファイルが空です。")
    if len(contents) > MAX_IMAGE_BYTES:
        raise ValueError("画像は1枚5MB以下にしてください。")
    try:
        with Image.open(io.BytesIO(contents)) as candidate:
            image_format = candidate.format
            candidate.verify()
        if image_format not in ALLOWED_FORMATS:
            raise ValueError("JPEGまたはPNG画像を選択してください。")
        with Image.open(io.BytesIO(contents)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("画像の解像度が大きすぎます。")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            if image_format == "JPEG":
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(output, "JPEG", quality=88, optimize=True)
            else:
                if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    image = image.convert("RGBA")
                image.save(output, "PNG", optimize=True)
            width, height = image.size
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("正常なJPEGまたはPNG画像を選択してください。") from exc
    return output.getvalue(), image_format, width, height, ALLOWED_FORMATS[image_format], original


def study_image_path(root, stored_name):
    directory = Path(root).resolve()
    target = (directory / stored_name).resolve()
    if target.parent != directory or target.suffix.lower() not in {".jpg", ".png"}:
        raise ValueError("Invalid study image path")
    return target
