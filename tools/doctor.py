#!/usr/bin/env python3
"""Pre-flight check. Run this the week before, and again the morning of.

    tools/doctor.py
    tools/doctor.py --check-url        # also try to reach BASE_URL over the network
"""
from __future__ import annotations

import argparse
import shutil
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
problems = 0
warnings = 0


def ok(label: str, detail: str = "") -> None:
    print(f"  {GREEN}✓{RESET} {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    global warnings
    warnings += 1
    print(f"  {YELLOW}!{RESET} {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    global problems
    problems += 1
    print(f"  {RED}✗{RESET} {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}")


def lan_ip() -> str | None:
    """Whatever address this machine would use to reach the outside world."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))     # TEST-NET-1: routes nowhere, sends nothing
        addr = s.getsockname()[0]
        s.close()
        return addr
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-url", action="store_true",
                    help="try an HTTP request to BASE_URL")
    args = ap.parse_args()

    print(f"\n  {settings.couple_names} — pre-flight check")
    print("  " + "─" * 56)

    # ---------------------------------------------------------- dependencies
    section("Dependencies")
    for module, why in [("fastapi", "web server"), ("PIL", "image processing"),
                        ("qrcode", "QR generation"), ("multipart", "file uploads")]:
        try:
            __import__(module)
            ok(f"{module}", why)
        except ImportError:
            bad(f"{module} missing", "run: .venv/bin/pip install -r requirements.txt")

    try:
        import pillow_heif  # noqa: F401
        ok("pillow-heif", "iPhone HEIC photos will convert")
    except ImportError:
        warn("pillow-heif missing", "iPhone HEIC uploads will fail to render")

    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        ok("ffmpeg", "video uploads supported")
    else:
        bad("ffmpeg not found", "videos will be rejected — brew install ffmpeg")

    # --------------------------------------------------------------- storage
    section("Storage")
    settings.ensure_dirs()
    for label, path in [("data", settings.data_dir), ("originals", settings.originals_dir),
                        ("thumbnails", settings.thumbs_dir), ("display", settings.display_dir)]:
        probe = path / ".write-test"
        try:
            probe.write_text("x")
            probe.unlink()
            ok(f"{label} directory writable", str(path))
        except Exception as exc:
            bad(f"{label} directory not writable", f"{path}: {exc}")

    usage = shutil.disk_usage(settings.data_dir)
    free_gb = usage.free / 1e9
    detail = f"{free_gb:.1f} GB free of {usage.total / 1e9:.0f} GB"
    if free_gb < 10:
        bad("very little disk space", detail + " — 150 guests can produce 20 GB+")
    elif free_gb < 30:
        warn("disk space is tight", detail)
    else:
        ok("disk space", detail)

    # -------------------------------------------------------------- database
    section("Database")
    try:
        con = sqlite3.connect(settings.db_path)
        con.row_factory = sqlite3.Row
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity == "ok":
            ok("database integrity")
        else:
            bad("database integrity", integrity)

        row = con.execute(
            "SELECT COUNT(*) n, SUM(status='failed') f, SUM(status='processing') p, "
            "COALESCE(SUM(bytes),0) b FROM media"
        ).fetchone()
        ok("items stored", f"{row['n']} ({row['b'] / 1e6:.0f} MB)")
        if row["f"]:
            warn(f"{row['f']} item(s) failed to process", "retry them in /admin")
        if row["p"]:
            warn(f"{row['p']} item(s) still processing",
                 "normal if the server is running; stale otherwise")

        # Files on disk with no row, and rows with no file.
        known = {r["stored_name"] for r in con.execute("SELECT stored_name FROM media")}
        on_disk = {p.name for p in settings.originals_dir.iterdir() if p.is_file()}
        orphan_files = on_disk - known
        missing_files = {n for n in known if not (settings.originals_dir / n).exists()}
        if orphan_files:
            warn(f"{len(orphan_files)} file(s) on disk with no database row")
        if missing_files:
            bad(f"{len(missing_files)} database row(s) point at a missing file")
        if not orphan_files and not missing_files:
            ok("database and disk agree")
        con.close()
    except Exception as exc:
        bad("cannot read the database", str(exc))

    # ------------------------------------------------------------ networking
    section("Networking")
    base = settings.base_url
    print(f"  {DIM}BASE_URL is {base}{RESET}")

    if "localhost" in base or "127.0.0.1" in base:
        bad("BASE_URL points at localhost",
            "guests' phones cannot reach that — QR codes will be dead")
    elif base.startswith("http://"):
        warn("BASE_URL is plain HTTP",
             "the 'Take a photo' camera button needs HTTPS and will be hidden")
    else:
        ok("BASE_URL looks reachable and secure")

    ip = lan_ip()
    if ip:
        ok("this machine's LAN address", f"http://{ip}:{settings.port}")
        if ip not in base and "localhost" not in base:
            print(f"  {DIM}  (fine if you're using a domain or a tunnel){RESET}")
    else:
        warn("could not determine a LAN address", "not on a network?")

    if args.check_url:
        try:
            with urllib.request.urlopen(base + "/api/stats", timeout=10) as r:
                ok(f"BASE_URL responds", f"HTTP {r.status}")
        except urllib.error.HTTPError as exc:
            warn("BASE_URL responded with an error", f"HTTP {exc.code}")
        except Exception as exc:
            bad("BASE_URL is not reachable from this machine", str(exc))
    else:
        print(f"  {DIM}  pass --check-url to actually try connecting{RESET}")

    # ------------------------------------------------------------- behaviour
    section("Settings")
    pw_file = settings.data_dir / "admin_password.txt"
    if settings.admin_password:
        ok("admin password set", "from the environment")
    elif pw_file.exists():
        ok("admin password", f"generated, stored in {pw_file}")
    else:
        warn("no admin password yet", "one is generated when the server first starts")

    ok("moderation", settings.moderation)
    ok("guest gallery", "on" if settings.allow_guest_gallery else "off")
    ok("guest downloads", "on" if settings.allow_guest_download else "off")
    ok("max upload size", f"{settings.max_file_mb} MB")
    if settings.post_event:
        ok("post-event mode", "on — upload page uses the after-the-wedding copy")

    # ----------------------------------------------------------------- verdict
    print("\n  " + "─" * 56)
    if problems:
        print(f"  {RED}{problems} problem(s){RESET}"
              + (f" and {warnings} warning(s)" if warnings else "") + " — fix before the day\n")
        return 1
    if warnings:
        print(f"  {YELLOW}{warnings} warning(s){RESET}, nothing fatal\n")
        return 0
    print(f"  {GREEN}All clear.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
