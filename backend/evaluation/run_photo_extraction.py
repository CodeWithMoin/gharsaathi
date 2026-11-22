"""Live check: does photo extraction read a printed pack name verbatim?

Draws a simple "Amul Lassi 200 ml" pack label with Pillow, runs it through the
same sanitising as an upload, and calls the configured OpenAI vision model once
per run. OpenAI only; no store, cart or order calls.

    uv run --with-requirements requirements.txt python -m evaluation.run_photo_extraction [--env-file PATH]

Exit code 0 when the item's search_query names "Amul Lassi" (and not a category
or a sibling product such as "flavoured milk" or "Kool").
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings
from app.photos.extraction import OpenAIPhotoExtractor
from app.photos.images import sanitize_photo


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


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--save-image", type=Path, default=None)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        print("OPENAI_API_KEY is not set; nothing to run.", file=sys.stderr)
        return 2
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
