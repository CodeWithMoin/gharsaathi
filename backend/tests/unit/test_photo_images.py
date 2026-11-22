"""Photo upload sanitising: type by magic bytes, size cap, corrupt files, metadata stripped."""

import io

import pytest
from PIL import Image

from app.photos.images import (
    MAX_LONG_SIDE,
    MAX_UPLOAD_BYTES,
    PhotoRejected,
    fit_within,
    sanitize_photo,
    sniff_type,
)

GPS_IFD = 0x8825
ORIENTATION = 0x0112


def jpeg_with_gps(size: tuple[int, int] = (64, 48), orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, (200, 120, 40))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x0131] = "SecretCamApp 1.0"  # Software
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (12.0, 58.0, 10.0), 3: "E", 4: (77.0, 35.0, 40.0)})
    if orientation is not None:
        exif[ORIENTATION] = orientation
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes(), comment=b"home kitchen")
    return out.getvalue()


def encoded(fmt: str, size: tuple[int, int] = (40, 30), **kwargs) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 200, 90)).save(out, format=fmt, **kwargs)
    return out.getvalue()


def test_gps_exif_and_comments_are_gone_after_sanitising() -> None:
    raw = jpeg_with_gps()
    with Image.open(io.BytesIO(raw)) as before:
        assert before.getexif().get_ifd(GPS_IFD)  # the fixture really carries GPS

    clean = sanitize_photo(raw)

    with Image.open(io.BytesIO(clean.jpeg)) as after:
        assert after.format == "JPEG"
        assert len(after.getexif()) == 0
        assert not after.getexif().get_ifd(GPS_IFD)
        for key in ("exif", "xmp", "icc_profile", "comment", "photoshop"):
            assert key not in after.info
    for marker in (b"Exif", b"PhoneMaker", b"SecretCamApp", b"home kitchen", b"http://ns.adobe.com/xap"):
        assert marker not in clean.jpeg


def test_xmp_and_icc_are_not_carried_over_from_png() -> None:
    raw = encoded("PNG", icc_profile=b"\x00" * 128, pnginfo=None)
    clean = sanitize_photo(raw)
    with Image.open(io.BytesIO(clean.jpeg)) as after:
        assert "icc_profile" not in after.info


def test_exif_orientation_is_applied_before_it_is_dropped() -> None:
    # Orientation 6 = rotate 90° clockwise to display; a 64x48 file shows as 48x64.
    clean = sanitize_photo(jpeg_with_gps((64, 48), orientation=6))
    assert (clean.width, clean.height) == (48, 64)


def test_large_photo_is_downscaled_to_the_long_side_cap() -> None:
    clean = sanitize_photo(encoded("PNG", (3200, 1200)))
    assert (clean.width, clean.height) == (MAX_LONG_SIDE, 600)


@pytest.mark.parametrize(
    ("size", "expected"),
    [((4000, 3000), (1600, 1200)), ((1200, 1600), (1200, 1600)), ((1000, 5000), (320, 1600)), ((10, 10), (10, 10))],
)
def test_fit_within_keeps_aspect_and_never_enlarges(size, expected) -> None:
    assert fit_within(*size) == expected


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_supported_types_are_sniffed_and_re_encoded_as_jpeg(fmt) -> None:
    raw = encoded(fmt)
    assert sniff_type(raw) == {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}[fmt]
    clean = sanitize_photo(raw)
    assert clean.jpeg.startswith(b"\xff\xd8\xff")


def test_heif_brand_is_recognised() -> None:
    assert sniff_type(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00") == "heif"


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        b"%PDF-1.7 not an image",
        b"GIF89a" + b"\x00" * 32,
        b"MZ\x90\x00 executable",
    ],
)
def test_spoofed_or_unsupported_types_are_refused_by_magic_bytes(payload) -> None:
    with pytest.raises(PhotoRejected) as caught:
        sanitize_photo(payload)
    assert caught.value.reason == "unsupported_type"


def test_text_file_named_like_a_jpeg_is_refused() -> None:
    # The browser-declared type/name never matter; only the bytes do.
    with pytest.raises(PhotoRejected) as caught:
        sanitize_photo(b"just text pretending to be photo.jpg")
    assert caught.value.reason == "unsupported_type"


def test_truncated_jpeg_is_corrupt() -> None:
    raw = jpeg_with_gps((400, 300))
    with pytest.raises(PhotoRejected) as caught:
        sanitize_photo(raw[: len(raw) // 3])
    assert caught.value.reason == "corrupt"


def test_magic_bytes_with_garbage_body_is_corrupt() -> None:
    with pytest.raises(PhotoRejected) as caught:
        sanitize_photo(b"\x89PNG\r\n\x1a\n" + b"\x00garbage" * 50)
    assert caught.value.reason == "corrupt"


def test_oversize_upload_is_refused_before_decoding() -> None:
    with pytest.raises(PhotoRejected) as caught:
        sanitize_photo(b"\xff\xd8\xff" + b"\x00" * MAX_UPLOAD_BYTES)
    assert caught.value.reason == "too_large"


def test_empty_upload_is_refused() -> None:
    with pytest.raises(PhotoRejected) as caught:
        sanitize_photo(b"")
    assert caught.value.reason == "empty"
