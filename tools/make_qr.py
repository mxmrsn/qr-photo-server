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
    parse_hex,
    render_qr,
)

DPI = 300
DEFAULT_HEADER = Path(__file__).resolve().parent.parent / "assets" / "monogram-rm.png"

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


def tinted(art: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """Recolour a black-on-transparent mark, keeping its alpha."""
    out = Image.new("RGBA", art.size, colour + (255,))
    out.putalpha(art.split()[-1])
    return out


def render_card(
    label: str,
    url: str,
    args,
    size_in: tuple[float, float] = (5, 7),
    logo: Image.Image | None = None,
) -> Image.Image:
    """Draw one table card, on a transparent ground.

    Transparent so the card can be printed on any paper colour. The only
    opaque area is the panel behind the code, which stays white because a QR
    needs its light modules light — on dark stock that panel is the difference
    between scanning and not.

    With Wi-Fi details supplied the card becomes two numbered steps: on a
    local-only setup a guest who scans before joining gets a browser error and
    gives up, so joining is genuinely step one and is printed that way.
    """
    W, H = int(size_in[0] * DPI), int(size_in[1] * DPI)
    ink = hex_to_rgb(settings.ink)
    accent = hex_to_rgb(settings.accent)
    wifi_mode = bool(args.wifi_ssid)
    wifi_qr = wifi_mode and getattr(args, "wifi_qr", False)

    if args.card_bg:
        card = Image.new("RGBA", (W, H), hex_to_rgb(args.card_bg) + (255,))
    else:
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)

    f_date = load_font(SERIF_FONTS, int(W * 0.034))
    f_step = load_font(SANS_FONTS, int(W * (0.032 if wifi_mode else 0.037)))
    f_ssid = load_font(SERIF_FONTS, int(W * 0.040))
    f_pass = load_font(SANS_FONTS, int(W * 0.028))
    f_label = load_font(SERIF_FONTS, int(W * 0.052))
    f_tiny = load_font(SANS_FONTS, int(W * 0.023))
    f_names = load_font(SERIF_FONTS, int(W * 0.070))

    margin = int(W * 0.085)
    inner = W - margin * 2
    top = int(H * 0.032)
    bottom = int(H * 0.032)
    tight = 0.58 if wifi_mode else 1.0
    gap_s = int(H * 0.010 * tight)
    gap_m = int(H * 0.018 * tight)
    gap_l = int(H * 0.027 * tight)
    rule_len = inner * 0.11

    # --- the header mark -------------------------------------------------
    header = None
    header_path = Path(args.header_logo) if args.header_logo else DEFAULT_HEADER
    if not args.no_header_logo and header_path.exists():
        try:
            header = tinted(monochrome_logo(header_path), ink)
            header.thumbnail((int(inner * 0.46), int(H * 0.092)), Image.LANCZOS)
        except Exception:
            header = None

    step_one = "1   Join the Wi-Fi" if wifi_mode else ""
    step_two = ("2   " + args.call_to_action) if wifi_mode else args.call_to_action
    two_lines = wrap(d, step_two, f_step, inner)

    # --- measure ---------------------------------------------------------
    above = int(H * 0.006) + gap_m
    above += (header.height if header else line_height(f_names)) + gap_s
    if settings.event_date:
        above += line_height(f_date) + gap_s

    wifi_px = 0
    if wifi_mode:
        wifi_px = int(inner * 0.22) if wifi_qr else 0
        one_lines = wrap(d, step_one, f_step, inner)
        above += gap_m + sum(line_height(f_step) for _ in one_lines)
        above += max(wifi_px, line_height(f_ssid) + line_height(f_pass)) + gap_m
        above += int(H * 0.004) + gap_m            # divider

    above += gap_s + sum(line_height(f_step) for _ in two_lines) + gap_m

    below = gap_l
    if label:
        below += line_height(f_label) + gap_s
    below += gap_m + int(H * 0.006)                # closing rule
    if settings.event_hashtag:
        below += gap_m + line_height(f_tiny)

    pad_ratio = 0.045
    room = H - top - bottom - above - below
    qr_px = int(min(inner * 0.92, room / (1 + 2 * pad_ratio)))
    if qr_px < inner * 0.44 and len(two_lines) > 1:
        two_lines = two_lines[:1]
        above -= line_height(f_step)
        room = H - top - bottom - above - below
        qr_px = int(min(inner * 0.92, room / (1 + 2 * pad_ratio)))

    block_h = above + int(qr_px * (1 + 2 * pad_ratio)) + below
    y = int(top + max(0, (H - top - bottom - block_h) // 2))

    # --- draw ------------------------------------------------------------
    d.line([(W / 2 - rule_len, y), (W / 2 + rule_len, y)], fill=accent, width=3)
    y += int(H * 0.006) + gap_m

    if header:
        card.paste(header, ((W - header.width) // 2, y), header)
        y += header.height + gap_s
    else:
        y += draw_centred(d, y, settings.couple_names, f_names, ink, W) + gap_s

    if settings.event_date:
        y += draw_centred(d, y, settings.event_date, f_date, accent, W) + gap_s

    if wifi_mode:
        y += gap_m
        wifi_top = y
        for line in one_lines:
            y += draw_centred(d, y, line, f_step, ink, W)
        y += int(H * 0.004)
        y += draw_centred(d, y, args.wifi_ssid, f_ssid, accent, W)
        pass_line = (f"password  {args.wifi_password}" if args.wifi_password
                     else "no password needed")
        y += draw_centred(d, y, pass_line, f_pass, ink, W)

        if wifi_qr:
            wifi_img = render_qr(wifi_payload(args.wifi_ssid, args.wifi_password),
                                 wifi_px, ink, strong=False, style=args.style)
            card.paste(wifi_img, ((W - wifi_px) // 2, y + gap_s))
            y = max(y, y + gap_s + wifi_px)
        y += gap_m

        d.line([(margin, y), (W - margin, y)], fill=accent, width=2)
        y += int(H * 0.004) + gap_m

    y += gap_s
    for line in two_lines:
        y += draw_centred(d, y, line, f_step, ink, W)
    y += gap_m

    pad = int(qr_px * pad_ratio)
    qr_x = (W - qr_px) // 2
    y += pad
    # The one opaque area: a QR needs its light modules light.
    d.rounded_rectangle([qr_x - pad, y - pad, qr_x + qr_px + pad, y + qr_px + pad],
                        radius=int(pad * 1.4), fill=(255, 255, 255, 255))
    qr_img, qr_note = build_verified_qr(
        url, qr_px, ink, logo,
        coverage=args.logo_coverage, min_modules=args.min_modules,
        detail=args.logo_detail, style=args.style, mono=args.mono,
        accent=args.accent_rgb, logo_weight=args.logo_weight,
    )
    render_card.last_note = qr_note
    card.paste(qr_img.convert("RGBA"), (qr_x, y))
    y += qr_px + pad + gap_l

    if label:
        y += draw_centred(d, y, label, f_label, accent, W) + gap_s

    # Closing rule, mirroring the one at the top.
    y += gap_m
    d.line([(W / 2 - rule_len, y), (W / 2 + rule_len, y)], fill=accent, width=3)

    if settings.event_hashtag:
        draw_centred(d, H - bottom - line_height(f_tiny), settings.event_hashtag,
                     f_tiny, accent, W)

    if not args.no_border:
        inset = int(W * args.border_inset)
        width = max(2, int(W * args.border_width))
        colour = hex_to_rgb(args.border_color) if args.border_color else ink
        d.rectangle([inset, inset, W - inset - 1, H - inset - 1],
                    outline=colour + (255,), width=width)

    return card


def flatten(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    if im.mode != "RGBA":
        return im.convert("RGB")
    out = Image.new("RGB", im.size, bg)
    out.paste(im, mask=im.split()[-1])
    return out


def sheet_of(cards: list[Image.Image], per_row: int = 2, per_col: int = 2) -> list[Image.Image]:
    """Lay cards out 4-up on Letter with faint cut guides — cheaper to print."""
    PW, PH = int(8.5 * DPI), int(11 * DPI)
    pages: list[Image.Image] = []
    per_page = per_row * per_col

    for start in range(0, len(cards), per_page):
        page = Image.new("RGBA", (PW, PH), (255, 255, 255, 0))
        d = ImageDraw.Draw(page)
        chunk = cards[start:start + per_page]
        cw, ch = PW // per_row, PH // per_col
        for i, card in enumerate(chunk):
            scaled = card.copy()
            scaled.thumbnail((cw - 40, ch - 40), Image.LANCZOS)
            col, row = i % per_row, i // per_row
            x = col * cw + (cw - scaled.width) // 2
            y = row * ch + (ch - scaled.height) // 2
            page.paste(scaled, (x, y), scaled if scaled.mode == 'RGBA' else None)
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
    ap.add_argument("--accent", default=None,
                    help="hue for the logo inside the code, e.g. '#9b8aa6'. Only the "
                         "hue is used; luminance is pinned to verified-scannable "
                         f"values. Defaults to QR_ACCENT ({settings.qr_accent or 'none'})")
    ap.add_argument("--no-accent", action="store_true", help="neutral grey logo")
    ap.add_argument("--no-border", action="store_true", help="omit the frame")
    ap.add_argument("--border-inset", type=float, default=0.030,
                    help="frame inset as a fraction of card width (default 0.030)")
    ap.add_argument("--border-width", type=float, default=0.0022,
                    help="frame line weight as a fraction of card width")
    ap.add_argument("--border-color", default="",
                    help="frame colour, e.g. '#8a6f4e'; defaults to the ink colour")
    ap.add_argument("--header-logo", default="",
                    help=f"artwork for the top of the card "
                         f"(default: {DEFAULT_HEADER.name} if present)")
    ap.add_argument("--no-header-logo", action="store_true",
                    help="print the couple's names as text instead")
    ap.add_argument("--logo-weight", type=float, default=1.0,
                    help="thicken the mark: 1.0 keeps its true proportions, "
                         "higher suits fine line art (monogram ~1.8, posy ~2.2)")
    ap.add_argument("--mono", action="store_true",
                    help="black and white only; the logo is carried by dot size "
                         "alone. Scans with more margin, but strokes break up "
                         "wherever a light module falls inside the mark")
    ap.add_argument("--tables", default="",
                    help="optional: per-table codes, e.g. 1-18 (default is one shared code)")
    ap.add_argument("--labels", default="",
                    help="optional: per-place codes, e.g. 'Bar,Patio'")
    ap.add_argument("--url", default="", help="override BASE_URL")
    ap.add_argument("--out", default="qr_out", help="output directory")
    ap.add_argument("--layout", choices=["card", "sheet"], default="card",
                    help="one 5x7 card per page, or 4-up on Letter")
    ap.add_argument("--size", default="5x7", help="card size in inches, e.g. 4x6")
    ap.add_argument("--call-to-action", default="",
                    help="wording above the code; defaults to fit whether wifi is shown")
    ap.add_argument("--card-bg", default="", help="hex background, defaults to the site's paper")
    ap.add_argument("--wifi-ssid", default="", help="add a second QR that joins your wifi")
    ap.add_argument("--wifi-password", default="")
    ap.add_argument("--wifi-qr", action="store_true",
                    help="also print a scannable code that joins the network. "
                         "Convenient, but it costs the upload code about a third "
                         "of its size, which measurably hurts scanning in low light")
    ap.add_argument("--generic", action="store_true",
                    help="also make one card with no table label")
    args = ap.parse_args()

    if not args.call_to_action:
        args.call_to_action = ("Then scan to share your photos and videos"
                               if args.wifi_ssid
                               else "Scan to share your photos and videos with us")

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
    raw_accent = "" if args.no_accent else (
        args.accent if args.accent is not None else settings.qr_accent)
    args.accent_rgb = parse_hex(raw_accent)
    if args.accent_rgb:
        print(f"accent: #{''.join(f'{c:02x}' for c in args.accent_rgb)} "
              f"(hue only — luminance is pinned to what verifies)")
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
    # PDF has no alpha channel. Flattening onto white loses nothing in print —
    # a printer lays down no ink there either, so coloured stock still shows
    # through. The PNGs keep their transparency for digital use.
    flat = [flatten(p) for p in pages]
    flat[0].save(pdf, "PDF", resolution=DPI, save_all=True, append_images=flat[1:])

    print(f"\n{len(to_print)} card(s) to print -> {out}/")
    print(f"print this: {pdf}")
    if args.layout == "card":
        print("tip: --layout sheet puts 4 cards on a Letter page, which is much cheaper to print")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
