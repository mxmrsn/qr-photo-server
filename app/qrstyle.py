"""Styled QR rendering, shared by the card generator and the live server.

Dots instead of squares, rounded finder patterns, and a logo redrawn on the
same dot grid so it belongs to the code rather than sitting on top of it.
"""
from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_Q

ASSETS = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_LOGO = ASSETS / "smoose-logo.png"


def monochrome_logo(path: Path) -> Image.Image:
    """Reduce any logo to solid black artwork on transparency.

    Works for light-on-dark marks (white artwork on a green square) and
    dark-on-light ones alike: the background tone is read from the border
    pixels, and everything that differs from it becomes ink. Anti-aliased
    edges survive as partial alpha, so the result doesn't look jagged when
    it lands in the middle of a QR code.
    """
    src = Image.open(path)
    if src.mode in ("RGBA", "LA", "P"):
        src = src.convert("RGBA")
        flat = Image.new("RGB", src.size, "white")
        flat.paste(src, mask=src.split()[-1])
        src = flat
    else:
        src = src.convert("RGB")

    grey = src.convert("L")
    w, h = grey.size
    px = grey.load()

    # Background tone: the median of a one-pixel ring around the edge.
    ring = [px[x, 0] for x in range(w)] + [px[x, h - 1] for x in range(w)] \
         + [px[0, y] for y in range(h)] + [px[w - 1, y] for y in range(h)]
    ring.sort()
    bg = ring[len(ring) // 2]

    # Which way does the artwork sit relative to that background?
    #
    # The bands must not include the background itself. Clamping bg+25 to 255
    # counts a pure-white background as "artwork lighter than background",
    # which is nonsense — and then the span below collapses to 1 and every
    # pixel clamps to zero alpha. That silently produced an empty mask for any
    # black-on-white logo; only light-on-dark marks happened to work.
    hist = grey.histogram()
    hi, lo = bg + 25, bg - 24
    lighter = sum(hist[hi:]) if hi <= 255 else 0
    darker = sum(hist[:lo]) if lo > 0 else 0
    artwork_is_lighter = lighter >= darker

    # A degenerate span means the background sits hard against one end of the
    # range; use the full range rather than dividing by ~1 and clipping away
    # the entire mark.
    if artwork_is_lighter:
        span = 255 - bg if (255 - bg) >= 16 else 255
        alpha = grey.point(lambda v: max(0, min(255, int((v - bg) * 255 / span))))
    else:
        span = bg if bg >= 16 else 255
        alpha = grey.point(lambda v: max(0, min(255, int((bg - v) * 255 / span))))

    ink = Image.new("RGBA", src.size, (0, 0, 0, 0))
    ink.putalpha(alpha)
    black = Image.new("RGBA", src.size, (0, 0, 0, 255))
    black.putalpha(alpha)

    bbox = black.getbbox()          # trim the dead margin so it fills its panel
    return black.crop(bbox) if bbox else black


# Four tones. The logo is expressed by *which* tone a module gets, never by
# removing it — so no data is destroyed and the mark can span the whole code
# at full module resolution.
#
# A decoder binarises around the local midpoint, so the only hard rule is that
# both dark tones stay clearly below it and both light tones clearly above.
#
#            in the logo    outside it
#   dark        near-black    grey
#   light       light grey    white
# Tone AND dot size carry the logo. Tone is the constrained half: a decoder
# samples the middle of each module, so every dark tone must binarise dark and
# every light tone light. Size is unconstrained — a fat dark dot and a thin
# dark dot read identically to a scanner — so most of the visual punch comes
# from there.
TONES = {
    "dark_logo": 16,      # near-black, drawn fat enough to touch its neighbours
    "dark_plain": 64,     # grey, well below the binarisation midpoint
    "light_logo": 176,    # grey where there would otherwise be paper. Darker
                          # than it needs to be for scanning, because this is
                          # what fills the gaps between the black dots and
                          # makes the mark read as continuous strokes.
                          # Measured: 166 still scans clean but slips under
                          # heavy ink spread, so 176 is the honest floor.
    "light_plain": 255,   # paper
}
# Pure black and white: the mark is carried by dot size alone.
#
# Not the default, and worth knowing why. Roughly half the modules inside the
# mark are *light* modules, which must stay light or the data breaks — so there
# is no black-or-white value that can draw them, and the mark's strokes come out
# full of holes. Grey is the only tone available for those, which is exactly
# what MONO gives up. Offered because it maximises scanning margin and some
# marks (solid silhouettes, no fine lettering) survive the holes fine.
MONO_TONES = {"dark_logo": 0, "dark_plain": 0, "light_logo": 255, "light_plain": 255}
MONO_RADII = {"dark_logo": 0.50, "dark_plain": 0.28, "light_logo": 0.0}

RADII = {
    "dark_logo": 0.50,    # the mark: fat dots that join into strokes
    "dark_plain": 0.30,   # the field: small dots that recede.
                          # Don't shrink these further chasing contrast — measured
                          # against simulated phone photos, smaller field dots
                          # leave too little ink at the module centre and scanning
                          # gets *worse*, not better.
    "light_logo": 0.47,   # fills the gaps in the mark so strokes stay continuous
}


def _luma(rgb: tuple[int, int, int]) -> float:
    """Rec.601 luminance — the number a QR decoder actually thresholds on."""
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def at_luma(rgb: tuple[int, int, int], target: float) -> tuple[int, int, int]:
    """Re-light a colour to an exact luminance, keeping its hue.

    This is the whole trick behind using colour here. A decoder sees only
    luminance, so a teal pinned to the same luminance as our grey scans
    identically — while the eye, which separates hue far better than it
    separates subtle lightness, reads it as much more solid. Colour buys
    apparent contrast for free; it does not buy permission to go darker.
    """
    import colorsys

    r, g, b = (c / 255.0 for c in rgb)
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    lo, hi = 0.0, 1.0
    for _ in range(24):                      # bisect on lightness
        mid = (lo + hi) / 2
        candidate = colorsys.hls_to_rgb(h, mid, sat)
        if _luma(tuple(c * 255 for c in candidate)) < target:
            lo = mid
        else:
            hi = mid
    out = colorsys.hls_to_rgb(h, (lo + hi) / 2, sat)
    return tuple(max(0, min(255, round(c * 255))) for c in out)  # type: ignore[return-value]


def _tone(level: int, ink: tuple[int, int, int]) -> tuple[int, int, int]:
    """Blend between the ink colour and white by a 0-255 lightness."""
    t = level / 255.0
    return tuple(round(c + (255 - c) * t) for c in ink)  # type: ignore[return-value]


def _logo_mask(logo: Image.Image, n: int, coverage: float) -> list[list[bool]]:
    """Reduce the logo to one true/false per QR module.

    Averaging alpha down to ~45 squares and thresholding at 50% works for a
    solid mark and destroys line art: a hairline stroke reduced 40x averages
    to almost nothing and disappears entirely.

    So the threshold is derived instead of fixed. We measure how much of the
    artwork is actually ink at full resolution, then pick the cut that marks
    that same fraction of modules. A solid logo and a single-line drawing both
    come out with their true visual weight.
    """
    k = max(3, int(n * coverage))
    art = logo.copy()

    full = art.split()[-1]
    ink_fraction = sum(
        count * (level / 255.0) for level, count in enumerate(full.histogram())
    ) / max(1, full.width * full.height)
    # Line art needs a nudge: strokes thinner than a module still deserve one.
    target = min(0.55, max(0.04, ink_fraction * 1.9))

    art.thumbnail((k, k), Image.LANCZOS)          # box-averages the alpha
    alpha = art.split()[-1]

    hist = alpha.histogram()
    total = max(1, alpha.width * alpha.height)
    cut, running = 255, 0
    for level in range(255, -1, -1):              # walk down from opaque
        running += hist[level]
        if running / total >= target:
            cut = level
            break
    cut = max(12, min(cut, 200))

    grid = [[False] * n for _ in range(n)]
    ox = (n - alpha.width) // 2
    oy = (n - alpha.height) // 2
    px = alpha.load()
    for y in range(alpha.height):
        for x in range(alpha.width):
            if px[x, y] >= cut:
                gx, gy = ox + x, oy + y
                if 0 <= gx < n and 0 <= gy < n:
                    grid[gy][gx] = True
    return grid


def render_qr(
    data: str,
    px: int,
    ink: tuple[int, int, int],
    strong: bool = True,
    logo: Image.Image | None = None,
    logo_coverage: float = 0.95,
    min_modules: int = 57,
    logo_detail: int = 2,          # kept for call compatibility; unused now
    style: str = "dots",
    tones: dict | None = None,
    radii: dict | None = None,
    accent: tuple[int, int, int] | None = None,
) -> Image.Image:
    """Draw a QR as a field of dots, with rounded finder patterns.

    The three corner squares stay solid and full-contrast — they are what a
    scanner locks onto first, and stippling or tinting those is how pretty QR
    codes end up unscannable.

    A logo is applied by shading: modules inside the mark are drawn at full
    strength, modules outside it in a softer grey. Every module keeps its
    correct light/dark value, so the code carries all of its data and the
    error correction budget is untouched.
    """
    tone = dict(TONES)
    if tones:
        tone.update(tones)
    radii = {**RADII, **(radii or {})}
    if logo is None:
        # Without a logo every dark module is equal; draw them all full size.
        tone["dark_plain"] = 0
        radii["dark_plain"] = 0.47

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H if strong else ERROR_CORRECT_Q,
        box_size=1,
        border=0,
    )
    qr.add_data(data)
    qr.make(fit=True)

    # A denser grid renders the logo more finely. It costs nothing here, but
    # it does shrink the physical modules, so don't reach for a huge version.
    if logo is not None and len(qr.get_matrix()) < min_modules:
        version = qr.version
        while version < 40:
            version += 1
            trial = qrcode.QRCode(version=version, error_correction=ERROR_CORRECT_H,
                                  box_size=1, border=0)
            trial.add_data(data)
            try:
                trial.make(fit=False)
            except Exception:
                continue
            qr = trial
            if len(qr.get_matrix()) >= min_modules:
                break

    matrix = qr.get_matrix()
    n = len(matrix)
    mask = _logo_mask(logo, n, logo_coverage) if logo is not None else None

    SS = 4                                   # supersample, for clean circle edges
    module = max(4, round(px / n))
    size = module * n
    canvas = Image.new("RGB", (size * SS, size * SS), _tone(tone["light_plain"], ink))
    d = ImageDraw.Draw(canvas)
    m = module * SS

    finder = set()
    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
        for dx in range(7):
            for dy in range(7):
                finder.add((ox + dx, oy + dy))

    for y in range(n):
        for x in range(n):
            if (x, y) in finder:
                continue
            in_logo = bool(mask and mask[y][x])
            dark = matrix[y][x]

            if dark:
                key = "dark_logo" if in_logo else "dark_plain"
            elif in_logo:
                key = "light_logo"            # a faint dot where there'd be paper
            else:
                continue                      # plain light module: leave the paper

            r_factor = radii.get(key, 0.47)
            if r_factor <= 0:
                continue                      # e.g. mono mode, which has no fill

            if accent is not None and key in ("dark_logo", "light_logo"):
                # Hue is free; the luminance stays exactly where it verified.
                colour = at_luma(accent, _luma(_tone(tone[key], ink)))
            else:
                colour = _tone(tone[key], ink)
            cx, cy = x * m + m / 2, y * m + m / 2
            if style == "squares":
                half = m * r_factor
                d.rectangle([cx - half, cy - half, cx + half, cy + half], fill=colour)
            else:
                r = m * r_factor
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)

    # Finder patterns: always full contrast, never tinted.
    solid = _tone(0, ink)
    paper = _tone(255, ink)
    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
        x0, y0 = ox * m, oy * m
        outer = 7 * m
        d.rounded_rectangle([x0, y0, x0 + outer, y0 + outer], radius=int(m * 1.9), fill=solid)
        d.rounded_rectangle([x0 + m, y0 + m, x0 + 6 * m, y0 + 6 * m],
                            radius=int(m * 1.3), fill=paper)
        d.rounded_rectangle([x0 + 2 * m, y0 + 2 * m, x0 + 5 * m, y0 + 5 * m],
                            radius=int(m * 0.85), fill=solid)

    return canvas.resize((px, px), Image.LANCZOS)


def decode(img: Image.Image) -> str | None:
    """Read a QR back out of an image, if a decoder is installed."""
    try:
        import zxingcpp
    except ImportError:
        return None
    results = zxingcpp.read_barcodes(img.convert("RGB"))
    return results[0].text if results else None


def scans_reliably(img: Image.Image, expected: str) -> bool | None:
    """True/False if we could check, None if no decoder is installed.

    Three renditions, all of which must decode: crisp, shrunk-and-blurred, and
    a deliberately punishing one standing in for a phone photo of the printed
    card taken across a dim room with an unsteady hand.

    Those numbers are calibrated, not guessed: they were tuned until this check
    agreed with a separate harness that renders whole cards, warps them in
    perspective, simulates ink spread and decodes the result. Loosen them and
    the tool will happily approve codes that fail on the night.

    Deliberately free of random noise. An earlier version blended in
    Image.effect_noise, which is unseeded — so the same input could verify on
    one run and not the next, and the tool would quietly emit a different card
    each time. A reproducible check is worth more here than a realistic one.
    """
    try:
        import zxingcpp  # noqa: F401
    except ImportError:
        return None

    soft = img.resize((700, 700), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.2))

    dim = img.resize((500, 500), Image.LANCZOS).filter(ImageFilter.GaussianBlur(2.6))
    dim = ImageEnhance.Contrast(dim).enhance(0.58)
    dim = ImageEnhance.Brightness(dim).enhance(0.70)

    return all(decode(v) == expected for v in (img, soft, dim))


# Ordered from best-looking to most robust. Grid density is the strongest
# lever by far — a coarser grid means physically larger modules, which is what
# actually survives a blurry photo — so it moves first, and only then does the
# shading contrast get eased.
LOGO_FALLBACKS: list[tuple[int, dict] | None] = [
    (57, {}),
    (49, {}),
    (45, {}),
    (41, {}),
    (41, {"dark_plain": 48, "light_logo": 216}),
    (37, {"dark_plain": 40, "light_logo": 224}),
    None,                                        # give up on the logo
]


def build_verified_qr(url: str, px: int, ink: tuple[int, int, int],
                      logo: Image.Image | None,
                      coverage: float = 0.95, min_modules: int = 57,
                      detail: int = 2, style: str = "dots",
                      mono: bool = False,
                      accent: tuple[int, int, int] | None = None) -> tuple[Image.Image, str]:
    """Render the code, then prove it decodes before handing it back."""
    if logo is None:
        return render_qr(url, px, ink, style=style), "no logo"

    if mono:
        # Nothing to ease off here — the palette is already maximal contrast —
        # so the only lever left is grid density.
        for modules in [m for m in (57, 49, 45, 41, 37) if m <= min_modules] or [37]:
            img = render_qr(url, px, ink, logo=logo, logo_coverage=coverage,
                            min_modules=modules, style=style,
                            tones=MONO_TONES, radii=MONO_RADII, accent=accent)
            note = f"logo in black and white, {modules}-module grid"
            verdict = scans_reliably(img, url)
            if verdict is None:
                return img, note + "  (unverified: pip install zxing-cpp to check)"
            if verdict:
                return img, note + "  verified"
        return render_qr(url, px, ink, style=style), "logo dropped"

    ladder = [f for f in LOGO_FALLBACKS if f is None or f[0] <= min_modules] or [None]
    for attempt in ladder:
        if attempt is None:
            return render_qr(url, px, ink, style=style), "logo dropped — could not make it scan"
        modules, override = attempt
        img = render_qr(url, px, ink, logo=logo, logo_coverage=coverage,
                        min_modules=modules, style=style, tones=override,
                        accent=accent)
        note = f"logo shaded across the code, {modules}-module grid"
        if override:
            note += ", contrast eased"
        verdict = scans_reliably(img, url)
        if verdict is None:
            return img, note + "  (unverified: pip install zxing-cpp to check)"
        if verdict:
            return img, note + "  verified"
    return render_qr(url, px, ink, style=style), "logo dropped"


def load_default_logo() -> Image.Image | None:
    if not DEFAULT_LOGO.exists():
        return None
    try:
        return monochrome_logo(DEFAULT_LOGO)
    except Exception:
        return None


def parse_hex(value: str) -> tuple[int, int, int] | None:
    """'#9b8aa6' -> (155, 138, 166). Blank or malformed gives None (neutral)."""
    value = (value or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None
