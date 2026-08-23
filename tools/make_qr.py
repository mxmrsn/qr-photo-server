#!/usr/bin/env python3
"""Generate printable QR table cards.

    tools/make_qr.py --tables 1-18
    tools/make_qr.py --labels "Head Table,Bar,Patio,Photo Booth"
    tools/make_qr.py --tables 1-12 --layout sheet     # 4-up on Letter, with cut guides
    tools/make_qr.py --wifi-ssid Wedding --wifi-password loveislove

Writes PNGs and a print-ready PDF into qr_out/.

The URL comes from BASE_URL in .env unless you pass --url. Point that at a
domain you own rather than an IP address, so you can move the server later
without reprinting sixty cards.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.qrstyle import (
    DEFAULT_LOGO,
    build_verified_qr,
    monochrome_logo,
    render_qr,
)

DPI = 300

# Font candidates, best first. Falls back to PIL's bitmap font if none exist,
# which is ugly but never crashes on a machine without these installed.
SERIF_FONTS = [
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Didot.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
]
SANS_FONTS = [
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/Supplemental/Avenir Next.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default(size)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0)


def wifi_payload(ssid: str, password: str, hidden: bool = False) -> str:
    def esc(text: str) -> str:
        for ch in ("\\", ";", ",", ":", '"'):
            text = text.replace(ch, "\\" + ch)
        return text
    auth = "WPA" if password else "nopass"
    return f"WIFI:T:{auth};S:{esc(ssid)};P:{esc(password)};H:{'true' if hidden else 'false'};;"


def line_height(font) -> int:
    """A dependable line advance. Ink-box height varies wildly by glyph
    ("us" measures far shorter than "Photography"), so lay out on font size."""
    return int(getattr(font, "size", 20) * 1.24)


def draw_centred(draw, y, text, font, fill, width) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (box[2] - box[0])) / 2 - box[0], y), text, font=font, fill=fill)
    return line_height(font)


def wrap(draw, text, font, max_width) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def render_card(
    label: str,
    url: str,
    args,
    size_in: tuple[float, float] = (5, 7),
    logo: Image.Image | None = None,
) -> Image.Image:
    W, H = int(size_in[0] * DPI), int(size_in[1] * DPI)
    ink = hex_to_rgb(settings.ink)
    accent = hex_to_rgb(settings.accent)
    paper = hex_to_rgb(args.card_bg or settings.paper)

    card = Image.new("RGB", (W, H), paper)
    d = ImageDraw.Draw(card)

    f_names = load_font(SERIF_FONTS, int(W * 0.098))
    f_date = load_font(SERIF_FONTS, int(W * 0.036))
    f_call = load_font(SANS_FONTS, int(W * 0.038))
    f_label = load_font(SERIF_FONTS, int(W * 0.058))
    f_url = load_font(SANS_FONTS, int(W * 0.029))
    f_tiny = load_font(SANS_FONTS, int(W * 0.024))

    margin = int(W * 0.085)
    inner = W - margin * 2
    top = int(H * 0.062)
    bottom = int(H * 0.052)

    gap_s, gap_m, gap_l = int(H * 0.011), int(H * 0.020), int(H * 0.030)

    # --- measure everything before drawing anything ----------------------
    call_lines = wrap(d, args.call_to_action, f_call, inner)
    pretty_url = url.replace("https://", "").replace("http://", "").rstrip("/")

    above = int(H * 0.006) + gap_m                                  # rule
    above += line_height(f_names) + gap_s
    if settings.event_date:
        above += line_height(f_date) + gap_s
    above += gap_s + sum(line_height(f_call) for _ in call_lines) + gap_m

    below = gap_l
    if label:
        below += line_height(f_label) + gap_s
    below += line_height(f_url)

    wifi_px = 0
    if args.wifi_ssid:
        wifi_px = int(inner * 0.34)
        below += gap_m + int(H * 0.004) + gap_m + wifi_px
    if settings.event_hashtag:
        below += gap_m + line_height(f_tiny)

    # Whatever vertical room is left belongs to the QR — it is the one element
    # that must never be cramped, and the one that can absorb the slack.
    pad_ratio = 0.045
    room = H - top - bottom - above - below
    qr_px = int(min(inner * 0.92, room / (1 + 2 * pad_ratio)))
    if qr_px < inner * 0.42:
        # Too much text to fit a scannable code; drop the call to action.
        call_lines = call_lines[:1]
        above = int(H * 0.006) + gap_m + line_height(f_names) + gap_s
        if settings.event_date:
            above += line_height(f_date) + gap_s
        above += gap_s + line_height(f_call) + gap_m
        room = H - top - bottom - above - below
        qr_px = int(min(inner * 0.92, room / (1 + 2 * pad_ratio)))

    block_h = above + int(qr_px * (1 + 2 * pad_ratio)) + below
    y = int(top + max(0, (H - top - bottom - block_h) // 2))

    # --- draw ------------------------------------------------------------
    d.line([(W / 2 - inner * 0.11, y), (W / 2 + inner * 0.11, y)], fill=accent, width=3)
    y += int(H * 0.006) + gap_m

    y += draw_centred(d, y, settings.couple_names, f_names, ink, W) + gap_s
    if settings.event_date:
        y += draw_centred(d, y, settings.event_date, f_date, accent, W) + gap_s

    y += gap_s
    for line in call_lines:
        y += draw_centred(d, y, line, f_call, ink, W)
    y += gap_m

    pad = int(qr_px * pad_ratio)
    qr_x = (W - qr_px) // 2
    y += pad
    d.rounded_rectangle(
        [qr_x - pad, y - pad, qr_x + qr_px + pad, y + qr_px + pad],
        radius=int(pad * 1.4), fill="white",
    )
    qr_img, qr_note = build_verified_qr(
        url, qr_px, ink, logo,
        coverage=args.logo_coverage, min_modules=args.min_modules,
        detail=args.logo_detail, style=args.style,
    )
    render_card.last_note = qr_note
    card.paste(qr_img, (qr_x, y))
    y += qr_px + pad + gap_l

    if label:
        y += draw_centred(d, y, label, f_label, accent, W) + gap_s
    y += draw_centred(d, y, pretty_url, f_url, ink, W)

    if args.wifi_ssid:
        y += gap_m
        d.line([(margin, y), (W - margin, y)], fill=accent, width=2)
        y += int(H * 0.004) + gap_m
        wifi = render_qr(wifi_payload(args.wifi_ssid, args.wifi_password), wifi_px, ink,
                         strong=False, style=args.style)
        card.paste(wifi, (margin, y))
        tx = margin + wifi_px + int(W * 0.045)
        d.text((tx, y + int(wifi_px * 0.08)), "Join the Wi-Fi", font=f_call, fill=ink)
        d.text((tx, y + int(wifi_px * 0.42)), args.wifi_ssid, font=f_url, fill=accent)
        if args.wifi_password:
            d.text((tx, y + int(wifi_px * 0.68)), args.wifi_password, font=f_tiny, fill=ink)
        y += wifi_px + gap_m

    if settings.event_hashtag:
        draw_centred(d, H - bottom - line_height(f_tiny), settings.event_hashtag, f_tiny, accent, W)

    return card


def sheet_of(cards: list[Image.Image], per_row: int = 2, per_col: int = 2) -> list[Image.Image]:
    """Lay cards out 4-up on Letter with faint cut guides — cheaper to print."""
    PW, PH = int(8.5 * DPI), int(11 * DPI)
    pages: list[Image.Image] = []
    per_page = per_row * per_col

    for start in range(0, len(cards), per_page):
        page = Image.new("RGB", (PW, PH), "white")
        d = ImageDraw.Draw(page)
        chunk = cards[start:start + per_page]
        cw, ch = PW // per_row, PH // per_col
        for i, card in enumerate(chunk):
            scaled = card.copy()
            scaled.thumbnail((cw - 40, ch - 40), Image.LANCZOS)
            col, row = i % per_row, i // per_row
            x = col * cw + (cw - scaled.width) // 2
            y = row * ch + (ch - scaled.height) // 2
            page.paste(scaled, (x, y))
            d.rectangle([x - 1, y - 1, x + scaled.width, y + scaled.height],
                        outline=(205, 205, 205), width=1)
        pages.append(page)
    return pages


def parse_tables(spec: str) -> list[str]:
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and all(p.strip().isdigit() for p in part.split("-", 1)):
            lo, hi = (int(p) for p in part.split("-", 1))
            out.extend(str(n) for n in range(lo, hi + 1))
        else:
            out.append(part)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate printable QR table cards.")
    ap.add_argument("--copies", type=int, default=1,
                    help="how many identical cards to print (one per table)")
    ap.add_argument("--logo", default="",
                    help=f"logo to set into the QR (default: {DEFAULT_LOGO.name} if present)")
    ap.add_argument("--no-logo", action="store_true", help="plain QR, no logo")
    ap.add_argument("--logo-coverage", type=float, default=0.95,
                    help="how much of the code the logo spans (default 0.95)")
    ap.add_argument("--logo-detail", type=int, default=2, choices=[1, 2, 3],
                    help="dots per module used to draw the logo (default 2)")
    ap.add_argument("--min-modules", type=int, default=57,
                    help="grid density; higher renders the logo finer (default 57)")
    ap.add_argument("--style", choices=["dots", "squares"], default="dots",
                    help="round dots (default) or classic squares")
    ap.add_argument("--tables", default="",
                    help="optional: per-table codes, e.g. 1-18 (default is one shared code)")
    ap.add_argument("--labels", default="",
                    help="optional: per-place codes, e.g. 'Bar,Patio'")
    ap.add_argument("--url", default="", help="override BASE_URL")
    ap.add_argument("--out", default="qr_out", help="output directory")
    ap.add_argument("--layout", choices=["card", "sheet"], default="card",
                    help="one 5x7 card per page, or 4-up on Letter")
    ap.add_argument("--size", default="5x7", help="card size in inches, e.g. 4x6")
    ap.add_argument("--call-to-action", default="Scan to share your photos and videos with us")
    ap.add_argument("--card-bg", default="", help="hex background, defaults to the site's paper")
    ap.add_argument("--wifi-ssid", default="", help="add a second QR that joins your wifi")
    ap.add_argument("--wifi-password", default="")
    ap.add_argument("--generic", action="store_true",
                    help="also make one card with no table label")
    args = ap.parse_args()

    base = (args.url or settings.base_url).rstrip("/")
    if not base.startswith(("http://", "https://")):
        print(f"error: URL must start with http:// or https:// (got {base!r})", file=sys.stderr)
        return 2
    if "localhost" in base or "127.0.0.1" in base:
        print("warning: BASE_URL points at localhost — guests' phones cannot reach that.\n"
              "         Set BASE_URL in .env, or pass --url, before printing.\n", file=sys.stderr)

    try:
        w_in, h_in = (float(v) for v in args.size.lower().split("x", 1))
    except ValueError:
        print(f"error: --size must look like 5x7 (got {args.size!r})", file=sys.stderr)
        return 2

    # --- the logo that goes in the middle of the code --------------------
    logo = None
    if not args.no_logo:
        logo_path = Path(args.logo) if args.logo else DEFAULT_LOGO
        if logo_path.exists():
            try:
                logo = monochrome_logo(logo_path)
                print(f"logo: {logo_path.name} -> black and white, "
                      f"shaded across {args.logo_coverage:.0%} of the code")
            except Exception as exc:
                print(f"warning: could not use {logo_path}: {exc}", file=sys.stderr)
        elif args.logo:
            print(f"error: no logo at {logo_path}", file=sys.stderr)
            return 2
    args.logo_coverage = max(0.20, min(args.logo_coverage, 1.0))
    if args.no_logo:
        logo = None

    # --- what to make ----------------------------------------------------
    labels = parse_tables(args.tables)
    labels += [l.strip() for l in args.labels.split(",") if l.strip()]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("card-*.png"):
        stale.unlink()

    targets: list[tuple[str, str]] = []
    if labels:
        for label in labels:
            pretty = f"Table {label}" if label.isdigit() else label
            targets.append((pretty, f"{base}/t/{quote(label, safe='')}"))
    else:
        # One code for the whole wedding — the same card on every table.
        targets.append(("", base + "/"))

    cards: list[Image.Image] = []
    for pretty, url in targets:
        card = render_card(pretty, url, args, (w_in, h_in), logo=logo)
        cards.append(card)
        slug = "".join(c if c.isalnum() else "-" for c in pretty.lower()).strip("-") or "card"
        card.save(out / f"card-{slug}.png", dpi=(DPI, DPI))
        print(f"  card-{slug}.png   ->  {url}\n                  {getattr(render_card, 'last_note', '')}")

    # One design, many printed copies: you still need a card per table.
    copies = max(1, args.copies)
    to_print = [c for c in cards for _ in range(copies)] if copies > 1 else cards
    if copies > 1:
        print(f"  x{copies} copies -> {len(to_print)} cards to print")

    pages = sheet_of(to_print) if args.layout == "sheet" else to_print
    pdf = out / ("table-cards-4up.pdf" if args.layout == "sheet" else "table-cards.pdf")
    pages[0].save(pdf, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])

    print(f"\n{len(to_print)} card(s) to print -> {out}/")
    print(f"print this: {pdf}")
    if args.layout == "card":
        print("tip: --layout sheet puts 4 cards on a Letter page, which is much cheaper to print")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
