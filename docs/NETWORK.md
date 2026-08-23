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

## The uplink is not optional: iCloud

**Confirmed by testing, and it is the single most expensive thing to get wrong.**

A guest selects photos, taps the checkmark, and their iPhone shows **"Preparing
media…"** — forever. Nothing reaches the server; the upload page never even sees
a file. It looks exactly like the software is broken, and no amount of fixing
the software helps.

What is happening: with **Settings → Photos → Optimize iPhone Storage** enabled,
full-resolution originals live in iCloud and only small previews stay on the
device. Handing a photo to a web page requires downloading the original first.
On a network with no internet, that download cannot complete, and iOS waits
indefinitely.

That setting turns itself on when a phone runs low on space, so a meaningful
share of guests will have it.

### What survives and what doesn't

| | |
| --- | --- |
| Photos taken **at the wedding** | usually fine — recent shots are still on the device |
| Anything **older**, or on a **nearly-full phone** | fails, silently, forever |
| Android | unaffected; it does not do this |

So the common case mostly works, which is exactly what makes this dangerous: it
passes a casual test and fails for the guest with three years of photos and no
free storage.

### The fix

**Give the router any internet uplink.** A phone hotspot into the WAN port is
enough. It carries no photos — uploads still travel over local wifi at full
speed — it exists purely so iPhones can materialise their own pictures.

This also settles the older question about iOS marking the network "No
Internet". That alone was cosmetic; this is not.

### If you truly cannot get an uplink

- Put a line on the table cards: *"Photos from today work best."*
- Tell guests they can set **Settings → Photos → Download and Keep Originals**,
  though asking wedding guests to change phone settings is optimistic.
- Expect to collect the rest afterwards, from the cloud instance.

The upload page helps a little: if the hand-off takes more than twenty seconds
it stops saying "getting your photos ready" and explains that iCloud photos may
not download here. That turns a mystifying freeze into something a guest can
act on, but it does not get you the photo.

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

## Plan 6: your own router, with a real certificate

*If you own a router and can put it at the venue, this is the best setup
available — and it is the only local plan that gets you a working camera
button.*

The trick is **split-horizon DNS**: your domain resolves to the MacBook's local
address at the venue, and to your cloud instance everywhere else. Same printed
QR code, both worlds.

### Why this beats a plain LAN

A bare `http://192.168.1.154:8000` has three problems: no HTTPS (so no camera
button), an address that changes, and nothing memorable to print. Pointing your
own domain at the laptop fixes all three at once.

### How it works

1. **Get a certificate at home, by DNS challenge.** Let's Encrypt normally
   proves you own a domain by connecting to it — impossible for a machine on a
   private network. The DNS-01 challenge proves ownership by putting a record
   in DNS instead, so it works for a server that is not publicly reachable at
   all:

   ```bash
   brew install certbot
   sudo certbot certonly --manual --preferred-challenges dns \
        -d photos.yourdomain.com
   ```

   It prints a TXT record to add at your registrar. Add it, wait a minute,
   press enter. You now hold a real certificate for a machine on your kitchen
   table.

   With Cloudflare as your DNS host this is fully automatic and renews itself:

   ```bash
   brew install certbot
   pip install certbot-dns-cloudflare
   sudo certbot certonly --dns-cloudflare \
        --dns-cloudflare-credentials ~/.secrets/cloudflare.ini \
        -d photos.yourdomain.com
   ```

2. **Point the domain at the laptop, on your router only.** In your router's
   DNS settings add a host override:

   ```
   photos.yourdomain.com  ->  192.168.1.154
   ```

   Look for "Local DNS", "DNS host entries", "Static DNS", or "DNS rewrite".
   OpenWrt, Asus, Ubiquiti, pfSense and AdGuard/Pi-hole all do this. Some cheap
   ISP-supplied routers do not — check yours before relying on it.

3. **Give the MacBook a fixed address**, by DHCP reservation on your own router.
   You control the DHCP server now, so this is easy and reliable.

4. **Serve HTTPS** with the certificate:

   ```bash
   caddy run --config Caddyfile
   ```

   ```
   photos.yourdomain.com {
       tls /etc/letsencrypt/live/photos.yourdomain.com/fullchain.pem \
           /etc/letsencrypt/live/photos.yourdomain.com/privkey.pem
       reverse_proxy 127.0.0.1:8000
       request_body { max_size 600MB }
       timeouts { read_body 30m }
   }
   ```

   Set `BASE_URL=https://photos.yourdomain.com` and `TRUST_PROXY=true`.

5. **Afterwards, change one DNS record.** Point the public record at your cloud
   instance, sync the night's photos up, and every card you printed keeps
   working.

### What this gets you

- A valid padlock, so **"Take a photo" works** — the single biggest guest-facing
  difference.
- Photos never leave the building; uploads run at full LAN speed regardless of
  the venue's internet.
- A memorable printed URL that survives the move to the cloud.
- No client isolation, no captive portal, no arguing with venue IT.

### What it does not solve

**Coverage.** One router will not cover two buildings and a field, however good
it is. You still need access points where the people are — see the kit list in
Plan 4. Your router becomes the thing they all plug into.

**The iOS connectivity check.** Give the router any internet uplink — a phone
hotspot is enough — or phones will mark the network "No Internet" and drift back
to cellular mid-upload. The photos still travel locally; the uplink exists to
keep phones from leaving.

**Certificate expiry.** Let's Encrypt certificates last 90 days. Issue or renew
yours in the week before, not three months ahead.

---

## Making the page open by itself

One QR code cannot both join a network and open a web page — a QR carries a
single payload, and no format both iOS and Android honour does both. (An iOS
configuration profile can, but Android ignores it and iPhones have to walk
through Settings to install one. Not worth it.)

There is a better answer than a second code: **a captive portal**. Joining the
wifi makes the page appear on its own, with no second scan at all.

### How phones decide a network needs a sign-in

On joining, every phone quietly fetches a known URL and checks the reply:

| OS | Probe | Expects |
| --- | --- | --- |
| iOS, macOS | `captive.apple.com/hotspot-detect.html` | the word `Success` |
| Android | `connectivitycheck.gstatic.com/generate_204` | HTTP 204 |
| Windows | `www.msftconnecttest.com/connecttest.txt` | `Microsoft Connect Test` |

Reply with anything else and the OS decides it is behind a sign-in page — and
opens that page immediately, unprompted. The server already answers all of
these; set `CAPTIVE_PORTAL=true` to make it serve the splash instead of the
expected reply.

**With it off, the probes are answered honestly**, so the server can never make
a working network look broken by accident.

### What it needs

The phone has to resolve those hostnames to *this machine*, which means being
the network's DNS server:

1. Run a resolver that answers everything with the MacBook's address:

   ```bash
   brew install dnsmasq
   # /opt/homebrew/etc/dnsmasq.conf
   address=/#/192.168.1.154      # every name resolves here
   ```

   ```bash
   sudo brew services start dnsmasq
   ```

2. On your router, hand out the MacBook as the DNS server over DHCP. Most
   routers expose this as "DNS server" in the DHCP or LAN settings.

3. Serve on **port 80** — the probes do not use any other port:

   ```bash
   sudo .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 80
   ```

4. Set `CAPTIVE_PORTAL=true`.

### The catch, and why the splash is a signpost

iOS opens captive portals in a stripped-down browser (the Captive Network
Assistant) that **does not reliably support file pickers**. Putting the upload
page there directly would give guests a button that does nothing.

So the splash is deliberately not the upload page. It confirms they are
connected and points them at the address to open in their real browser. The
printed card still carries the QR for exactly that, and now it is the only
thing left to do.

### Open network, no password

Dropping the password removes a step and shortens the card, and `make_qr.py`
prints "no password needed" so nobody hunts for one:

```bash
tools/make_qr.py --wifi-ssid "MaxRachel" --copies 18 --layout sheet
```

Anyone in range can join, which for a private event behind a door is a fair
trade. They reach an upload page and nothing else; there is no path from there
to the rest of your network, and anything unwelcome can be removed in `/admin`.
If the venue is somewhere with public footfall, set `MODERATION=slideshow` so
nothing reaches the projector unapproved.

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

- [ ] **Router has an internet uplink** — without one, guests with iCloud
      photos cannot upload at all (see above; this is not optional)
- [ ] Tested with a phone that has **Optimize iPhone Storage** on
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
