"""Live check: does photo extraction read a printed pack name verbatim?

Draws a simple "Amul Lassi 200 ml" pack label with Pillow, runs it through the
same sanitising as an upload, and calls the configured OpenAI vision model once
per run. OpenAI only; no store, cart or order calls.

    uv run --with-requirements requirements.txt python -m evaluation.run_photo_extraction [--env-file PATH]
    ... python -m evaluation.run_photo_extraction --visual [--save-image DIR]

Exit code 0 when the item's search_query names "Amul Lassi" (and not a category
or a sibling product such as "flavoured milk" or "Kool").

``--visual`` instead runs three OpenAI-only checks on locally drawn images: a
text-less, blurred, tilted lassi-like tetra pack (identified as lassi with no
invented brand), loose tomatoes (a generic "tomato" item), and one visual-match
call comparing the lassi photo with drawn store images (Kool bottle, lassi
tetra pack, Smoodh pack; the lassi pack must be the only same_product).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.core.config import Settings
from app.photos.extraction import OpenAIPhotoExtractor, PhotoItem
from app.photos.images import sanitize_photo
from app.photos.visual_match import Candidate, OpenAIVisualMatchModel, small_jpeg


def label_image() -> bytes:
    image = Image.new("RGB", (900, 1200), (250, 246, 236))
    draw = ImageDraw.Draw(image)
    # A pack-like shape: white carton, red brand band, blue product band.
    draw.rounded_rectangle((150, 120, 750, 1080), radius=40, fill=(255, 255, 255), outline=(30, 60, 140), width=8)
    draw.rectangle((150, 200, 750, 380), fill=(200, 30, 40))
    draw.rectangle((150, 520, 750, 760), fill=(25, 70, 160))

    def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return ImageFont.load_default(size=size)

    def centred(text: str, y: int, size: int, fill: tuple[int, int, int]) -> None:
        face = font(size)
        width = draw.textlength(text, font=face)
        draw.text(((900 - width) / 2, y), text, font=face, fill=fill)

    centred("Amul", 220, 130, (255, 255, 255))
    centred("The Taste of India", 410, 40, (30, 60, 140))
    centred("Lassi", 545, 150, (255, 255, 255))
    centred("Sweet & refreshing", 800, 44, (60, 60, 60))
    centred("200 ml", 900, 90, (30, 60, 140))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _jpeg(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


def lassi_pack(*, text: bool, blur: float = 0.0, tilt: float = 0.0) -> bytes:
    """A blue-and-white tetra pack with a lassi glass picture; optionally no text."""

    image = Image.new("RGB", (900, 1200), (214, 200, 180))
    draw = ImageDraw.Draw(image)
    draw.polygon([(300, 170), (600, 170), (650, 230), (250, 230)], fill=(235, 235, 240))  # carton top
    draw.rectangle((250, 230, 650, 1050), fill=(250, 250, 252), outline=(40, 80, 170), width=6)
    draw.rectangle((250, 260, 650, 420), fill=(30, 80, 175))
    draw.rectangle((250, 880, 650, 1050), fill=(30, 80, 175))
    # The glass of white lassi with a straw.
    draw.polygon([(360, 520), (540, 520), (515, 820), (385, 820)], fill=(255, 252, 240), outline=(120, 150, 200), width=5)
    draw.ellipse((360, 500, 540, 545), fill=(255, 250, 230), outline=(120, 150, 200), width=4)
    draw.line((480, 430, 455, 600), fill=(220, 60, 60), width=10)
    if text:
        face = ImageFont.load_default(size=110)
        draw.text((330, 280), "Lassi", font=face, fill=(255, 255, 255))
    if tilt:
        image = image.rotate(tilt, expand=False, fillcolor=(214, 200, 180))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    return _jpeg(image)


def kool_bottle() -> bytes:
    image = Image.new("RGB", (600, 800), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((255, 90, 345, 170), fill=(200, 30, 40))  # cap
    draw.polygon([(255, 170), (345, 170), (420, 260), (420, 720), (180, 720), (180, 260)], fill=(245, 170, 60))
    draw.rectangle((180, 380, 420, 560), fill=(200, 30, 40))
    face = ImageFont.load_default(size=60)
    draw.text((215, 420), "Kool", font=face, fill=(255, 255, 255))
    draw.text((205, 600), "KESAR", font=ImageFont.load_default(size=40), fill=(120, 50, 10))
    return _jpeg(image)


def smoodh_pack() -> bytes:
    image = Image.new("RGB", (600, 800), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((170, 150, 430, 700), radius=30, fill=(110, 60, 30))
    draw.ellipse((220, 380, 380, 540), fill=(160, 100, 50))
    draw.text((200, 220), "Smoodh", font=ImageFont.load_default(size=56), fill=(255, 230, 200))
    draw.text((215, 600), "Chocolate", font=ImageFont.load_default(size=36), fill=(255, 230, 200))
    return _jpeg(image)


def tomatoes() -> bytes:
    image = Image.new("RGB", (1200, 900), (150, 105, 70))
    draw = ImageDraw.Draw(image)
    spots = [(250, 300), (480, 260), (720, 320), (950, 280), (360, 540), (610, 560), (860, 560), (500, 760)]
    for x, y in spots:
        draw.ellipse((x - 120, y - 105, x + 120, y + 105), fill=(205, 35, 30), outline=(150, 20, 20), width=4)
        draw.ellipse((x - 70, y - 70, x - 20, y - 35), fill=(240, 110, 100))  # shine
        for dx, dy in ((0, -100), (-30, -90), (30, -90), (-15, -110), (15, -110)):
            draw.polygon([(x, y - 85), (x + dx, y + dy - 25), (x + dx // 2 + 8, y - 80)], fill=(40, 120, 40))
    return _jpeg(image.filter(ImageFilter.GaussianBlur(1.2)))


async def visual_checks(settings: Settings, api_key: str, save: Path | None) -> int:
    extractor = OpenAIPhotoExtractor(api_key, settings.openai_base_url, settings.photo_extraction_model)
    model = settings.photo_visual_match_model.strip() or settings.photo_extraction_model
    matcher = OpenAIVisualMatchModel(api_key, settings.openai_base_url, model)
    photo = sanitize_photo(lassi_pack(text=False, blur=3.0, tilt=8.0)).jpeg
    produce = sanitize_photo(tomatoes()).jpeg
    if save:
        save.mkdir(parents=True, exist_ok=True)
        (save / "lassi_no_text.jpg").write_bytes(photo)
        (save / "tomatoes.jpg").write_bytes(produce)
    report: dict[str, object] = {"extraction_model": settings.photo_extraction_model, "match_model": model}
    passed = True

    started = time.monotonic()
    lassi = await extractor.extract(photo)
    report["lassi_no_text"] = {"latency_ms": int((time.monotonic() - started) * 1000), **lassi.model_dump()}
    first = lassi.items[0] if lassi.items else None
    lassi_ok = (
        first is not None
        and "lassi" in " ".join([first.product, first.search_query, *first.alternative_queries]).lower()
        and first.brand is None
        and first.identified_from == "visual"
    )
    passed &= lassi_ok

    started = time.monotonic()
    tomato = await extractor.extract(produce)
    report["tomatoes"] = {"latency_ms": int((time.monotonic() - started) * 1000), **tomato.model_dump()}
    tomato_ok = any("tomato" in item.product.lower() and item.brand is None for item in tomato.items)
    passed &= tomato_ok

    item = first or PhotoItem(product="lassi", search_query="sweet lassi tetra pack")
    candidates = [
        Candidate(1, "Amul Kool Kesar Flavoured Milk 180 ml", small_jpeg(kool_bottle()) or b""),
        Candidate(2, "Amul Lassi 200 ml", small_jpeg(lassi_pack(text=True)) or b""),
        Candidate(3, "Amul Smoodh Chocolate Milk 160 ml", small_jpeg(smoodh_pack()) or b""),
    ]
    started = time.monotonic()
    verdicts = await matcher.compare(photo, item, candidates)
    report["visual_match"] = {
        "latency_ms": int((time.monotonic() - started) * 1000),
        "verdicts": {position: [v.label, v.reason] for position, v in sorted(verdicts.items())},
    }
    match_ok = (
        verdicts.get(2) is not None
        and verdicts[2].label == "same_product"
        and all(verdicts.get(n) is not None and verdicts[n].label != "same_product" for n in (1, 3))
    )
    passed &= match_ok
    report["checks"] = {"lassi_no_text": lassi_ok, "tomatoes": tomato_ok, "visual_match": match_ok}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--save-image", type=Path, default=None)
    parser.add_argument("--visual", action="store_true", help="visual identification and match checks")
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        print("OPENAI_API_KEY is not set; nothing to run.", file=sys.stderr)
        return 2
    if args.visual:
        return await visual_checks(settings, api_key, args.save_image)
    clean = sanitize_photo(label_image())
    if args.save_image:
        args.save_image.write_bytes(clean.jpeg)
    extractor = OpenAIPhotoExtractor(api_key, settings.openai_base_url, settings.photo_extraction_model)
    extraction = await extractor.extract(clean.jpeg)
    print(json.dumps({"model": settings.photo_extraction_model, **extraction.model_dump()}, ensure_ascii=False, indent=2))
    queries = [item.search_query.lower() for item in extraction.items]
    ok = any("amul" in query and "lassi" in query for query in queries) and not any(
        word in query for query in queries for word in ("flavoured milk", "flavored milk", "kool")
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
