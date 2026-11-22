"""Validate an uploaded photo and re-encode it without any metadata.

The browser already downscales and re-encodes to JPEG, but the server never
trusts that: the type is checked by magic bytes, the image is decoded with
Pillow, EXIF orientation is applied, and only the pixels are re-encoded as a
fresh JPEG. EXIF (including GPS), XMP, IPTC, ICC profiles and comments are not
carried over.
"""

from __future__ import annotations

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
