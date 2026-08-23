"""The seating chart: who is sitting where.

With one shared QR code there's no table information in the URL any more, so
it has to come from somewhere else. A guest picks their name, and the table
comes with it — which is less typing for them than the old free-text field,
not more.

Nothing here is mandatory. Anyone not on the list (a late plus-one, a
babysitter, someone's dog) can still type a name and upload exactly as before.
"""
from __future__ import annotations

import csv
import io
import re
import time
from typing import Iterable

from . import db

# Header names people actually use in a seating spreadsheet.
NAME_KEYS = {"name", "guest", "guest name", "full name", "fullname", "attendee", "person"}
TABLE_KEYS = {"table", "table name", "table_name", "table number", "table no",
              "tablenumber", "seating", "seat table"}
SEAT_KEYS = {"seat", "seat number", "chair", "place"}
SIDE_KEYS = {"side", "party", "group", "family", "affiliation"}
NOTE_KEYS = {"notes", "note", "dietary", "comment", "comments"}


def tidy(value: str) -> str:
    """Collapse the whitespace a spreadsheet inevitably contains.

    'Marjorie  Hale' and 'Marjorie Hale' are the same person, and one of them
    is a typo nobody will ever notice in a cell.
    """
    return re.sub(r"\s+", " ", (value or "").strip())


def _pick(header: list[str], keys: set[str]) -> int | None:
    for i, h in enumerate(header):
        if (h or "").strip().lower() in keys:
            return i
    return None


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """Turn a seating spreadsheet into rows. Returns (guests, warnings).

    Deliberately forgiving about headers, because the file is going to come out
    of whatever spreadsheet the couple already keeps.
    """
    warnings: list[str] = []
    text = text.lstrip("﻿")                     # Excel loves a BOM
    if not text.strip():
        return [], ["The file was empty."]

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return [], ["No rows found."]

    header = [(c or "").strip() for c in rows[0]]
    i_name = _pick(header, NAME_KEYS)
    i_table = _pick(header, TABLE_KEYS)

    if i_name is None:
        # No recognisable header: assume the first column is the name and the
        # second, if present, is the table.
        warnings.append(
            "No 'name' column header found — assuming column 1 is the name"
            + (" and column 2 is the table." if len(header) > 1 else ".")
        )
        i_name, i_table = 0, (1 if len(header) > 1 else None)
        body = rows
        i_seat = i_side = i_note = None
    else:
        body = rows[1:]
        i_seat = _pick(header, SEAT_KEYS)
        i_side = _pick(header, SIDE_KEYS)
        i_note = _pick(header, NOTE_KEYS)
        if i_table is None:
            warnings.append("No 'table' column found — guests will have no table.")

    def cell(row: list[str], idx: int | None) -> str:
        if idx is None or idx >= len(row):
            return ""
        return tidy(row[idx])

    out: list[dict] = []
    seen: set[str] = set()
    for row in body:
        name = cell(row, i_name)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            warnings.append(f"Duplicate in the file, kept the first: {name}")
            continue
        seen.add(key)
        out.append({
            "name": name[:120],
            "table_name": cell(row, i_table)[:60] or None,
            "seat": cell(row, i_seat)[:20] or None,
            "side": cell(row, i_side)[:60] or None,
            "notes": cell(row, i_note)[:200] or None,
        })
    if not out:
        warnings.append("No usable rows — is the name column empty?")
    return out, warnings


def replace_all(guests: Iterable[dict]) -> int:
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM guests")
        conn.executemany(
            "INSERT INTO guests (name, table_name, seat, side, notes, created_at) "
            "VALUES (:name, :table_name, :seat, :side, :notes, :created_at)",
            [{**g, "created_at": time.time()} for g in guests],
        )
    return db.query_one("SELECT COUNT(*) n FROM guests")["n"]


def merge(guests: Iterable[dict]) -> tuple[int, int]:
    """Add or update by name. Returns (added, updated)."""
    added = updated = 0
    conn = db.connect()
    with conn:
        for g in guests:
            existing = conn.execute(
                "SELECT id FROM guests WHERE name = ? COLLATE NOCASE", (g["name"],)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE guests SET table_name=?, seat=?, side=?, notes=? WHERE id=?",
                    (g["table_name"], g["seat"], g["side"], g["notes"], existing["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO guests (name, table_name, seat, side, notes, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (g["name"], g["table_name"], g["seat"], g["side"], g["notes"], time.time()),
                )
                added += 1
    return added, updated


def search(query: str, limit: int = 12) -> list[dict]:
    """Typeahead for the upload page. Prefix matches rank above substrings."""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    starts = f"{q}%"
    word = f"% {q}%"
    rows = db.query(
        """
        SELECT id, name, table_name, seat FROM guests
         WHERE name LIKE ? COLLATE NOCASE
         ORDER BY CASE
                    WHEN name LIKE ? COLLATE NOCASE THEN 0
                    WHEN name LIKE ? COLLATE NOCASE THEN 1
                    ELSE 2
                  END,
                  name COLLATE NOCASE
         LIMIT ?
        """,
        (like, starts, word, max(1, min(limit, 40))),
    )
    return [dict(r) for r in rows]


def by_id(guest_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM guests WHERE id = ?", (guest_id,))
    return dict(row) if row else None


def by_name(name: str) -> dict | None:
    row = db.query_one("SELECT * FROM guests WHERE name = ? COLLATE NOCASE", (name,))
    return dict(row) if row else None


def all_guests() -> list[dict]:
    rows = db.query(
        """
        SELECT g.*,
               (SELECT COUNT(*) FROM media m WHERE m.guest_id = g.id) AS uploads
          FROM guests g
         ORDER BY
           CASE WHEN g.table_name GLOB '[0-9]*' THEN CAST(g.table_name AS INTEGER)
                ELSE 999999 END,
           g.table_name COLLATE NOCASE,
           g.name COLLATE NOCASE
        """
    )
    return [dict(r) for r in rows]


def to_csv() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "table", "seat", "side", "notes", "uploads"])
    for g in all_guests():
        writer.writerow([g["name"], g["table_name"] or "", g["seat"] or "",
                         g["side"] or "", g["notes"] or "", g["uploads"]])
    return buf.getvalue()
