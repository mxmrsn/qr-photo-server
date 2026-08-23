# Wedding photo & video collection

Guests scan a QR code on their table, land on a page with your names on it, and
their photos and videos go straight into your album — no app, no account, no
per-guest fee. Optionally, a projector shows them as they arrive.

Built to run in two phases:

1. **On the day** — the server runs on a laptop at the venue. Guests upload over
   local Wi-Fi. No cellular service required, nothing depends on the internet.
2. **Afterwards** — the same app runs on a small cloud VM. You sync the venue
   collection up to it, then send everyone a link so they can add whatever is
   still sitting in their camera roll for the next few weeks.

The two instances are the same code, and `tools/sync.py` moves everything from
one to the other without duplicates.

---

## Quick start

```bash
./run.sh
```

First run creates the virtual environment and installs dependencies. Then open:

| | |
| --- | --- |
| **Guest upload** | `http://localhost:8000/` |
| **The album** | `http://localhost:8000/gallery` |
| **Projector** | `http://localhost:8000/slideshow` |
| **Management** | `http://localhost:8000/admin` |

The admin password is printed in the console on startup and saved to
`data/admin_password.txt`.

### Configure it

```bash
cp .env.example .env
```

Edit your names, the date, and — importantly — `BASE_URL`, which is what the QR
codes point at. Every setting is documented in the file.

### Print the table cards

```bash
tools/make_qr.py --copies 18 --layout sheet
```

One QR code for the whole wedding, printed as many times as you have tables.
`--layout sheet` puts four cards on a Letter page with cut guides, so eighteen
cards is five sheets rather than eighteen.

The card carries your names, the code, and the URL in plain text underneath for
any camera that won't cooperate. Output lands in `qr_out/`, including a
print-ready PDF.

**The code is styled**, not a stock black square: round dots instead of squares,
rounded finder patterns, and the logo from `assets/` shaded *across the whole
code* rather than punched into a white hole in the middle.

That last part is the interesting bit. Nothing is covered up and no data is
destroyed — every module keeps its correct light or dark value. The logo is
carried by *which shade and size* each dot gets:

| | inside the mark | outside it |
| --- | --- | --- |
| **dark module** | near-black, fat dots that join into strokes | small grey dots |
| **light module** | grey dot, filling gaps in the strokes | paper |

A scanner samples the middle of each module and only cares which side of the
light/dark threshold it lands on, so the mark costs nothing from the error
correction budget and can span the entire code at full resolution.

```bash
tools/make_qr.py --logo path/to/your-logo.png   # any logo; converted to black and white
tools/make_qr.py --no-logo                      # plain code
tools/make_qr.py --style squares                # classic square modules
tools/make_qr.py --min-modules 57               # finer grid, crisper logo, less margin
tools/make_qr.py --wifi-ssid Wedding --wifi-password loveislove   # + a code that joins your wifi
```

Any logo works. The background tone is read from the border pixels and
everything that differs from it becomes ink, so a white mark on a coloured
square and a dark mark on a light one both come out as clean black artwork.

> **Every generated code is decoded before it's written out.** Shading a logo
> across a QR eats into its contrast margin, and going too far fails silently —
> the code looks perfect and simply never scans. So the tool reads its own
> output back three times: crisp, shrunk and blurred, and through a punishing
> simulation of a photo taken across a dim room with an unsteady hand. If a
> setting doesn't survive all three it coarsens the grid and tries again, and
> tells you what it settled on:
>
> ```
> card-card.png   ->  https://photos.example.com/
>       logo shaded across the code, 49-module grid  verified
> ```
>
> Those thresholds are calibrated against a separate harness that renders whole
> cards, warps them in perspective, simulates ink spread on paper and decodes
> the result — not guessed. The check is deterministic, so the same inputs
> always produce the same card. This needs `zxing-cpp` (in `requirements.txt`);
> without it the tool says `unverified` rather than pretending.

If you'd rather know which table a photo came from, you can still print
per-table codes with `--tables 1-18`, and the table name shows up on the upload
page, in the gallery, and on the projector.

### Check everything before the day

```bash
tools/doctor.py --check-url
```

Verifies dependencies, ffmpeg, disk space, database integrity, and whether your
`BASE_URL` is something a guest's phone can actually reach.

---

## What guests see

The upload page is deliberately one screen: their name, one big button for
photos and videos, one for the camera, and a queue that starts uploading the
moment files are chosen — nobody has to remember to press "send". There's also a
fold-out box for leaving a written note instead, which shows up in the gallery
and on the projector as a caption.

It handles what phones actually produce: **HEIC** photos from iPhones, **HEVC
.mov** video, 48-megapixel originals, and sideways footage. Originals are kept
untouched; the browser is served converted copies.

## The projector

`/slideshow` is meant to be opened on a laptop plugged into a projector and left
alone. It shuffles the collection, crossfades with a slow Ken Burns drift, fills
the letterbox bars with a blurred copy of the photo, plays videos muted with a
length cap, and holds a screen wake lock so the display doesn't sleep.

When a photo arrives it jumps the queue with a *"Just added by Priya · Table 3"*
banner, which is the part that makes people actually pull their phones out. A QR
card sits in the corner so anyone looking at the screen knows how to contribute.

Keys: `space` pause · `←` `→` step · `c` captions · `q` QR card · `f` fullscreen

## Management

`/admin` gives you the whole collection with filters, bulk approve/hide/delete,
retry for anything that failed to process, guest-note moderation, and a
**Download all** button that streams every original out as one zip with a
`manifest.csv` mapping files to guests and tables.

If you'd rather vet things before they hit the projector, set
`MODERATION=slideshow` — the gallery stays live, but the big screen waits for
your approval. `MODERATION=all` holds everything.

---

## After the wedding

Stand up an instance somewhere permanent (see `docs/DEPLOY.md`), then push the
venue collection to it:

```bash
tools/sync.py --to https://photos.yourdomain.com --token <its admin password>
```

It diffs by content hash, so it is idempotent and resumable — if it dies at 60%,
run it again. Guest names, tables, messages, timestamps and the guestbook all
travel with the files.

Then set `POST_EVENT=true` on the cloud instance. The upload page copy changes
from "today" to an invitation to go digging through their camera roll, and you
can send the same link to everyone who was there.

---

## How it works

```
app/
  main.py       FastAPI: routes, uploads, auth, moderation, sync endpoints
  config.py     every setting, from .env or the environment
  db.py         SQLite in WAL mode; media + guestbook tables
  media.py      HEIC/video normalisation into thumb + display JPEGs
  templates/    server-rendered pages
  static/       vanilla CSS and JS — no build step, nothing to compile
  qrstyle.py    the dotted QR renderer + its scan verifier, shared with tools/
tools/
  make_qr.py    printable table cards
  doctor.py     pre-flight checks
  sync.py       push one instance's collection to another
data/           originals, derivatives, the database  (never committed)
docs/
  NETWORK.md    getting Wi-Fi right at the venue — read this one
  DEPLOY.md     putting it on a server with HTTPS
```

Uploads stream to disk in 1 MB chunks with the size cap enforced mid-stream, get
hashed on the way past for dedupe, then land in a bounded worker queue for
thumbnailing so forty simultaneous uploads can't spawn forty ffmpeg processes.
Files are served by database id, never by a client-supplied path.

**No build step and no JavaScript framework.** Fonts are system fonts. The whole
thing works with the venue's internet unplugged, which is the point.

## Requirements

- Python 3.10+
- `ffmpeg` (`brew install ffmpeg`) — required for video
- ~1 GB of disk per 100 photos, considerably more with video

## A word on the network

**Read `docs/NETWORK.md` before buying any hardware.** Most of the ways this
project can fail on the day are network failures, not software ones — and two of
the most common instincts (using the laptop as the Wi-Fi access point, buying a
Wi-Fi "booster") both make things worse.
