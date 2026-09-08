#!/usr/bin/env python3
"""Print-test sheets: one card per motif, plus a combined PDF.

    tools/make_test_signs.py
    tools/make_test_signs.py --url http://192.168.0.10:8000   # a URL that's live now

Produces, in qr_signs/:
  card-<motif>.png    each card at 300 DPI
  test-cards.pdf      one card per page, for a proper look
  test-sheet.pdf      all five 2-up, to print on two sheets and scan quickly
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

# Coverage and weight are per-motif: thickness depends on stroke width
# relative to a module, so one value cannot serve a filled logo and a
# line drawing both.
MOTIFS = [
    ("monogram", "assets/monogram-rm.png", 0.95, 1.8),
    ("smoose",   "assets/smoose-logo.png", 0.95, 1.0),
    ("flutes",   "assets/art/flutes.png",  0.80, 1.0),
    ("heart",    "assets/art/heart.png",   0.72, 1.0),
    ("posy",     "assets/art/posy.png",    0.90, 2.2),
]
DPI = 300


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one test card per motif.")
    ap.add_argument("--url", default="", help="override BASE_URL")
    ap.add_argument("--wifi-ssid", default="MaxAndRachel")
    ap.add_argument("--wifi-password", default="")
    ap.add_argument("--out", default="qr_signs")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*"):
        if stale.is_file():
            stale.unlink()

    url = args.url or settings.base_url
    cards: list[tuple[str, Path]] = []

    for name, logo, coverage, weight in MOTIFS:
        if not (ROOT / logo).exists():
            print(f"  skipping {name}: {logo} not found")
            continue
        tmp = out / f"_work_{name}"
        cmd = [str(ROOT / ".venv/bin/python"), str(ROOT / "tools/make_qr.py"),
               "--url", url, "--logo", logo,
               "--logo-coverage", str(coverage), "--logo-weight", str(weight),
               "--min-modules", "49", "--out", str(tmp)]
        if args.wifi_ssid:
            cmd += ["--wifi-ssid", args.wifi_ssid]
        if args.wifi_password:
            cmd += ["--wifi-password", args.wifi_password]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        src = tmp / "card-card.png"
        if proc.returncode != 0 or not src.exists():
            print(f"  {name}: FAILED\n{proc.stdout}{proc.stderr}")
            continue
        dest = out / f"card-{name}.png"
        src.replace(dest)
        for leftover in tmp.iterdir():
            leftover.unlink()
        tmp.rmdir()
        note = next((l.strip() for l in proc.stdout.splitlines()
                     if "grid" in l or "verified" in l), "")
        print(f"  card-{name}.png   {note}")
        cards.append((name, dest))

    if not cards:
        print("nothing generated")
        return 1

    # One card per page.
    pages = [Image.open(p).convert("RGB") for _, p in cards]
    pdf = out / "test-cards.pdf"
    pages[0].save(pdf, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])

    # Two per Letter page, with a cut line, for fast scan testing.
    PW, PH = int(8.5 * DPI), int(11 * DPI)
    sheets: list[Image.Image] = []
    for i in range(0, len(pages), 2):
        sheet = Image.new("RGB", (PW, PH), "white")
        for slot, card in enumerate(pages[i:i + 2]):
            c = card.copy()
            c.thumbnail((PW - 240, PH // 2 - 200), Image.LANCZOS)
            x = (PW - c.width) // 2
            y = 90 + slot * (PH // 2)
            sheet.paste(c, (x, y))
        sheets.append(sheet)
    sheet_pdf = out / "test-sheet.pdf"
    sheets[0].save(sheet_pdf, "PDF", resolution=DPI, save_all=True, append_images=sheets[1:])

    print(f"\n{len(cards)} card(s) -> {out}/")
    print(f"  {pdf}        one card per page")
    print(f"  {sheet_pdf}        {len(sheets)} sheet(s), two cards each")
    print(f"\nAll encode: {url.rstrip('/')}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
