"""Configuration, loaded from environment with a tiny .env reader.

Everything the couple would want to change lives here, so the rest of the
code never hard-codes a name, a colour or a path.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env support so we don't need another dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- The event ------------------------------------------------------
    couple_names = _str("COUPLE_NAMES", "Max & Rachel")
    event_date = _str("EVENT_DATE", "")            # free text, e.g. "September 14, 2026"
    event_hashtag = _str("EVENT_HASHTAG", "")      # optional, e.g. "#MaxAndRachel"
    welcome_message = _str(
        "WELCOME_MESSAGE",
        "Help us see today through your eyes. Every photo and video you add "
        "lands straight in our album.",
    )
    thanks_message = _str("THANKS_MESSAGE", "Thank you — we can't wait to see these.")

    # Flip this on after the wedding: the copy changes from "today" to
    # "the wedding", and the page nudges people to go through their camera roll.
    post_event = _bool("POST_EVENT", False)
    post_event_message = _str(
        "POST_EVENT_MESSAGE",
        "Still got photos on your phone from the big day? We'd love to have them. "
        "Scroll back through your camera roll — nothing is too small.",
    )

    # --- Look and feel --------------------------------------------------
    accent = _str("ACCENT", "#8a6f4e")             # warm gold
    # Hue for the logo inside the QR code. Only the hue is used — the
    # luminance is pinned to values that are verified to scan, so changing
    # this can't break the code. Set blank for neutral grey.
    qr_accent = _str("QR_ACCENT", "#9b8aa6")       # dusty lilac
    accent_soft = _str("ACCENT_SOFT", "#efe7db")
    ink = _str("INK", "#2c2825")
    paper = _str("PAPER", "#fbf8f3")

    # --- Networking -----------------------------------------------------
    # Used to build QR codes. e.g. https://photos.ouwedding.com
    base_url = _str("BASE_URL", "http://localhost:8000").rstrip("/")
    host = _str("HOST", "0.0.0.0")
    port = _int("PORT", 8000)

    # --- Storage --------------------------------------------------------
    data_dir = Path(_str("DATA_DIR", str(ROOT / "data")))

    # --- Limits ---------------------------------------------------------
    max_file_mb = _int("MAX_FILE_MB", 512)
    max_files_per_batch = _int("MAX_FILES_PER_BATCH", 40)
    upload_rate_per_hour = _int("UPLOAD_RATE_PER_HOUR", 400)   # per IP address

    # --- Behaviour ------------------------------------------------------
    # "off"       : everything shows everywhere immediately
    # "slideshow" : uploads appear in the gallery but need a thumbs-up
    #               before they hit the projector
    # "all"       : nothing is public until approved
    moderation = _str("MODERATION", "off").lower()
    allow_guest_gallery = _bool("ALLOW_GUEST_GALLERY", True)
    allow_guest_download = _bool("ALLOW_GUEST_DOWNLOAD", False)
    slideshow_seconds = _int("SLIDESHOW_SECONDS", 7)
    slideshow_max_video_seconds = _int("SLIDESHOW_MAX_VIDEO_SECONDS", 25)

    admin_password = _str("ADMIN_PASSWORD", "")
    # Set to true only when a reverse proxy (Caddy, nginx, Fly, Cloudflare)
    # sits in front and sets X-Forwarded-For. Otherwise clients can spoof it.
    trust_proxy = _bool("TRUST_PROXY", False)

    # --- Derived --------------------------------------------------------
    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def display_dir(self) -> Path:
        return self.data_dir / "display"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wedding.db"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.originals_dir, self.thumbs_dir, self.display_dir):
            d.mkdir(parents=True, exist_ok=True)

    def theme(self) -> dict:
        return {
            "accent": self.accent,
            "accent_soft": self.accent_soft,
            "ink": self.ink,
            "paper": self.paper,
        }


settings = Settings()
