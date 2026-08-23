# Getting the network right at the venue

The software is the easy part. Almost every failure of a self-hosted event
photo service is a networking failure. This is the decision tree.

---

## Your plan

You've ruled out depending on cellular service, and you want guests to be able
to keep uploading after the wedding. That settles the shape of this:

- **On the day** — the server runs on the laptop at the venue. Guests upload
  over local Wi-Fi (the venue's, if it's usable — otherwise your own access
  points). Nothing depends on the internet being up. Go to
  [Step 0](#step-0-scout-the-venue) to work out which.
- **Afterwards** — a small cloud instance becomes the permanent home. You run
  `tools/sync.py` to push the night's collection up to it, then send the link
  round so people can add what's still on their phones.

So read Step 0 and pick between **Plan 2, 3 or 4** for the day itself. Plan 1
(cloud-only) is not your wedding-day answer, but it *is* the after-party one —
you'll want that instance either way, so it's worth standing up early and using
it as a rehearsal.

---

## The one rule that saves you from all of this

**Buy a cheap domain and print the QR codes pointing at it.**

Something like `ourphotos.xyz` for a few dollars. Every QR code, every table
card, every sign points at `https://ourphotos.xyz`. Then the question of *where
the server actually lives* becomes a DNS record you can change the morning of
the wedding — or from your phone, from the venue, after discovering the guest
Wi-Fi has client isolation turned on.

Without a domain, your QR codes hard-code an IP address or a tunnel hostname,
and any change means reprinting sixty table cards. With one, the backend can
move and nobody notices.

Do this first. It is the cheapest insurance in the whole project.

---

## Step 0: scout the venue

Two questions, answered on site, decide everything:

**1. Is there cellular signal?** Walk the field and both buildings. Try two
carriers if you can. Screenshot the bars and run a speed test.

**2. Is there venue Wi-Fi — and what kind?** Get on it and check:

```bash
# From a laptop on the venue wifi:
curl -s https://example.com > /dev/null && echo "internet: yes" || echo "internet: no"
ipconfig getifaddr en0            # your address on their network
```

Then the critical test — **client isolation**. Most guest networks block
device-to-device traffic, which silently kills any plan where phones talk
directly to your laptop:

```bash
# On the laptop:  python3 -m http.server 8000
# On a phone on the same wifi, open http://<laptop-ip>:8000
# Page loads  -> isolation is off, direct LAN is possible
# Times out   -> isolation is on, you need a tunnel or your own AP
```

| Venue Wi-Fi | Uplink | Isolation | Go to |
| --- | --- | --- | --- |
| Yes | working internet | on (or unknown) | [Plan 2 — laptop + tunnel](#plan-2-laptop--tunnel-over-venue-wi-fi) |
| Yes | working internet | off | [Plan 3 — direct LAN](#plan-3-laptop-directly-on-venue-wi-fi) — fastest |
| Yes | slow or none | off | [Plan 3 — direct LAN](#plan-3-laptop-directly-on-venue-wi-fi) |
| No / unusable | — | — | [Plan 4 — bring your own APs](#plan-4-bring-your-own-access-points) |

Cell signal no longer picks your plan, but **still check it** — it tells you
whether guests can reach the after-the-wedding instance from the venue, and
whether a phone hotspot is available as an uplink of last resort.

**Every plan uses the same QR codes**, as long as you followed the rule above.

---

## Plan 1: cloud hosted

Guests use cellular data or the venue Wi-Fi; the server lives on a small VM.

**Why this wins:** no access points, no power runs, no coverage map, no laptop
babysitting, no client isolation to worry about. If a laptop dies, the service
doesn't. And you can test the entire thing weeks in advance from your couch.

**Cost:** $0–6 for the month.

| Option | Cost | Notes |
| --- | --- | --- |
| Fly.io | free tier, then ~$3/mo | needs a persistent volume for `data/` |
| Hetzner CX22 | ~€4/mo | most storage per euro |
| DigitalOcean / Linode | ~$6/mo | simplest dashboards |

**Sizing:** 2 GB RAM, 40+ GB disk. 150 guests × ~15 items × ~5 MB is roughly
10–12 GB; video is the variable. If you expect a lot of it, take 80 GB.

Point your domain's A record at the VM. Caddy gets you HTTPS in two lines —
see `docs/DEPLOY.md`. **HTTPS is not optional**: iOS blocks camera access on
plain HTTP, so the "Take a photo" button silently dies without it.

---

## Plan 2: laptop + tunnel over venue Wi-Fi

*The best self-hosted option, and the one to reach for if the venue has any
working internet at all.*

The laptop joins the venue Wi-Fi like any other device and opens an **outbound**
tunnel to a public HTTPS hostname. Guests reach that hostname from anywhere —
cellular, venue Wi-Fi, doesn't matter.

This quietly solves four problems at once:

- **Client isolation stops mattering.** Traffic goes out to the internet and
  back, never phone-to-laptop directly.
- **You get real HTTPS**, with a valid certificate, for free.
- **DHCP changes stop mattering.** The laptop's local address can change all day.
- **No inbound firewall rules or port forwarding** — the venue's router sees
  only an ordinary outbound connection.

The photos live on your laptop, in your hands, at the end of the night.

### Cloudflare Tunnel (recommended)

```bash
brew install cloudflared
cloudflared tunnel login                       # once, at home
cloudflared tunnel create wedding
cloudflared tunnel route dns wedding photos.ourphotos.xyz
cloudflared tunnel run --url http://localhost:8000 wedding
```

`photos.ourphotos.xyz` now serves your laptop over HTTPS. Set
`BASE_URL=https://photos.ourphotos.xyz` in `.env` before generating QR codes.

### Tailscale Funnel (simpler, no domain needed)

```bash
brew install tailscale
tailscale up
tailscale funnel 8000
```

Gives you a `https://your-machine.tailnet-name.ts.net` URL. Free, one command,
but the hostname is ugly on a printed card — pair it with a domain redirect.

### The catch

Every photo now uploads *out* to the internet and the slideshow pulls them
back *in*. On a slow venue DSL line this is the bottleneck. If the venue
uplink is under ~10 Mbps up and you expect heavy video, prefer Plan 3 or 4 so
uploads stay on the local network.

Also: **the tunnel dies if the venue Wi-Fi drops.** `cloudflared` reconnects on
its own, but check on it once during the reception.

---

## Plan 3: laptop directly on venue Wi-Fi

Cheapest and fastest — uploads never leave the building — but the most fragile.
**Only viable if your client-isolation test above passed.**

### Pin the laptop's address

DHCP will otherwise hand your laptop a new address mid-reception and every QR
code breaks. Pick one:

- Ask the venue for a **DHCP reservation** for your laptop's MAC address.
- Set a **static IP** outside their DHCP pool (System Settings → Network →
  Details → TCP/IP → Manually).
- Use the laptop's **mDNS name** — `http://max-macbook.local:8000` — which
  survives address changes. Works well on iOS and modern Android, but not all
  Android builds, so keep a printed IP fallback.

### The HTTPS problem

On a bare LAN address there is no way to get a trusted certificate. That means:

- The **"Take a photo" camera button will not work** — iOS requires a secure
  context for `getUserMedia`.
- "Choose from library" uploads still work fine over plain HTTP.

The upload page detects this and hides the camera button rather than showing a
button that fails. If you want the camera button, you want Plan 1 or Plan 2.

### The captive portal problem

Most venue guest networks put a click-through terms page in front of the
internet. Guests who have not accepted it yet may get intercepted when they scan
the QR code. Put a line on the table card: *"Join **Venue-Guest** Wi-Fi and
accept the terms page first."*

---

## Plan 4: bring your own access points

For a genuine dead zone with no usable venue Wi-Fi.

### Do not use the MacBook as the access point

macOS Internet Sharing over Wi-Fi is a single-radio, low-client-count AP. Fine
for three laptops in a hotel room, quite bad for a hundred phones. It also can't
solve the coverage problem below, and it puts the one machine holding all your
photos out where the antennas need to be.

### Do not buy a "Wi-Fi booster" or amplifier

The link is symmetric: the phone has to be heard *back* by the AP, and a phone's
transmitter is the weak half. Amplifying only your side gives guests a strong
signal bar and failed uploads. **More access points beats more power**, nearly
always. A directional antenna on a real AP is legitimate engineering; a generic
"range extender" is usually worse than nothing, because it halves throughput and
adds a hop.

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
  powered, and out of the way.

### Kit list

| Item | Why | Rough cost |
| --- | --- | --- |
| 3-pack mesh (TP-Link Deco X20 / UniFi Express) | one per building + field | $150–250 |
| Outdoor AP (UniFi AC Mesh, Deco X50-Outdoor) | if the field AP is exposed | $100–180 |
| PoE injector + outdoor Cat6 run | powers the field AP with no outdoor outlet | $30–60 |
| Cellular hotspot or 5G router | WAN uplink — see below | you may own one |

Prefer **wired backhaul** between buildings if you can run cable. Wireless
backhaul works but roughly halves throughput per hop.

### Give the network an internet uplink anyway

The non-obvious one. **iPhones test for internet access** (they fetch
`captive.apple.com`) and, when it fails, mark the network "No Internet" and
drift back to cellular — sometimes mid-upload. Android does the same with
`connectivitycheck.gstatic.com`.

The clean fix is to give the router *any* uplink: a phone hotspot, a 5G router,
the venue's DSL. It does not need to be fast. Guest photos still travel over
local Wi-Fi at full LAN speed; the uplink exists purely so phones believe the
network is real and stay put. It also lets you run Plan 2's tunnel as a backup.

### Tell guests the network name

QR codes can encode Wi-Fi credentials. Print a second small QR on each table
card that joins the network, beside the one that opens the upload page:

```bash
tools/make_qr.py --wifi-ssid "Wedding" --wifi-password "loveislove"
```

---

## Plan 5: the hedge

If cell service is *marginal*, do both:

- Host in the cloud (Plan 1) so anyone with a bar of signal just works.
- Put one AP with a cellular uplink in the main reception room for the dead spot.

Same QR code, same domain, two paths to it. Most robust option available, and it
costs one hotspot more than Plan 1.

---

## Keeping the laptop alive (Plans 2–4)

macOS will sleep and take the server with it:

```bash
caffeinate -dimsu ./run.sh
```

`-d` display, `-i` idle, `-m` disk, `-s` on AC power, `-u` asserts user activity.
Also set Energy Saver to never sleep, and leave it on AC power, not battery.

---

## Day-of checklist

- [ ] Server reachable from a phone **on cellular** — not just your own browser
- [ ] Server reachable from a phone **on the venue Wi-Fi**
- [ ] HTTPS certificate valid, if you expect the camera button to work
- [ ] Upload a photo **and a video** from a real iPhone and a real Android
- [ ] Scan an actual printed card, at actual table lighting
- [ ] `python3 tools/doctor.py` passes
- [ ] Free disk space checked — 150 guests can produce 20 GB+
- [ ] `caffeinate` running; Energy Saver set to never sleep
- [ ] Laptop on AC power
- [ ] Someone other than the couple knows the admin password
- [ ] Printed fallback URL on every card, for cameras that won't scan
