# Getting the network right at the venue

The software is the easy part. Almost every failure of a self-hosted event
photo service is a networking failure. This is the decision tree.

---

## Step 0: the one test that decides everything

**Walk the venue and check cellular signal** — both buildings, the field, and
wherever the dance floor will be. Try more than one carrier if you can borrow a
second phone. Take screenshots of the bar count with a speed test.

- **Usable cell service (even 2–3 bars LTE)** → host in the cloud. Go to
  [Plan A](#plan-a-cloud-hosted-recommended).
- **Genuine dead zone** → host locally. Go to
  [Plan B](#plan-b-local-network).

Do this early. Everything below — what you buy, where the laptop sits, whether
you need to run a cable — follows from the answer.

---

## Plan A: cloud hosted (recommended)

Guests use their own cellular data. You host the server on a small VM.

**Why this wins:** no access points, no power runs, no coverage map, no captive
portal problems, no laptop babysitting. If the laptop dies, the service doesn't.
And you can test the whole thing weeks in advance from your couch.

**Cost:** $0–6 for the month.

| Option | Cost | Notes |
| --- | --- | --- |
| Fly.io | free tier, then ~$3/mo | needs a persistent volume for `data/` |
| Hetzner CX22 | ~€4/mo | most storage for the money |
| DigitalOcean / Linode | ~$6/mo | simplest dashboards |

**Sizing:** 2 GB RAM and 40+ GB disk is plenty. 150 guests × ~15 items × ~5 MB
lands around 10–12 GB, and video is the variable — if you expect a lot of it,
take 80 GB or attach object storage.

**You will also want:**
- A short domain (`ourphotos.xyz` is a few dollars) — it makes the QR fallback
  URL printable and memorable.
- HTTPS. Caddy does this automatically in two lines; see `docs/DEPLOY.md`.
  iOS will not let a page use the camera over plain HTTP, so this is required,
  not optional.

---

## Plan B: local network

The server lives at the venue and guests join a local Wi-Fi network.

### Do not use the MacBook as the access point

macOS Internet Sharing over Wi-Fi is a single-radio, low-client-count AP. It is
fine for three laptops in a hotel room and quite bad for a hundred phones. More
importantly it cannot solve the coverage problem below, and it puts the one
machine holding all your photos out where the antennas need to be.

### Do not buy a "Wi-Fi booster" or amplifier

The link is symmetric: the phone has to be heard *back* by the AP, and a phone's
transmitter is the weak half. Amplifying only your side gives guests a strong
signal bar and failed uploads. **More access points beats more power**, nearly
always. A directional antenna on a real AP is legitimate; a generic "range
extender" is usually worse than nothing because it halves throughput and adds a
hop.

### The layout

```
        BUILDING 1                BUILDING 2              OPEN FIELD
       ┌──────────┐              ┌──────────┐            ╭──────────╮
       │  AP  #1  │              │  AP  #2  │            │  AP  #3  │
       │          │              │          │            │ (outdoor,│
       │ MacBook  │              └────┬─────┘            │   PoE)   │
       │ + router │                   │                  ╰────┬─────╯
       └────┬─────┘                   │                       │
            └───── ethernet ──────────┴───── ethernet ────────┘
                        (or mesh wireless backhaul)
```

- **The MacBook stays indoors**, wired to the router by Ethernet, on AC power.
- **The APs travel, not the server.** Antennas belong where the people are; the
  machine holding every photo of your wedding belongs somewhere cool, dry,
  powered and out of the way.

### Kit list

| Item | Why | Rough cost |
| --- | --- | --- |
| 3-pack mesh (TP-Link Deco X20 / UniFi Express) | one per building + field | $150–250 |
| Outdoor AP (UniFi AC Mesh, Deco X50-Outdoor) | if the field AP is exposed | $100–180 |
| PoE injector + outdoor Cat6 run | powers the field AP with no outdoor outlet | $30–60 |
| Cellular hotspot or 5G router | WAN uplink — see below | you may already own one |

Buy the mesh kit as a **wired-backhaul** setup if you can run cable between
buildings. Wireless backhaul works but roughly halves throughput per hop.

### Give the network an internet uplink anyway

This is the non-obvious one. **iPhones test for internet access** (they fetch
`captive.apple.com`) and, when it fails, mark the network "No Internet" and
drift back to cellular — sometimes in the middle of an upload. Android does the
same with `connectivitycheck.gstatic.com`.

The clean fix is to give the router *any* uplink — a phone hotspot, a 5G
router, the venue's own DSL. It does not need to be fast. Guest photos still
travel over local Wi-Fi at full LAN speed; the uplink exists purely so phones
believe the network is real and stay put.

(You *can* instead spoof the connectivity checks with local DNS, but on the day
of your wedding is a poor time to be debugging DNS interception.)

### Keep the laptop awake

macOS will sleep and take the server with it. Run it under `caffeinate`:

```bash
caffeinate -dimsu ./run.sh
```

`-d` display, `-i` idle, `-m` disk, `-s` on AC power, `-u` asserts user activity.
Also turn off "Put hard disks to sleep" and set Energy Saver to never sleep.

### Tell guests the network name

QR codes can encode Wi-Fi credentials. Print a second small QR on each table
card that joins the network, next to the one that opens the upload page.
`tools/make_qr.py --wifi-ssid ... --wifi-password ...` generates it.

---

## Plan C: the hedge

If cell service is *marginal*, do both:

- Host in the cloud (Plan A) so anyone with a bar of signal just works.
- Put one AP with a cellular uplink in the main reception room, so guests in the
  dead spot have something to join that reaches the same cloud URL.

Same QR code, same URL, two paths to it. This is the most robust option and it
costs one hotspot more than Plan A.

---

## Day-of checklist

- [ ] Server reachable from a phone **on cellular** (not just your laptop's browser)
- [ ] HTTPS certificate valid — camera access silently fails without it
- [ ] Upload a photo *and a video* from an actual iPhone and an actual Android
- [ ] Check free disk space; 150 guests can produce 20 GB+
- [ ] `caffeinate` running, Energy Saver set to never sleep (Plan B)
- [ ] Laptop on AC power, not battery
- [ ] Someone other than the couple knows the admin password
- [ ] Walk the venue with a phone and confirm signal at every table
- [ ] A printed fallback URL on each card, in case a camera won't scan
