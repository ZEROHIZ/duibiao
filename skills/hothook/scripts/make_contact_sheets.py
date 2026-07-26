#!/usr/bin/env python3
"""Create contact sheets from timestamped HotHook frame screenshots."""

from __future__ import annotations

import argparse
import math
import pathlib

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create HotHook contact sheets")
    parser.add_argument("--frames", default="hothook_full_watch/frames", help="Directory containing frame_0000.png files")
    parser.add_argument("--out", default="hothook_full_watch/contact_sheets", help="Output directory")
    parser.add_argument("--interval", type=int, default=5, help="Seconds between frame files")
    parser.add_argument("--sheet-span", type=int, default=60, help="Seconds per sheet when --chapter is omitted")
    parser.add_argument("--chapter", action="append", default=[], help="Chapter as name:start:end, e.g. intro:0:40")
    return parser.parse_args()


def load_font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def parse_chapters(args: argparse.Namespace, max_second: int) -> list[tuple[str, int, int]]:
    chapters: list[tuple[str, int, int]] = []
    for item in args.chapter:
        try:
            name, start, end = item.split(":", 2)
            chapters.append((name, int(start), int(end)))
        except ValueError as exc:
            raise SystemExit(f"Invalid --chapter value: {item}. Expected name:start:end") from exc
    if chapters:
        return chapters

    sheet_count = max(1, math.ceil((max_second + args.interval) / args.sheet_span))
    for index in range(sheet_count):
        start = index * args.sheet_span
        end = min(max_second, start + args.sheet_span - args.interval)
        chapters.append((f"sheet_{index + 1:02d}_{start:04d}_{end:04d}", start, end))
    return chapters


def main() -> int:
    args = parse_args()
    frames_dir = pathlib.Path(args.frames)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if not frame_files:
        raise SystemExit(f"No frame_*.png files found in {frames_dir}")

    max_second = max(int(path.stem.split("_")[-1]) for path in frame_files)
    chapters = parse_chapters(args, max_second)

    label_font = load_font(24)
    title_font = load_font(34)
    thumb_w, thumb_h = 360, 250
    margin, gap, cols = 24, 16, 3

    for name, start, end in chapters:
        seconds = list(range(start, end + 1, args.interval))
        rows = max(1, math.ceil(len(seconds) / cols))
        sheet_w = margin * 2 + cols * thumb_w + (cols - 1) * gap
        sheet_h = margin * 2 + 48 + rows * (thumb_h + 38) + (rows - 1) * gap
        sheet = Image.new("RGB", (sheet_w, sheet_h), "#f7f4ee")
        draw = ImageDraw.Draw(sheet)
        draw.text((margin, margin), name, fill="#111318", font=title_font)
        y0 = margin + 56

        for index, second in enumerate(seconds):
            frame_path = frames_dir / f"frame_{second:04d}.png"
            if not frame_path.exists():
                continue
            image = Image.open(frame_path).convert("RGB")
            image.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (thumb_w, thumb_h), "#111318")
            tile.paste(image, ((thumb_w - image.width) // 2, (thumb_h - image.height) // 2))
            col = index % cols
            row = index // cols
            x = margin + col * (thumb_w + gap)
            y = y0 + row * (thumb_h + 38 + gap)
            sheet.paste(tile, (x, y))
            draw.text((x, y + thumb_h + 6), f"{second // 60:02d}:{second % 60:02d}", fill="#d43f32", font=label_font)

        output = out_dir / f"{name}.jpg"
        sheet.save(output, quality=92)
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
