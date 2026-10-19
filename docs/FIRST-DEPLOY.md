# Putting this on the internet, start to finish

Written for someone who has never hosted a website or opened AWS. Every
command is copy-paste. Budget about two hours, most of it waiting.

## Do these in this order

The ordering matters because **one step unblocks your printing deadline and
the rest don't.**

| | Step | Why this order |
| --- | --- | --- |
| 1 | **Buy the domain** | The only thing the printed cards depend on |
| 2 | **Print the cards** | Can happen while you build everything else |
| 3 | Create the AWS account | |
| 4 | Create the server | |
| 5 | Install the app | |
| 6 | Point the domain at it | |
| 7 | Turn on HTTPS | |
| 8 | Test from a phone | |

Once the domain exists, the cards are correct forever — the server behind it
can be rebuilt, moved, or replaced without reprinting anything. That is the
whole reason to buy a domain rather than print an IP address.

---

## 1. Buy the domain

**Cloudflare Registrar** — sells at cost, no upsells, and you will want their
DNS in step 6 anyway. Porkbun is a fine alternative.

1. Make a Cloudflare account at `dash.cloudflare.com`
2. **Domain Registration -> Register Domain**
3. Search for the name, buy it (~$11/year for a `.com`)

Pick something short — it gets printed under the QR as a fallback for anyone
whose camera won't scan. `maxrachelwedding.com` was available when we checked.

You can re-check availability any time:

```bash
tools/check_domain.py maxrachelwedding.com ourwedding.photos
```

**You will use `photos.yourdomain.com`** — a subdomain — throughout. That
leaves the bare domain free for a wedding website later.

---

## 2. Print the cards

As soon as the domain is yours, regenerate and print. Nothing else needs to
exist yet.

```bash
# in .env
BASE_URL=https://photos.yourdomain.com
```

```bash
tools/make_test_signs.py          # five motifs, two per sheet, for a test print
```

Scan the test print with both your phones before committing to a full run.
Then produce the real batch with whichever motif you picked.

**Hosting in the cloud means guests no longer need your Wi-Fi**, so the
two-step "join the Wi-Fi first" card is no longer needed:

```bash
tools/make_test_signs.py --no-wifi
```

The card becomes a single instruction. Measured, the code itself stays the
same size — 732px either way, because the simpler layout uses larger gaps
that absorb the saving — so this is a legibility choice, not a scanning one.

---

## 3. Create the AWS account

`aws.amazon.com` -> **Create an AWS Account**. Needs an email, a card, and a
phone number. The card is required even for free-tier services.

Two things to do immediately after, before anything else:

- **Turn on MFA for the root account.** Account menu -> Security credentials.
  This account can spend money; protect it like a bank login.
- **Set a billing alarm.** Billing -> Budgets -> Create budget -> $20/month.
  You will not hit it, and if something goes wrong you will know in hours
  rather than at the end of the month.

---

## 4. Create the server

Go to `lightsail.aws.amazon.com`.

1. **Create instance**
2. Region: pick the one nearest your guests
3. Platform: **Linux/Unix**
4. Blueprint: **OS Only -> Ubuntu 24.04 LTS**
   (not an app blueprint — you want a plain machine)
5. Plan: **$12/month** — 2 GB RAM, 60 GB SSD
6. Name it `wedding-photos`
7. **Create instance**, then wait about a minute for it to say *Running*

### Give it a fixed address

**Networking -> Create static IP -> attach to `wedding-photos`.**

Without this the address changes on restart and your domain stops resolving.
Static IPs are free while attached and **charged while unattached**, so
release it when you delete the instance.

Write the IP down. It looks like `52.14.231.9`.

### Open the firewall

Lightsail has its own firewall, separate from Ubuntu's. This is the single
most common stumbling block: SSH works, the browser times out, and nothing in
the logs explains it.

**Instance -> Networking -> IPv4 Firewall -> Add rule**, twice:

- HTTP, port **80**
- HTTPS, port **443**

---

## 5. Install the app

Click **Connect using SSH** in the Lightsail console. A terminal opens in your
browser — no keys to configure.

Paste this block. It takes a few minutes.

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg git caddy

sudo useradd -r -m -d /srv/photos photos
# useradd -m creates the home directory as mode 750, so your own login
# cannot even cd into it. Without this the next commands silently run in
# the wrong directory.
sudo chmod 755 /srv/photos

sudo -u photos git clone https://github.com/mxmrsn/qr-photo-server.git /srv/photos/app
sudo -u photos python3 -m venv /srv/photos/app/.venv
sudo -u photos /srv/photos/app/.venv/bin/pip install --upgrade pip
sudo -u photos /srv/photos/app/.venv/bin/pip install -r /srv/photos/app/requirements.txt

# Absolute paths everywhere above, so there is no cd to go wrong. This one
# needs the project root on sys.path, hence the subshell.
sudo -u photos bash -c 'cd /srv/photos/app && \
  .venv/bin/python -c "from app.transcribe import _get_model; _get_model()"'

ls /srv/photos/app        # sanity check: should list app/ tools/ requirements.txt
```

That last line downloads the speech model used for video captions. Doing it
now means the first video upload isn't also a 141 MB download.

### Configure it

```bash
sudo -u photos nano /srv/photos/app/.env
```

```ini
PROFILE=wedding
COUPLE_NAMES=Max & Rachel
EVENT_DATE=November 11, 2026
BASE_URL=https://photos.yourdomain.com
HOST=127.0.0.1
PORT=8000
TRUST_PROXY=true
QR_ACCENT=#9b8aa6
MODERATION=slideshow
ADMIN_PASSWORD=pick-something-long-here
```

`Ctrl+O`, `Enter`, `Ctrl+X` to save and quit nano.

### Run it as a service

```bash
sudo tee /etc/systemd/system/photos.service > /dev/null <<'EOF'
[Unit]
Description=Wedding photo server
After=network.target

[Service]
User=photos
WorkingDirectory=/srv/photos/app
ExecStart=/srv/photos/app/.venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8000 --timeout-keep-alive 75
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now photos
sudo systemctl status photos --no-pager
```

Green `active (running)` means it's up. It isn't reachable from outside yet —
that's step 7.

---

## 6. Point the domain at it

Back in Cloudflare -> your domain -> **DNS -> Add record**:

| Field | Value |
| --- | --- |
| Type | **A** |
| Name | **photos** |
| IPv4 address | your static IP |
| Proxy status | **DNS only** (grey cloud, not orange) |

**The grey cloud matters.** Cloudflare's proxy caps uploads at 100 MB on the
free plan, which would silently break video. Turn it off.

Wait a couple of minutes, then check from your laptop:

```bash
dig +short photos.yourdomain.com
```

It should print your IP. If it prints nothing, wait longer.

---

## 7. Turn on HTTPS

Back in the Lightsail terminal:

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
photos.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
    request_body { max_size 600MB }
    timeouts { read_body 30m }
}
EOF

sudo systemctl reload caddy
```

Caddy fetches a certificate automatically on the first request. That's why
step 6 had to come first — the certificate authority checks that the domain
really points here.

`request_body` and `timeouts` are not optional: without them a phone uploading
a long video on a slow connection gets cut off by the proxy before the app
ever sees the file.

**HTTPS is what re-enables the "Take a photo" button.** iOS blocks camera
access on plain HTTP, so this is a visible, guest-facing difference.

---

## 8. Test it

Open `https://photos.yourdomain.com` on your phone, **on cellular with Wi-Fi
off** — that proves it works from anywhere, not just your house.

Check:

- [ ] Padlock in the address bar
- [ ] **"Take a photo"** button is visible (it is not, over plain HTTP)
- [ ] Upload a photo, and a video
- [ ] Upload something **stored in iCloud** — the case that failed locally
- [ ] `/gallery` shows them
- [ ] `/slideshow` plays, muted, with captions on the video
- [ ] `/admin` accepts your password
- [ ] Scan the printed card and confirm it lands here

Run the built-in check too:

```bash
cd /srv/photos/app && .venv/bin/python tools/doctor.py --check-url
```

---

## Useful commands

```bash
sudo systemctl restart photos         # restart after a config change
sudo journalctl -u photos -f          # watch the live log
sudo journalctl -u photos -n 100      # last 100 lines
df -h /                               # disk space
cd /srv/photos/app && git pull && sudo systemctl restart photos   # update
```

---

## When it's over

```bash
tools/teardown.sh <your-static-ip>
```

Copies every photo home, verifies the count and opens the database to confirm
it's intact, then prints the steps to delete the instance and release the IP.
It won't delete anything itself — that's deliberate.

**Make a second copy before you delete the server.** One copy is not a backup.

---

## If something doesn't work

| Symptom | Cause |
| --- | --- |
| `cd: /srv/photos/app: Permission denied` | `useradd -m` made the home 750 — `sudo chmod 755 /srv/photos` |
| `.venv` appeared in `/home/ubuntu` | Same cause: the `cd` failed and later commands ran in your own home |
| Browser times out, SSH fine | Lightsail firewall — ports 80/443 not opened (step 4) |
| "Connection refused" | App isn't running: `sudo systemctl status photos` |
| Certificate error | DNS not pointing here yet, or orange cloud is on |
| Large videos fail at ~100 MB | Cloudflare proxy is on — set the record to DNS only |
| Large videos fail at ~1 MB | `request_body` missing from the Caddyfile |
| Camera button missing | Not on HTTPS |
| Video uploads rejected | `ffmpeg` not installed |
| Captions never appear | Model not downloaded — rerun the `_get_model()` line |
