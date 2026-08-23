#!/usr/bin/env python3
"""Push everything this instance holds up to another instance.

The venue laptop collects photos with no internet. Afterwards, point it at
the permanent cloud instance and run:

    tools/sync.py --to https://photos.ourwedding.com --token <admin password>

Idempotent and resumable: content hashes decide what's already there, so a
run that dies halfway can simply be run again. Nothing is deleted locally.

    --dry-run     say what would be sent, send nothing
    --notes-only  just the guestbook messages
    --limit N     stop after N items (useful for a first trial run)
"""
from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import sqlite3
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

CHUNK = 1024 * 256


class MultipartBody:
    """A read()-able multipart body that streams the file from disk.

    Building the whole thing in memory would mean holding a 400 MB video in
    RAM; http.client will happily pull this in blocks instead.
    """

    def __init__(self, fields: dict[str, str], file_path: Path, file_field: str = "file"):
        self.boundary = "----sync" + uuid.uuid4().hex
        head = b""
        for key, value in fields.items():
            head += (
                f"--{self.boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            ).encode("utf-8")
        ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        head += (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{self.boundary}--\r\n".encode("utf-8")

        self._head, self._tail = head, tail
        self._path = file_path
        self._size = file_path.stat().st_size
        self._fh = None
        self._stage = 0            # 0 head, 1 file, 2 tail, 3 done
        self._offset = 0
        self.length = len(head) + self._size + len(tail)

    def read(self, amount: int = CHUNK) -> bytes:
        if self._stage == 0:
            chunk = self._head[self._offset:self._offset + amount]
            self._offset += len(chunk)
            if self._offset >= len(self._head):
                self._stage, self._offset = 1, 0
                self._fh = self._path.open("rb")
            return chunk or self.read(amount)
        if self._stage == 1:
            chunk = self._fh.read(amount)
            if chunk:
                return chunk
            self._fh.close()
            self._stage, self._offset = 2, 0
        if self._stage == 2:
            chunk = self._tail[self._offset:self._offset + amount]
            self._offset += len(chunk)
            if self._offset >= len(self._tail):
                self._stage = 3
            return chunk
        return b""

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()


class Remote:
    def __init__(self, base: str, token: str):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme not in ("http", "https"):
            raise SystemExit(f"--to must be an http(s) URL (got {base!r})")
        self.scheme = parsed.scheme
        self.host = parsed.netloc
        self.prefix = parsed.path.rstrip("/")
        self.token = token

    def _connect(self) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, timeout=1800)

    def post_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        conn = self._connect()
        try:
            conn.request("POST", self.prefix + path, body, {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            })
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"{path} -> HTTP {resp.status}: {data[:200].decode(errors='replace')}")
            return json.loads(data or b"{}")
        finally:
            conn.close()

    def post_file(self, path: str, fields: dict[str, str], file_path: Path) -> dict:
        body = MultipartBody(fields, file_path)
        conn = self._connect()
        try:
            conn.request("POST", self.prefix + path, body, {
                "Content-Type": f"multipart/form-data; boundary={body.boundary}",
                "Content-Length": str(body.length),
                "Authorization": f"Bearer {self.token}",
            })
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {data[:200].decode(errors='replace')}")
            return json.loads(data or b"{}")
        finally:
            body.close()
            conn.close()

    def post_form(self, path: str, fields: dict[str, str]) -> dict:
        body = urllib.parse.urlencode(fields).encode()
        conn = self._connect()
        try:
            conn.request("POST", self.prefix + path, body, {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Bearer {self.token}",
            })
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {data[:200].decode(errors='replace')}")
            return json.loads(data or b"{}")
        finally:
            conn.close()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    ap = argparse.ArgumentParser(description="Push this instance's collection to another.")
    ap.add_argument("--to", required=True, help="base URL of the destination instance")
    ap.add_argument("--token", required=True, help="the destination's admin password")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notes-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    remote = Remote(args.to.rstrip("/"), args.token)

    # Fail fast on a bad URL or password rather than part-way through a note loop.
    try:
        remote.post_json("/api/admin/have", {"sha256": []})
    except Exception as exc:
        print(f"\nCannot talk to {args.to}: {exc}")
        print("Check the URL, and that --token matches the destination's admin password.")
        return 1
    print(f"Connected to {args.to}")

    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row

    # ------------------------------------------------------------- notes
    notes = con.execute(
        "SELECT id, guest_name, message, table_id, created_at FROM guestbook WHERE hidden = 0"
    ).fetchall()
    sent_notes = 0
    if notes:
        print(f"\nGuestbook: {len(notes)} note(s)")
        if args.dry_run:
            print(f"  would send up to {len(notes)} (duplicates are skipped by the destination)")
        for note in notes:
            if args.dry_run:
                continue
            try:
                result = remote.post_form("/api/admin/ingest-note", {
                    "note_id": note["id"],
                    "guest_name": note["guest_name"] or "",
                    "message": note["message"],
                    "table_id": note["table_id"] or "",
                    "created_at": str(note["created_at"]),
                })
                if not result.get("duplicate"):
                    sent_notes += 1
            except Exception as exc:
                print(f"  ! note {note['id'][:8]}: {exc}")
        if not args.dry_run:
            print(f"  {sent_notes} new, {len(notes) - sent_notes} already there")

    if args.notes_only:
        return 0

    # ------------------------------------------------------------- media
    rows = con.execute(
        "SELECT * FROM media WHERE status = 'ready' AND sha256 IS NOT NULL ORDER BY seq"
    ).fetchall()
    if not rows:
        print("\nNothing to sync.")
        return 0

    print(f"\nLocal collection: {len(rows)} item(s), "
          f"{human(sum(r['bytes'] or 0 for r in rows))}")

    # Ask the destination what it already has, 500 hashes at a time.
    have: set[str] = set()
    hashes = [r["sha256"] for r in rows]
    for i in range(0, len(hashes), 500):
        try:
            result = remote.post_json("/api/admin/have", {"sha256": hashes[i:i + 500]})
            have.update(result.get("have") or [])
        except Exception as exc:
            print(f"\nCould not reach the destination: {exc}")
            return 1

    todo = [r for r in rows if r["sha256"] not in have]
    total_bytes = sum(r["bytes"] or 0 for r in todo)
    print(f"Destination already has {len(have)}; {len(todo)} to send "
          f"({human(total_bytes)})")

    if args.limit:
        todo = todo[:args.limit]
        print(f"--limit {args.limit}: sending only the first {len(todo)}")

    if args.dry_run:
        for r in todo[:20]:
            print(f"  would send {r['original_name']}  {human(r['bytes'])}  "
                  f"{r['guest_name'] or 'anonymous'}")
        if len(todo) > 20:
            print(f"  … and {len(todo) - 20} more")
        return 0

    if not todo:
        print("\nAlready in sync.")
        return 0

    sent = failed = skipped = 0
    done_bytes = 0
    started = time.time()

    for index, row in enumerate(todo, 1):
        src = settings.originals_dir / row["stored_name"]
        if not src.exists():
            print(f"  [{index}/{len(todo)}] missing on disk: {row['stored_name']}")
            skipped += 1
            continue
        try:
            result = remote.post_file("/api/admin/ingest", {
                "sha256": row["sha256"] or "",
                "guest_name": row["guest_name"] or "",
                "message": row["message"] or "",
                "table_id": row["table_id"] or "",
                "original_name": row["original_name"] or src.name,
                "uploaded_at": str(row["uploaded_at"] or 0),
                "source": row["source"] or "venue",
            }, src)
            sent += 1
            done_bytes += row["bytes"] or 0
            elapsed = max(0.001, time.time() - started)
            rate = done_bytes / elapsed
            remaining = (total_bytes - done_bytes) / rate if rate > 0 else 0
            tag = "dup" if result.get("duplicate") else "ok "
            print(f"  [{index}/{len(todo)}] {tag} {(row['original_name'] or '')[:38]:<38} "
                  f"{human(row['bytes']):>9}  {human(rate)}/s  ~{remaining / 60:.0f} min left")
        except Exception as exc:
            failed += 1
            print(f"  [{index}/{len(todo)}] FAILED {row['original_name']}: {exc}")

    print(f"\nSent {sent}, failed {failed}, skipped {skipped} in "
          f"{(time.time() - started) / 60:.1f} min")
    if failed:
        print("Re-run this command to retry the failures — it picks up where it left off.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
