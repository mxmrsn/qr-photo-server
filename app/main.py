"""Wedding photo & video collection server.

Guests scan a QR code on their table, land on an upload page, and their
photos go straight into the couple's album. Optional projector slideshow
shows them as they arrive.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import mimetypes
import os
import secrets
import time
import uuid
import zipfile
from collections import defaultdict, deque
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, guests, media, transcribe
from .config import ROOT, settings

# --------------------------------------------------------------------------
# Admin credentials
# --------------------------------------------------------------------------

ADMIN_COOKIE = "wedding_admin"


def _resolve_admin_password() -> str:
    """Use the configured password, else generate and persist one once."""
    if settings.admin_password:
        return settings.admin_password
    settings.ensure_dirs()
    stash = settings.data_dir / "admin_password.txt"
    if stash.exists():
        existing = stash.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    generated = secrets.token_urlsafe(9)
    stash.write_text(generated + "\n", encoding="utf-8")
    try:
        os.chmod(stash, 0o600)
    except OSError:
        pass
    return generated


ADMIN_PASSWORD = ""


def _admin_cookie_value() -> str:
    return hmac.new(
        ADMIN_PASSWORD.encode("utf-8"), b"qr-photo-admin-v1", hashlib.sha256
    ).hexdigest()


def is_admin(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token or not ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(token, _admin_cookie_value())


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Admin sign-in required")


# --------------------------------------------------------------------------
# Rate limiting (per IP, sliding window)
# --------------------------------------------------------------------------

_hits: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def rate_limit(ip: str, limit: int, window: float = 3600.0) -> bool:
    now = time.time()
    bucket = _hits[ip]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    if len(_hits) > 5000:  # keep the dict from growing without bound
        for key in [k for k, v in _hits.items() if not v][:1000]:
            _hits.pop(key, None)
    return True


# --------------------------------------------------------------------------
# Background processing queue
# --------------------------------------------------------------------------

_queue: asyncio.Queue[tuple[str, str]] | None = None
_workers: list[asyncio.Task] = []


def _worker_count() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def _process_one(media_id: str) -> None:
    """Runs in a worker thread: build derivatives, update the row."""
    row = db.query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None:
        return
    src = settings.originals_dir / row["stored_name"]
    if not src.exists():
        db.execute(
            "UPDATE media SET status='failed', error=? WHERE id=?",
            ("original file is missing", media_id),
        )
        return
    try:
        result = media.process(src, media_id, row["kind"])
    except Exception as exc:  # noqa: BLE001 - record and move on
        db.execute(
            "UPDATE media SET status='failed', error=? WHERE id=?",
            (str(exc)[:400], media_id),
        )
        return

    approved = 0 if settings.moderation in {"all", "slideshow"} else 1
    db.execute(
        """
        UPDATE media
           SET status='ready', width=?, height=?, duration=?, taken_at=COALESCE(?, taken_at),
               thumb_name=?, display_name=?, approved=?, error=NULL
         WHERE id=?
        """,
        (
            result["width"], result["height"], result["duration"], result["taken_at"],
            result["thumb_name"], result["display_name"], approved, media_id,
        ),
    )


def _silence_one(media_id: str) -> None:
    """Build the audio-free copy for a video that predates this feature."""
    row = db.query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None or row["kind"] != "video":
        return
    src = settings.originals_dir / row["stored_name"]
    if not src.exists() or media.silent_path(media_id).exists():
        return
    if media.make_silent_copy(src, media_id):
        print(f"[silence] {media_id}: audio stripped for playback")
    else:
        print(f"[silence] {media_id}: could not strip audio, player stays muted")


def _transcribe_one(media_id: str) -> None:
    """Runs in a worker thread, after the video is already on screen.

    Deliberately a separate stage: a guest's clip should reach the projector as
    soon as its poster frame exists, and gain captions a moment later, rather
    than waiting on speech recognition before anyone can see it.
    """
    row = db.query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None or row["kind"] != "video":
        return
    src = settings.originals_dir / row["stored_name"]
    if not src.exists():
        return
    try:
        segments = transcribe.transcribe(src)
    except Exception as exc:  # noqa: BLE001 - captions are never worth failing over
        print(f"[transcribe] {media_id}: {exc}")
        return
    db.execute("UPDATE media SET transcript = ? WHERE id = ?",
               (transcribe.to_json(segments), media_id))
    if segments:
        print(f"[transcribe] {media_id}: {len(segments)} caption segment(s)")


async def _worker(name: str) -> None:
    assert _queue is not None
    while True:
        job, media_id = await _queue.get()
        try:
            if job == "derive":
                await asyncio.to_thread(_process_one, media_id)
                # Video that landed successfully gets queued for captions.
                row = db.query_one(
                    "SELECT kind, status FROM media WHERE id = ?", (media_id,))
                if (row and row["kind"] == "video" and row["status"] == "ready"
                        and transcribe.available()):
                    await _queue.put(("transcribe", media_id))
            elif job == "transcribe":
                await asyncio.to_thread(_transcribe_one, media_id)
            elif job == "silence":
                await asyncio.to_thread(_silence_one, media_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] unexpected failure on {job} {media_id}: {exc}")
        finally:
            _queue.task_done()


async def enqueue(media_id: str, job: str = "derive") -> None:
    if _queue is not None:
        await _queue.put((job, media_id))


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
app = FastAPI(title="Wedding Photo Server", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")


@app.on_event("startup")
async def _startup() -> None:
    global _queue, ADMIN_PASSWORD
    settings.ensure_dirs()
    db.init_db()
    ADMIN_PASSWORD = _resolve_admin_password()

    _queue = asyncio.Queue()
    for i in range(_worker_count()):
        _workers.append(asyncio.create_task(_worker(f"worker-{i}")))

    # Anything left mid-flight by a restart goes back in the queue.
    stuck = db.query("SELECT id FROM media WHERE status='processing'")
    for row in stuck:
        await _queue.put(("derive", row["id"]))
    # Videos uploaded before the audio-strip existed.
    for row in db.query("SELECT id FROM media WHERE kind='video' AND status='ready'"):
        if not media.silent_path(row["id"]).exists():
            await _queue.put(("silence", row["id"]))

    # Videos that never got captions (added later, or a restart mid-job).
    if transcribe.available():
        for row in db.query(
            "SELECT id FROM media WHERE kind='video' AND status='ready' "
            "AND transcript IS NULL"
        ):
            await _queue.put(("transcribe", row["id"]))

    banner = [
        "",
        "  " + "=" * 62,
        f"   {settings.couple_names} — photo & video collection",
        "  " + "=" * 62,
        f"   Guest upload : {settings.base_url}/",
        f"   Gallery      : {settings.base_url}/gallery",
        f"   Slideshow    : {settings.base_url}/slideshow",
        f"   Admin        : {settings.base_url}/admin",
        f"   Admin password: {ADMIN_PASSWORD}",
        f"   Captions     : "
        f"{'on (' + settings.whisper_model + ')' if transcribe.available() else 'off'}",
        f"   Moderation   : {settings.moderation}   |  ffmpeg: "
        f"{'yes' if media.has_ffmpeg() else 'NO (videos will fail)'}",
        f"   Requeued {len(stuck)} unfinished upload(s)" if stuck else "",
        "  " + "=" * 62,
        "",
    ]
    print("\n".join(line for line in banner if line != "" or True))


@app.on_event("shutdown")
async def _shutdown() -> None:
    for task in _workers:
        task.cancel()


def page(request: Request, template: str, **extra):
    ctx = {
        "s": settings,
        "theme": settings.theme(),
        "is_admin": is_admin(request),
        "ffmpeg": media.has_ffmpeg(),
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, template, ctx)


# --------------------------------------------------------------------------
# Guest pages
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return page(request, "upload.html", table_id=None)


@app.get("/t/{table_id}", response_class=HTMLResponse)
async def table_upload(request: Request, table_id: str):
    clean = "".join(ch for ch in table_id if ch.isalnum() or ch in " -_")[:40].strip()
    return page(request, "upload.html", table_id=clean or None)


@app.get("/gallery", response_class=HTMLResponse)
async def gallery(request: Request):
    if not settings.allow_guest_gallery and not is_admin(request):
        raise HTTPException(status_code=404, detail="Not found")
    return page(request, "gallery.html")


@app.get("/slideshow", response_class=HTMLResponse)
async def slideshow(request: Request):
    return page(request, "slideshow.html")


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------

CHUNK = 1024 * 1024


def resolve_guest(guest_id: str, guest_name: str, table_id: str) -> tuple[int | None, str, str]:
    """A roster pick wins over typed text; typed text still works on its own.

    The name and table are copied onto the item rather than only referenced, so
    attribution survives a sync to an instance that has no seating chart.
    """
    if guest_id:
        try:
            record = guests.by_id(int(guest_id))
        except (TypeError, ValueError):
            record = None
        if record:
            return (record["id"], record["name"],
                    record["table_name"] or table_id.strip()[:40])
    return None, guest_name.strip()[:80], table_id.strip()[:40]


@app.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile,
    guest_name: str = Form(""),
    message: str = Form(""),
    table_id: str = Form(""),
    guest_id: str = Form(""),
):
    ip = client_ip(request)
    if not rate_limit(ip, settings.upload_rate_per_hour):
        raise HTTPException(status_code=429, detail="That's a lot of uploads! Try again shortly.")

    ext = media.safe_extension(file.filename, file.content_type)
    kind = media.classify(ext, file.content_type)
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail=f"'{media.clean_display_name(file.filename)}' isn't a photo or video we can read.",
        )
    if kind == "video" and not media.has_ffmpeg():
        raise HTTPException(status_code=503, detail="Video uploads aren't available right now.")
    if not ext:
        ext = ".jpg" if kind == "image" else ".mp4"

    media_id = uuid.uuid4().hex
    stored_name = f"{media_id}{ext}"
    dest = settings.originals_dir / stored_name

    written = 0
    hasher = hashlib.sha256()
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.max_file_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"That file is over the {settings.max_file_mb} MB limit.",
                    )
                hasher.update(chunk)
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Upload failed — please try again.")
    finally:
        await file.close()

    if written == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="That file came through empty.")

    resolved_id, resolved_name, resolved_table = resolve_guest(guest_id, guest_name, table_id)
    digest = hasher.hexdigest()

    # Same bytes already here? Someone double-tapped, or is re-uploading their
    # camera roll after the wedding. Keep the first copy and its attribution.
    existing = db.query_one("SELECT id FROM media WHERE sha256 = ?", (digest,))
    if existing:
        dest.unlink(missing_ok=True)
        return {"ok": True, "id": existing["id"], "kind": kind,
                "bytes": written, "duplicate": True}

    db.execute(
        """
        INSERT INTO media (id, stored_name, original_name, kind, mime, bytes,
                           guest_name, message, table_id, uploaded_at, status,
                           approved, uploader_ip, sha256, source, guest_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,'processing',1,?,?,?,?)
        """,
        (
            media_id, stored_name, media.clean_display_name(file.filename), kind,
            (file.content_type or "")[:100], written,
            resolved_name or None, message.strip()[:500] or None,
            resolved_table or None, time.time(), ip, digest,
            "post" if settings.post_event else "venue", resolved_id,
        ),
    )
    await enqueue(media_id)
    return {"ok": True, "id": media_id, "kind": kind, "bytes": written}


@app.post("/api/name")
async def api_name(request: Request):
    """Attach a name to items a guest already sent, if they typed it late."""
    body = await request.json()
    ids = [str(i) for i in (body.get("ids") or [])][:200]
    name = str(body.get("guest_name") or "").strip()[:80]
    if not ids or not name:
        raise HTTPException(status_code=400, detail="Need ids and a name")
    ip = client_ip(request)
    placeholders = ",".join("?" for _ in ids)
    # Only let a device rename what it uploaded itself.
    db.execute(
        f"UPDATE media SET guest_name = ? "
        f"WHERE id IN ({placeholders}) AND uploader_ip = ? "
        f"AND (guest_name IS NULL OR guest_name = '')",
        (name, *ids, ip),
    )
    return {"ok": True}


@app.post("/api/note")
async def api_note(
    request: Request,
    guest_name: str = Form(""),
    message: str = Form(...),
    table_id: str = Form(""),
):
    text = message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write us something first!")
    if not rate_limit("note:" + client_ip(request), 30):
        raise HTTPException(status_code=429, detail="Slow down a moment.")
    note_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO guestbook (id, guest_name, message, table_id, created_at, approved, uploader_ip)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            note_id, guest_name.strip()[:80] or None, text[:1000],
            table_id.strip()[:40] or None, time.time(),
            0 if settings.moderation == "all" else 1, client_ip(request),
        ),
    )
    return {"ok": True, "id": note_id}


# --------------------------------------------------------------------------
# Media feeds
# --------------------------------------------------------------------------

def _public_fields(row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "kind": row["kind"],
        "width": row["width"],
        "height": row["height"],
        "duration": row["duration"],
        "guest_name": row["guest_name"],
        "message": row["message"],
        "table_id": row["table_id"],
        "uploaded_at": row["uploaded_at"],
        "thumb": f"/m/{row['id']}/thumb.jpg",
        "display": f"/m/{row['id']}/display.jpg",
        "video": f"/m/{row['id']}/video" if row["kind"] == "video" else None,
    }


@app.get("/api/media")
async def api_media(
    request: Request,
    after: int = 0,
    before: int = 0,
    limit: int = 60,
    order: str = "new",
):
    if not settings.allow_guest_gallery and not is_admin(request):
        raise HTTPException(status_code=404, detail="Not found")
    limit = max(1, min(limit, 200))
    where = db.visible_clause("gallery")
    params: list = []
    if after:
        where += " AND seq > ?"
        params.append(after)
    if before:
        where += " AND seq < ?"
        params.append(before)
    direction = "ASC" if order == "old" else "DESC"
    rows = db.query(
        f"SELECT * FROM media WHERE {where} ORDER BY seq {direction} LIMIT ?",
        (*params, limit),
    )
    return {"items": [_public_fields(r) for r in rows], "count": len(rows)}


@app.get("/api/slideshow")
async def api_slideshow(after: int = 0, before: int = 0, limit: int = 300):
    """Feed for the projector: newest first, plus a 'fresh' flag.

    `after` polls for new arrivals; `before` pages backwards so the projector
    can build up the whole evening rather than looping the most recent page.
    """
    limit = max(1, min(limit, 500))
    where = db.visible_clause("slideshow")
    params: list = []
    if after:
        where += " AND seq > ?"
        params.append(after)
    if before:
        where += " AND seq < ?"
        params.append(before)
    rows = db.query(
        f"SELECT * FROM media WHERE {where} ORDER BY seq DESC LIMIT ?", (*params, limit)
    )
    now = time.time()
    items = []
    for r in rows:
        item = _public_fields(r)
        item["fresh"] = (now - (r["uploaded_at"] or 0)) < 180
        item["featured"] = bool(r["featured"])
        if r["kind"] == "video":
            item["captions"] = transcribe.from_json(r["transcript"])
            item["video"] = f"/m/{r['id']}/silent.mp4"
            item["silent"] = media.silent_path(r["id"]).exists()
        items.append(item)
    head = db.query_one(f"SELECT MAX(seq) AS m FROM media WHERE {db.visible_clause('slideshow')}")
    return {"items": items, "head": (head["m"] if head and head["m"] else 0)}


@app.get("/api/notes")
async def api_notes(limit: int = 50):
    limit = max(1, min(limit, 200))
    approved = " AND approved = 1" if settings.moderation == "all" else ""
    rows = db.query(
        f"SELECT id, guest_name, message, table_id, created_at FROM guestbook "
        f"WHERE hidden = 0{approved} ORDER BY seq DESC LIMIT ?",
        (limit,),
    )
    return {"items": [dict(r) for r in rows]}


@app.get("/api/stats")
async def api_stats():
    c = db.counts()
    return {
        "photos": c.get("images", 0),
        "videos": c.get("videos", 0),
        "notes": c.get("notes", 0),
        "contributors": c.get("contributors", 0),
        "processing": c.get("processing", 0),
    }


# --------------------------------------------------------------------------
# Serving files
# --------------------------------------------------------------------------

def _row_or_404(media_id: str):
    row = db.query_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


def _guard_visibility(request: Request, row) -> None:
    if is_admin(request):
        return
    if row["hidden"] or row["status"] != "ready":
        raise HTTPException(status_code=404, detail="Not found")
    if settings.moderation == "all" and not row["approved"]:
        raise HTTPException(status_code=404, detail="Not found")


def _serve(path: Path, mime: str, filename: str | None = None) -> FileResponse:
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return FileResponse(path, media_type=mime, headers=headers)


@app.get("/m/{media_id}/thumb.jpg")
async def serve_thumb(request: Request, media_id: str):
    row = _row_or_404(media_id)
    _guard_visibility(request, row)
    if not row["thumb_name"]:
        raise HTTPException(status_code=404, detail="Still processing")
    return _serve(settings.thumbs_dir / row["thumb_name"], "image/jpeg")


@app.get("/m/{media_id}/display.jpg")
async def serve_display(request: Request, media_id: str):
    row = _row_or_404(media_id)
    _guard_visibility(request, row)
    if not row["display_name"]:
        raise HTTPException(status_code=404, detail="Still processing")
    return _serve(settings.display_dir / row["display_name"], "image/jpeg")


@app.get("/m/{media_id}/video")
async def serve_video(request: Request, media_id: str):
    row = _row_or_404(media_id)
    _guard_visibility(request, row)
    if row["kind"] != "video":
        raise HTTPException(status_code=404, detail="Not a video")
    path = settings.originals_dir / row["stored_name"]
    mime = row["mime"] or mimetypes.guess_type(row["stored_name"])[0] or "video/mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    # FileResponse handles Range requests, which phones need for scrubbing.
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/m/{media_id}/silent.mp4")
async def serve_silent(request: Request, media_id: str):
    """Video with the audio track removed — what the projector plays."""
    row = _row_or_404(media_id)
    _guard_visibility(request, row)
    if row["kind"] != "video":
        raise HTTPException(status_code=404, detail="Not a video")
    path = media.silent_path(media_id)
    if not path.exists():
        # Not built yet (or a restart caught it mid-flight). The player is
        # muted regardless, so falling back is safe rather than showing nothing.
        path = settings.originals_dir / row["stored_name"]
        if not path.exists():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, media_type=row["mime"] or "video/mp4",
                            headers={"Cache-Control": "public, max-age=86400"})
    return FileResponse(path, media_type="video/mp4",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/m/{media_id}/original")
async def serve_original(request: Request, media_id: str):
    row = _row_or_404(media_id)
    _guard_visibility(request, row)
    if not settings.allow_guest_download and not is_admin(request):
        raise HTTPException(status_code=403, detail="Downloads are turned off")
    path = settings.originals_dir / row["stored_name"]
    mime = row["mime"] or mimetypes.guess_type(row["stored_name"])[0] or "application/octet-stream"
    return _serve(path, mime, filename=row["original_name"] or row["stored_name"])


# --------------------------------------------------------------------------
# The seating chart
# --------------------------------------------------------------------------

@app.get("/api/guests")
async def api_guests(request: Request, q: str = ""):
    """Typeahead for the upload page."""
    if not settings.guest_list_public and not is_admin(request):
        return {"items": [], "enabled": False}
    query = q.strip()
    if len(query) < 1:
        return {"items": [], "enabled": True}
    return {"items": guests.search(query, limit=12), "enabled": True}


@app.get("/api/guests/enabled")
async def api_guests_enabled(request: Request):
    """Does this event have a seating chart worth searching?"""
    if not settings.guest_list_public and not is_admin(request):
        return {"enabled": False, "count": 0}
    row = db.query_one("SELECT COUNT(*) n FROM guests")
    return {"enabled": True, "count": row["n"] if row else 0}


@app.get("/api/admin/guests")
async def admin_guests(request: Request):
    require_admin(request)
    roster = guests.all_guests()
    tables: dict[str, int] = {}
    for g in roster:
        key = g["table_name"] or "(no table)"
        tables[key] = tables.get(key, 0) + 1
    return {"items": roster, "tables": tables, "count": len(roster)}


@app.get("/api/admin/guests.csv")
async def admin_guests_csv(request: Request):
    require_admin(request)
    return StreamingResponse(
        io.BytesIO(guests.to_csv().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="seating-chart.csv"'},
    )


@app.post("/api/admin/guests/import")
async def admin_guests_import(
    request: Request,
    file: UploadFile,
    mode: str = Form("merge"),
):
    """Take a seating spreadsheet. 'merge' updates by name, 'replace' starts over."""
    require_admin(request)
    raw = await file.read()
    await file.close()
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That CSV is suspiciously large.")
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=400, detail="Could not read that file as text.")

    rows, warnings = guests.parse_csv(text)
    if not rows:
        raise HTTPException(status_code=400, detail=" ".join(warnings) or "Nothing to import.")

    if mode == "replace":
        total = guests.replace_all(rows)
        result = {"replaced": total, "added": total, "updated": 0}
    else:
        added, updated = guests.merge(rows)
        result = {"added": added, "updated": updated}

    # Re-link any uploads whose typed name now matches someone on the chart.
    linked = 0
    for row in db.query(
        "SELECT id, guest_name FROM media "
        "WHERE guest_id IS NULL AND guest_name IS NOT NULL AND guest_name != ''"
    ):
        match = guests.by_name(row["guest_name"])
        if match:
            db.execute(
                "UPDATE media SET guest_id = ?, table_id = COALESCE(?, table_id) WHERE id = ?",
                (match["id"], match["table_name"], row["id"]),
            )
            linked += 1

    return {"ok": True, **result, "linked_existing_uploads": linked,
            "warnings": warnings[:10]}


@app.post("/api/admin/guests/edit")
async def admin_guests_edit(request: Request):
    require_admin(request)
    body = await request.json()
    action = body.get("action")

    if action == "delete":
        db.execute("DELETE FROM guests WHERE id = ?", (body.get("id"),))
        db.execute("UPDATE media SET guest_id = NULL WHERE guest_id = ?", (body.get("id"),))
        return {"ok": True}

    name = guests.tidy(str(body.get("name") or ""))[:120]
    if not name:
        raise HTTPException(status_code=400, detail="A guest needs a name.")
    table = guests.tidy(str(body.get("table_name") or ""))[:60] or None
    seat = guests.tidy(str(body.get("seat") or ""))[:20] or None
    side = guests.tidy(str(body.get("side") or ""))[:60] or None

    if action == "add":
        if guests.by_name(name):
            raise HTTPException(status_code=409, detail=f"{name} is already on the list.")
        db.execute(
            "INSERT INTO guests (name, table_name, seat, side, created_at) VALUES (?,?,?,?,?)",
            (name, table, seat, side, time.time()),
        )
        return {"ok": True}

    if action == "update":
        db.execute(
            "UPDATE guests SET name=?, table_name=?, seat=?, side=? WHERE id=?",
            (name, table, seat, side, body.get("id")),
        )
        # Keep already-uploaded photos in step with a corrected table.
        db.execute(
            "UPDATE media SET guest_name = ?, table_id = ? WHERE guest_id = ?",
            (name, table, body.get("id")),
        )
        return {"ok": True}

    raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")


# --------------------------------------------------------------------------
# Live QR code (used by the slideshow corner card)
# --------------------------------------------------------------------------

_qr_cache: dict[tuple[str, int], bytes] = {}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0)


@app.get("/qr.png")
async def qr_png(size: int = 8, url: str = ""):
    """QR for the upload page, in the same dotted style as the printed cards."""
    target = url.strip() or settings.base_url + "/"
    if not target.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Bad URL")
    size = max(2, min(size, 20))
    px = size * 60          # `size` mirrors the old box-size feel

    key = (target, px)
    if key not in _qr_cache:
        from . import qrstyle

        img, _ = qrstyle.build_verified_qr(
            target, px, _hex_to_rgb(settings.ink), qrstyle.load_default_logo(),
            accent=qrstyle.parse_hex(settings.qr_accent),
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        _qr_cache[key] = buf.getvalue()

    return StreamingResponse(
        io.BytesIO(_qr_cache[key]),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not is_admin(request):
        return page(request, "admin_login.html", error=None)
    return page(request, "admin.html")


@app.post("/admin/login")
async def admin_login(request: Request, password: str = Form("")):
    ip = client_ip(request)
    if not rate_limit("login:" + ip, 20, window=600):
        return page(request, "admin_login.html", error="Too many attempts. Wait a few minutes.")
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return page(request, "admin_login.html", error="That password didn't match.")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        ADMIN_COOKIE, _admin_cookie_value(),
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30,
        secure=settings.base_url.startswith("https"),
    )
    return resp


@app.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


@app.get("/api/admin/media")
async def admin_media(request: Request, limit: int = 120, before: int = 0, filter: str = "all"):
    require_admin(request)
    limit = max(1, min(limit, 500))
    where = "1=1"
    params: list = []
    if filter == "pending":
        where = "approved = 0 AND hidden = 0 AND status = 'ready'"
    elif filter == "hidden":
        where = "hidden = 1"
    elif filter == "failed":
        where = "status = 'failed'"
    elif filter == "videos":
        where = "kind = 'video'"
    if before:
        where += " AND seq < ?"
        params.append(before)
    rows = db.query(
        f"SELECT * FROM media WHERE {where} ORDER BY seq DESC LIMIT ?", (*params, limit)
    )
    items = []
    for r in rows:
        item = _public_fields(r)
        item.update({
            "status": r["status"], "approved": bool(r["approved"]),
            "hidden": bool(r["hidden"]), "featured": bool(r["featured"]),
            "bytes": r["bytes"], "original_name": r["original_name"],
            "error": r["error"], "original": f"/m/{r['id']}/original",
            "captions": transcribe.from_json(r["transcript"]),
        })
        items.append(item)
    return {"items": items, "stats": db.counts()}


@app.post("/api/admin/action")
async def admin_action(request: Request):
    require_admin(request)
    body = await request.json()
    ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
    action = body.get("action")
    if not ids or not action:
        raise HTTPException(status_code=400, detail="Need ids and an action")

    updates = {
        "hide": "hidden = 1", "show": "hidden = 0",
        "approve": "approved = 1", "unapprove": "approved = 0",
        "feature": "featured = 1", "unfeature": "featured = 0",
    }
    placeholders = ",".join("?" for _ in ids)

    if action == "delete":
        rows = db.query(f"SELECT * FROM media WHERE id IN ({placeholders})", ids)
        for r in rows:
            (settings.originals_dir / r["stored_name"]).unlink(missing_ok=True)
            if r["thumb_name"]:
                (settings.thumbs_dir / r["thumb_name"]).unlink(missing_ok=True)
            if r["display_name"]:
                (settings.display_dir / r["display_name"]).unlink(missing_ok=True)
        db.execute(f"DELETE FROM media WHERE id IN ({placeholders})", ids)
        return {"ok": True, "deleted": len(rows)}

    if action == "clear-captions":
        db.execute(
            f"UPDATE media SET transcript = '[]' WHERE id IN ({placeholders})", ids)
        return {"ok": True, "cleared": len(ids)}

    if action == "recaption":
        db.execute(
            f"UPDATE media SET transcript = NULL WHERE id IN ({placeholders})", ids)
        for media_id in ids:
            await enqueue(media_id, "transcribe")
        return {"ok": True, "requeued": len(ids)}

    if action == "reprocess":
        db.execute(
            f"UPDATE media SET status='processing', error=NULL WHERE id IN ({placeholders})", ids
        )
        for media_id in ids:
            await enqueue(media_id)
        return {"ok": True, "requeued": len(ids)}

    if action not in updates:
        raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")
    db.execute(f"UPDATE media SET {updates[action]} WHERE id IN ({placeholders})", ids)
    return {"ok": True, "updated": len(ids)}


@app.post("/api/admin/note-action")
async def admin_note_action(request: Request):
    require_admin(request)
    body = await request.json()
    note_id, action = body.get("id"), body.get("action")
    if action == "delete":
        db.execute("DELETE FROM guestbook WHERE id = ?", (note_id,))
    elif action == "hide":
        db.execute("UPDATE guestbook SET hidden = 1 WHERE id = ?", (note_id,))
    elif action == "show":
        db.execute("UPDATE guestbook SET hidden = 0 WHERE id = ?", (note_id,))
    elif action == "approve":
        db.execute("UPDATE guestbook SET approved = 1 WHERE id = ?", (note_id,))
    else:
        raise HTTPException(status_code=400, detail="Unknown action")
    return {"ok": True}


@app.get("/api/admin/notes")
async def admin_notes(request: Request):
    require_admin(request)
    rows = db.query("SELECT * FROM guestbook ORDER BY seq DESC LIMIT 500")
    return {"items": [dict(r) for r in rows]}


@app.get("/api/admin/export.zip")
async def admin_export(request: Request, what: str = "originals"):
    """Stream every original out as one zip. No temp file, no memory blow-up."""
    require_admin(request)
    rows = db.query("SELECT * FROM media WHERE status='ready' ORDER BY seq ASC")

    def generate():
        # ZipFile patches local headers by seeking backwards when it can, which
        # is impossible while streaming. Handing it a tell()-only stream makes
        # it emit data descriptors instead, producing a valid streaming zip.
        class Sink:
            def __init__(self):
                self.buf = bytearray()
                self.pos = 0

            def write(self, data):
                self.buf.extend(data)
                self.pos += len(data)
                return len(data)

            def tell(self):
                return self.pos

            def flush(self):
                pass

            def drain(self) -> bytes:
                out = bytes(self.buf)
                self.buf.clear()
                return out

        sink = Sink()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            manifest = ["filename,guest,table,kind,uploaded_at,original_name"]
            for r in rows:
                src = (settings.originals_dir / r["stored_name"]) if what == "originals" \
                    else (settings.display_dir / (r["display_name"] or ""))
                if not src.exists():
                    continue
                guest = (r["guest_name"] or "guest").replace(",", " ")
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(r["uploaded_at"]))
                safe_guest = "".join(
                    ch for ch in guest if ch.isalnum() or ch in " -_"
                ).strip().replace(" ", "-") or "guest"
                arc = f"{stamp}_{safe_guest}_{r['id'][:6]}{src.suffix}"
                manifest.append(
                    f"{arc},{guest},{r['table_id'] or ''},{r['kind']},{stamp},"
                    f"{(r['original_name'] or '').replace(',', ' ')}"
                )
                with zf.open(arc, "w") as entry, src.open("rb") as fh:
                    while True:
                        chunk = fh.read(CHUNK)
                        if not chunk:
                            break
                        entry.write(chunk)
                        if len(sink.buf) >= CHUNK:
                            yield sink.drain()
                yield sink.drain()
            zf.writestr("manifest.csv", "\n".join(manifest))
        yield sink.drain()

    stamp = time.strftime("%Y%m%d-%H%M")
    name = f"wedding-{what}-{stamp}.zip"
    return StreamingResponse(
        generate(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --------------------------------------------------------------------------
# Sync: pushing a venue instance up to the permanent cloud instance
# --------------------------------------------------------------------------

def require_sync_auth(request: Request) -> None:
    """Admin cookie, or a bearer token equal to the admin password."""
    if is_admin(request):
        return
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if ADMIN_PASSWORD and hmac.compare_digest(token, ADMIN_PASSWORD):
            return
    raise HTTPException(status_code=401, detail="Sync token required")


@app.post("/api/admin/have")
async def admin_have(request: Request):
    """Which of these content hashes does this instance already hold?"""
    require_sync_auth(request)
    body = await request.json()
    hashes = [str(h) for h in (body.get("sha256") or [])][:1000]
    if not hashes:
        return {"have": []}
    placeholders = ",".join("?" for _ in hashes)
    rows = db.query(
        f"SELECT sha256 FROM media WHERE sha256 IN ({placeholders})", hashes
    )
    return {"have": [r["sha256"] for r in rows]}


@app.post("/api/admin/ingest")
async def admin_ingest(
    request: Request,
    file: UploadFile,
    sha256: str = Form(""),
    guest_name: str = Form(""),
    message: str = Form(""),
    table_id: str = Form(""),
    original_name: str = Form(""),
    uploaded_at: float = Form(0.0),
    source: str = Form("import"),
):
    """Accept an item from another instance, preserving its original metadata.

    Idempotent: re-running a sync that half-finished is safe.
    """
    require_sync_auth(request)

    ext = media.safe_extension(original_name or file.filename, file.content_type)
    kind = media.classify(ext, file.content_type)
    if kind is None:
        raise HTTPException(status_code=415, detail="Unsupported file type")
    if not ext:
        ext = ".jpg" if kind == "image" else ".mp4"

    media_id = uuid.uuid4().hex
    dest = settings.originals_dir / f"{media_id}{ext}"
    hasher = hashlib.sha256()
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                hasher.update(chunk)
                out.write(chunk)
    finally:
        await file.close()

    digest = hasher.hexdigest()
    if sha256 and not hmac.compare_digest(sha256, digest):
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Checksum mismatch — transfer corrupted")

    existing = db.query_one("SELECT id FROM media WHERE sha256 = ?", (digest,))
    if existing:
        dest.unlink(missing_ok=True)
        return {"ok": True, "id": existing["id"], "duplicate": True}

    db.execute(
        """
        INSERT INTO media (id, stored_name, original_name, kind, mime, bytes,
                           guest_name, message, table_id, uploaded_at, status,
                           approved, sha256, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,'processing',1,?,?)
        """,
        (
            media_id, dest.name, media.clean_display_name(original_name or file.filename),
            kind, (file.content_type or "")[:100], written,
            guest_name.strip()[:80] or None, message.strip()[:500] or None,
            table_id.strip()[:40] or None, uploaded_at or time.time(),
            digest, (source or "import")[:16],
        ),
    )
    await enqueue(media_id)
    return {"ok": True, "id": media_id, "duplicate": False}


@app.post("/api/admin/ingest-note")
async def admin_ingest_note(
    request: Request,
    note_id: str = Form(...),
    guest_name: str = Form(""),
    message: str = Form(...),
    table_id: str = Form(""),
    created_at: float = Form(0.0),
):
    """Carry a guestbook note across during a sync. Idempotent on note_id."""
    require_sync_auth(request)
    existing = db.query_one("SELECT id FROM guestbook WHERE id = ?", (note_id,))
    if existing:
        return {"ok": True, "duplicate": True}
    db.execute(
        """
        INSERT INTO guestbook (id, guest_name, message, table_id, created_at, approved)
        VALUES (?,?,?,?,?,1)
        """,
        (note_id, guest_name.strip()[:80] or None, message.strip()[:1000],
         table_id.strip()[:40] or None, created_at or time.time()),
    )
    return {"ok": True, "duplicate": False}


@app.get("/api/admin/note-inventory")
async def admin_note_inventory(request: Request):
    require_sync_auth(request)
    rows = db.query("SELECT id, guest_name, message, table_id, created_at FROM guestbook")
    return {"items": [dict(r) for r in rows]}


@app.get("/api/admin/inventory")
async def admin_inventory(request: Request):
    """Everything this instance holds, for a sync client to diff against."""
    require_sync_auth(request)
    rows = db.query(
        "SELECT id, sha256, stored_name, original_name, guest_name, message, "
        "table_id, uploaded_at, kind, bytes, source FROM media "
        "WHERE sha256 IS NOT NULL ORDER BY seq ASC"
    )
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(
        f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<body style='font-family:Georgia,serif;background:{settings.paper};color:{settings.ink};"
        f"display:grid;place-items:center;height:100vh;margin:0;text-align:center'>"
        f"<div><h1 style='font-weight:400'>{exc.status_code}</h1><p>{exc.detail}</p>"
        f"<p><a style='color:{settings.accent}' href='/'>Back to the album</a></p></div>",
        status_code=exc.status_code,
    )
