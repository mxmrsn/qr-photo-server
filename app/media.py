"""Turning whatever a phone hands us into something a browser can show.

Phones upload HEIC, HEVC .mov, 48-megapixel JPEGs and the occasional
sideways video. This module normalises all of it into two derivatives:

  thumb   ~ 700px  JPEG  -- gallery grid, admin grid
  display ~ 2200px JPEG  -- lightbox and the projector slideshow

Originals are never touched; they are what the couple keeps.
"""
from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

try:  # iPhones default to HEIC and this is the only way to read it
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover - optional
    HEIF_OK = False

from .config import settings

Image.MAX_IMAGE_PIXELS = 400_000_000  # generous, but still a decompression-bomb guard

THUMB_PX = 700
DISPLAY_PX = 2200

IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
    ".gif", ".bmp", ".tif", ".tiff", ".avif",
}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".3gp", ".mpg", ".mpeg"}

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def has_ffmpeg() -> bool:
    return bool(FFMPEG and FFPROBE)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def safe_extension(filename: str | None, content_type: str | None) -> str:
    """Never trust a client filename; keep only a known-good extension."""
    ext = ""
    if filename:
        ext = Path(filename).suffix.lower()
        ext = re.sub(r"[^a-z0-9.]", "", ext)[:8]
    if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
        guessed = mimetypes.guess_extension(content_type or "") or ""
        ext = guessed.lower()
    if ext == ".jpe":
        ext = ".jpg"
    if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
        ext = ""
    return ext


def classify(ext: str, content_type: str | None) -> str | None:
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    return None


def clean_display_name(filename: str | None) -> str:
    if not filename:
        return "upload"
    name = Path(filename).name
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name[:180] or "upload"


# --------------------------------------------------------------------------
# EXIF
# --------------------------------------------------------------------------

def _exif_taken_at(img: Image.Image) -> float | None:
    try:
        exif = img.getexif()
        if not exif:
            return None
        # 36867 DateTimeOriginal, 36868 DateTimeDigitized, 306 DateTime
        for tag in (36867, 36868, 306):
            raw = exif.get(tag)
            if not raw:
                # DateTimeOriginal often lives in the Exif IFD
                ifd = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
                raw = ifd.get(tag) if ifd else None
            if raw:
                try:
                    return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").timestamp()
                except ValueError:
                    continue
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Derivative generation
# --------------------------------------------------------------------------

def _save_jpeg(img: Image.Image, path: Path, max_px: int, quality: int) -> tuple[int, int]:
    out = img.copy()
    out.thumbnail((max_px, max_px), Image.LANCZOS)
    if out.mode not in ("RGB", "L"):
        if out.mode in ("RGBA", "LA", "P"):
            out = out.convert("RGBA")
            bg = Image.new("RGB", out.size, (255, 255, 255))
            bg.paste(out, mask=out.split()[-1])
            out = bg
        else:
            out = out.convert("RGB")
    out.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.size


def _process_image(src: Path, stem: str) -> dict:
    with Image.open(src) as img:
        img.load()
        taken_at = _exif_taken_at(img)
        img = ImageOps.exif_transpose(img)          # honour phone orientation
        width, height = img.size
        thumb_name = f"{stem}.jpg"
        display_name = f"{stem}.jpg"
        _save_jpeg(img, settings.thumbs_dir / thumb_name, THUMB_PX, 78)
        _save_jpeg(img, settings.display_dir / display_name, DISPLAY_PX, 86)
    return {
        "width": width,
        "height": height,
        "taken_at": taken_at,
        "thumb_name": thumb_name,
        "display_name": display_name,
        "duration": None,
    }


def _ffprobe(src: Path) -> dict:
    if not FFPROBE:
        return {}
    try:
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(src)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            return {}
        return json.loads(proc.stdout or "{}")
    except Exception:
        return {}


def _video_creation_time(info: dict) -> float | None:
    tags = (info.get("format") or {}).get("tags") or {}
    raw = tags.get("creation_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def silent_path(stem: str) -> Path:
    """Where the audio-free playback copy lives."""
    return settings.display_dir / f"{stem}.silent.mp4"


def make_silent_copy(src: Path, stem: str) -> bool:
    """Write a video-only rendition for the projector.

    The slideshow plays muted, but muted is a browser setting — the audio is
    still in the file, one stray unmute away from a reception hall. This strips
    the track outright. It's a stream copy, so it costs a second and no quality.
    """
    if not FFMPEG:
        return False
    dest = silent_path(stem)
    cmd = [FFMPEG, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
           "-an",                       # drop every audio stream
           "-c:v", "copy",              # no re-encode
           "-movflags", "+faststart",   # so it starts playing before it's all there
           str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        # Some containers won't take a straight copy; re-encode as a fallback.
        cmd = [FFMPEG, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        except subprocess.TimeoutExpired:
            dest.unlink(missing_ok=True)
            return False
        if proc.returncode != 0:
            dest.unlink(missing_ok=True)
            return False
    return True


def _process_video(src: Path, stem: str) -> dict:
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg is not installed, cannot make a video poster frame")

    info = _ffprobe(src)
    duration = None
    try:
        duration = float((info.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None

    poster = settings.display_dir / f"{stem}.poster.jpg"
    seek = 1.0 if (duration or 0) > 2.5 else 0.0
    for attempt_seek in (seek, 0.0):
        cmd = [FFMPEG, "-nostdin", "-y", "-ss", str(attempt_seek), "-i", str(src),
               "-frames:v", "1", "-q:v", "3", "-f", "image2", str(poster)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError("ffmpeg timed out making a poster frame")
        if proc.returncode == 0 and poster.exists() and poster.stat().st_size > 0:
            break
    else:
        raise RuntimeError("could not extract a frame from this video")

    # Read dimensions off the poster: ffmpeg has already applied any rotation.
    with Image.open(poster) as img:
        img.load()
        width, height = img.size
        thumb_name = f"{stem}.jpg"
        display_name = f"{stem}.jpg"
        _save_jpeg(img, settings.thumbs_dir / thumb_name, THUMB_PX, 78)
        _save_jpeg(img, settings.display_dir / display_name, DISPLAY_PX, 86)
    poster.unlink(missing_ok=True)
    make_silent_copy(src, stem)

    return {
        "width": width,
        "height": height,
        "taken_at": _video_creation_time(info),
        "thumb_name": thumb_name,
        "display_name": display_name,
        "duration": duration,
    }


def process(src: Path, stem: str, kind: str) -> dict:
    """Build derivatives. Raises on failure; caller records the error."""
    started = time.time()
    result = _process_image(src, stem) if kind == "image" else _process_video(src, stem)
    result["process_seconds"] = round(time.time() - started, 2)
    return result
