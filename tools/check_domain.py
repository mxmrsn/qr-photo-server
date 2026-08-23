#!/usr/bin/env python3
"""Check whether candidate domains are free, and how well they'd print.

    tools/check_domain.py maxandrachel.com smoose.wedding
    tools/check_domain.py --suggest

Availability comes from RDAP, the registries' own lookup service — the same
data WHOIS returns, without the rate limits and scraping. A domain that RDAP
says is unregistered is genuinely free to buy.

Nothing here buys anything. Registration needs a card, which is yours to enter
at a registrar of your choosing.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")

# Rough retail pricing, first year / renewal, in USD. Registrars vary; these
# are here to stop a $1 first year from hiding a $40 renewal.
TLD_PRICES = {
    "com": (11, 11), "net": (13, 13), "org": (11, 11),
    "photos": (25, 25), "wedding": (25, 25), "party": (25, 25),
    "xyz": (2, 13), "life": (3, 32), "love": (30, 30),
    "us": (9, 9), "co": (28, 28), "me": (20, 20), "link": (10, 10),
    "pics": (18, 18), "gallery": (20, 20), "family": (25, 25),
}


def rdap(domain: str, attempts: int = 3) -> str:
    """'free', 'taken', or 'unknown'. Retries, because a rate-limited lookup
    reported as 'unknown' is worse than a slow one."""
    for attempt in range(attempts):
        verdict = _rdap_once(domain)
        if verdict != "unknown":
            return verdict
        time.sleep(1.5 * (attempt + 1))
    return "unknown"


def _rdap_once(domain: str) -> str:
    try:
        req = urllib.request.Request(
            f"https://rdap.org/domain/{domain}",
            headers={"Accept": "application/rdap+json", "User-Agent": "wedding-photo-server"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            json.loads(r.read())
            return "taken"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "free"
        return "unknown"
    except Exception:
        return "unknown"


def resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, None)
        return True
    except Exception:
        return False


def printability(domain: str) -> tuple[int, list[str]]:
    """How well does this survive being read off a card and typed by hand?"""
    notes: list[str] = []
    score = 100
    name = domain.split(".")[0]

    if len(domain) > 22:
        score -= 20; notes.append("long to type")
    if "-" in name:
        score -= 25; notes.append("hyphen — people forget it")
    if re.search(r"\d", name):
        score -= 15; notes.append("digits are ambiguous when spoken")
    if re.search(r"(.)\1\1", name):
        score -= 10; notes.append("triple letter")
    for pair in ("rs", "sh", "th", "ss"):
        if name.count(pair) > 1:
            score -= 5; break
    if len(name) <= 12 and "-" not in name and not re.search(r"\d", name):
        notes.append("reads cleanly aloud")
    return max(0, score), notes


def check(domain: str) -> dict:
    domain = domain.strip().lower().rstrip("/")
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    tld = domain.split(".")[-1]
    status = rdap(domain)
    first, renew = TLD_PRICES.get(tld, (None, None))
    score, notes = printability(domain)
    return {"domain": domain, "status": status, "resolves": resolves(domain),
            "first": first, "renew": renew, "score": score, "notes": notes}


SUGGESTIONS = [
    "maxandrachel.com", "rachelandmax.com", "maxandrachel.wedding",
    "maxandrachel.photos", "maxrachel.com", "smoose.wedding",
    "smoosewedding.com", "smoose.photos", "maxandrachel.xyz",
    "maxandrachel.us", "therachelandmax.com", "maxandrachelphotos.com",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Check domain availability and printability.")
    ap.add_argument("domains", nargs="*", help="candidates, e.g. maxandrachel.com")
    ap.add_argument("--suggest", action="store_true", help="check a built-in list")
    args = ap.parse_args()

    names = args.domains or (SUGGESTIONS if args.suggest else [])
    if not names:
        ap.print_help()
        return 2

    print(f"\n  Checking {len(names)} domain(s) — RDAP, the registries' own records\n")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, names))

    results.sort(key=lambda r: (r["status"] != "free", -r["score"]))
    print(f"  {'domain':<28}{'status':<12}{'~1st yr':>9}{'~renew':>9}  notes")
    print("  " + "─" * 78)
    for r in results:
        if r["status"] == "free":
            tag = f"{GREEN}available{RESET}   "
        elif r["status"] == "taken":
            tag = f"{RED}taken{RESET}       "
        else:
            tag = f"{YELLOW}unknown{RESET}     "
        first = f"${r['first']}" if r["first"] else "?"
        renew = f"${r['renew']}" if r["renew"] else "?"
        flag = ""
        if r["status"] == "free" and r["resolves"]:
            flag = " (resolves though — check it isn't parked)"
        notes = ", ".join(r["notes"]) + flag
        print(f"  {r['domain']:<28}{tag}{first:>9}{renew:>9}  {DIM}{notes}{RESET}")

    print(f"\n  {DIM}Prices are rough retail. Watch first-year teasers with steep renewals —")
    print(f"  you only need one year, but auto-renew will bill you at the higher rate.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
