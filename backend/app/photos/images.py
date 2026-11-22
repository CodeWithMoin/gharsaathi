"""Validate an uploaded photo and re-encode it without any metadata.

The browser already downscales and re-encodes to JPEG, but the server never
trusts that: the type is checked by magic bytes, the image is decoded with
Pillow, EXIF orientation is applied, and only the pixels are re-encoded as a
fresh JPEG. EXIF (including GPS), XMP, IPTC, ICC profiles and comments are not
carried over.
"""

from __future__ import annotations

import base64
import binascii
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

try:  # HEIC/HEIF from iPhones; optional at runtime.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on the installed wheels
    HEIF_SUPPORTED = False


MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_LONG_SIDE = 1600
JPEG_QUALITY = 85
# Decoded pixel cap (about 7000 x 7000); larger files are refused, not decoded.
MAX_DECODED_PIXELS = 50_000_000
# Chat-bubble thumbnail kept with the conversation after the full image is discarded.
THUMBNAIL_LONG_SIDE = 320
MAX_THUMBNAIL_BYTES = 40 * 1024
THUMBNAIL_PREFIX = "data:image/jpeg;base64,"
MAX_THUMBNAIL_CHARS = len(THUMBNAIL_PREFIX) + (MAX_THUMBNAIL_BYTES + 2) // 3 * 4
_THUMBNAIL_QUALITIES = (72, 60, 48, 36)
_METADATA_KEYS = {"exif", "icc_profile", "xmp", "XML:com.adobe.xmp", "photoshop", "comment", "adobe", "iptc"}
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


class PhotoRejected(ValueError):
    """The upload is not an acceptable photo. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class CleanPhoto:
    jpeg: bytes
    width: int
    height: int


def sniff_type(data: bytes) -> str | None:
    """The image type from its leading bytes, or None if it is not an accepted type."""

    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "heif"
    return None


def fit_within(width: int, height: int, max_side: int = MAX_LONG_SIDE) -> tuple[int, int]:
    """Scale (width, height) so the longest side is at most ``max_side``; never enlarge."""

    longest = max(width, height)
    if longest <= max_side:
        return width, height
    scale = max_side / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def sanitize_photo(data: bytes) -> CleanPhoto:
    if not data:
        raise PhotoRejected("empty", "The photo is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoRejected("too_large", "The photo is larger than 8 MB.")
    kind = sniff_type(data)
    if kind is None:
        raise PhotoRejected("unsupported_type", "Only JPEG, PNG, WebP or HEIC photos are supported.")
    if kind == "heif" and not HEIF_SUPPORTED:
        raise PhotoRejected("unsupported_type", "HEIC photos are not supported on this server.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.width * probe.height > MAX_DECODED_PIXELS:
                    raise PhotoRejected("too_large", "The photo has too many pixels.")
                probe.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.seek(0)
                upright = ImageOps.exif_transpose(image)
                rgb = upright.convert("RGB")
    except PhotoRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PhotoRejected("corrupt", "The photo could not be read.") from exc
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        raise PhotoRejected("corrupt", "The photo could not be read.") from exc

    size = fit_within(rgb.width, rgb.height)
    if size != rgb.size:
        rgb = rgb.resize(size, Image.Resampling.LANCZOS)
    # A brand-new image from raw pixels: no info dict, EXIF, XMP, ICC or comments.
    clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())
    out = io.BytesIO()
    clean.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return CleanPhoto(jpeg=out.getvalue(), width=clean.width, height=clean.height)


def _pixels_only(image: Image.Image) -> Image.Image:
    return Image.frombytes("RGB", image.size, image.convert("RGB").tobytes())


def make_thumbnail(clean_jpeg: bytes) -> str | None:
    """A <=320 px, <=40 KB JPEG data URL made from an already-sanitised photo.

    Re-encoded from pixels only, so it carries no metadata. None if it cannot be
    made within the cap (the chat then shows the text placeholder only).
    """

    try:
        with Image.open(io.BytesIO(clean_jpeg)) as image:
            image.load()
            small = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError):
        return None
    for side in (THUMBNAIL_LONG_SIDE, 240, 160):
        size = fit_within(small.width, small.height, side)
        resized = _pixels_only(small.resize(size, Image.Resampling.LANCZOS) if size != small.size else small)
        for quality in _THUMBNAIL_QUALITIES:
            out = io.BytesIO()
            resized.save(out, format="JPEG", quality=quality, optimize=True)
            data = out.getvalue()
            if len(data) <= MAX_THUMBNAIL_BYTES:
                return THUMBNAIL_PREFIX + base64.b64encode(data).decode("ascii")
    return None


def validate_thumbnail(value: object) -> str | None:
    """The data URL if it is a small, metadata-free JPEG thumbnail; otherwise None.

    Saved chats come from the browser, so a thumbnail is checked again on every
    save instead of being trusted.
    """

    if not isinstance(value, str) or not value.startswith(THUMBNAIL_PREFIX) or len(value) > MAX_THUMBNAIL_CHARS:
        return None
    try:
        data = base64.b64decode(value[len(THUMBNAIL_PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data or len(data) > MAX_THUMBNAIL_BYTES or sniff_type(data) != "jpeg":
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG" or max(image.size) > THUMBNAIL_LONG_SIDE or min(image.size) < 1:
                    return None
                if _METADATA_KEYS & set(image.info) or image.getexif():
                    return None
                image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, SyntaxError, EOFError):
        return None
    return value
