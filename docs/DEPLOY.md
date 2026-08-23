# Putting it on a server

You need this for the **permanent, after-the-wedding instance** — the one guests
keep uploading to for weeks. You may also want it for the wedding day itself if
you're going with the tunnel plan in `NETWORK.md`.

Everything here assumes you own a domain. If you don't, buy one first; it costs
a few dollars and it's what makes the QR codes durable.

---

## Why HTTPS is not optional

- iOS blocks camera access outside a secure context, so the **"Take a photo"
  button silently disappears** over plain HTTP.
- Browsers increasingly warn on file uploads to HTTP pages, which looks alarming
  on a page asking for your wedding photos.

Both options below get you a real certificate automatically.

---

## Option A: a small VM with Caddy

Any $5/month VM works. Hetzner CX22, DigitalOcean, Linode, Vultr. Take 2 GB RAM
and as much disk as you can — **80 GB if you expect video**.

### 1. Install

```bash
sudo apt update && sudo apt install -y python3-venv ffmpeg git caddy
sudo useradd -r -m -d /srv/photos photos
sudo -u photos git clone https://github.com/mxmrsn/qr-photo-server.git /srv/photos/app
cd /srv/photos/app
sudo -u photos python3 -m venv .venv
sudo -u photos .venv/bin/pip install -r requirements.txt
```

### 2. Configure

```bash
sudo -u photos cp .env.example .env
sudo -u photos nano .env
```

The settings that matter on a server:

```ini
BASE_URL=https://photos.yourdomain.com
HOST=127.0.0.1          # Caddy is the only thing that should reach it directly
PORT=8000
TRUST_PROXY=true        # so rate limiting sees real client addresses
ADMIN_PASSWORD=<something long>
POST_EVENT=true         # once the wedding has happened
```

`TRUST_PROXY=true` is correct **only** behind a proxy that sets
`X-Forwarded-For`. Directly exposed, it would let anyone forge their address and
walk around the rate limit.

### 3. Run it as a service

```ini
# /etc/systemd/system/photos.service
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
```

```bash
sudo systemctl enable --now photos
sudo journalctl -u photos -f      # the admin password is printed here on first start
```

### 4. Caddy

```
# /etc/caddy/Caddyfile
photos.yourdomain.com {
    reverse_proxy 127.0.0.1:8000

    # Phones on a slow uplink need a long time to push a 300 MB video.
    request_body {
        max_size 600MB
    }
    timeouts {
        read_body 30m
    }
}
```

```bash
sudo systemctl reload caddy
```

Point an A record at the VM and Caddy gets a certificate on the first request.
Keep `max_size` comfortably above `MAX_FILE_MB`, or large uploads fail at the
proxy before the app ever sees them.

### If you use nginx instead

```nginx
client_max_body_size 600M;
proxy_request_buffering off;   # stream to the app instead of spooling to disk first
proxy_read_timeout 1800s;
proxy_send_timeout 1800s;
```

`proxy_request_buffering off` matters — without it nginx writes the entire video
to its own disk before forwarding, doubling the write and the wait.

---

## Option B: Fly.io

Cheap, and the free tier may cover you entirely.

```toml
# fly.toml
app = "our-wedding-photos"
primary_region = "iad"

[build]
  builder = "paketobuildpacks/builder:base"

[env]
  BASE_URL  = "https://our-wedding-photos.fly.dev"
  HOST      = "0.0.0.0"
  PORT      = "8080"
  DATA_DIR  = "/data"
  TRUST_PROXY = "true"

[[mounts]]
  source      = "photo_data"
  destination = "/data"

[http_service]
  internal_port = 8080
  force_https   = true
  auto_stop_machines  = false      # keep it warm; a cold start mid-upload fails
  min_machines_running = 1
```

```bash
fly volumes create photo_data --size 40      # GB — size for your video estimate
fly secrets set ADMIN_PASSWORD='something long'
fly deploy
```

**The volume is the whole point** — without `[[mounts]]`, every deploy wipes the
photos. And leave `auto_stop_machines` off: a machine that suspends mid-upload
drops the guest's file.

Fly's buildpack won't include `ffmpeg`, so videos will be rejected unless you
use a Dockerfile:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--timeout-keep-alive", "75"]
```

Then drop the `[build]` block from `fly.toml`.

---

## Option C: a tunnel to your own machine

No server at all — the laptop keeps the photos and a tunnel gives it a public
HTTPS address. Setup is in `NETWORK.md` under *Plan 2*. Good for the wedding day
itself; less good as the permanent home, since it needs your laptop to stay on.

---

## Moving the venue collection up

Once the cloud instance is running:

```bash
tools/sync.py --to https://photos.yourdomain.com --token <its admin password> --dry-run
tools/sync.py --to https://photos.yourdomain.com --token <its admin password>
```

Resumable and idempotent. On a home connection with 20 GB of video this will
take hours — start it before bed. `--limit 5` first to prove the path works.

---

## Backups

The entire state is one directory. Two copies, in two places, from the night of:

```bash
# On the venue laptop, before you go to sleep
cp -a data /Volumes/BackupDrive/wedding-photos-$(date +%F)

# From the cloud instance
rsync -avz --progress user@server:/srv/photos/app/data/ ~/wedding-backup/
```

You can also just hit **Download all** in `/admin`, which gives you every
original in one zip with a manifest.

For the cloud instance, a nightly copy of `data/` to object storage is worth the
few cents:

```bash
# /etc/cron.daily/photo-backup
rclone sync /srv/photos/app/data remote:wedding-photos
```

`data/wedding.db` is SQLite in WAL mode — copy `wedding.db`, `wedding.db-wal`
and `wedding.db-shm` together, or use `sqlite3 wedding.db ".backup out.db"` for a
consistent snapshot while the server is running.

---

## Sizing

| Guests | Items | Photos only | With video |
| --- | --- | --- | --- |
| 50 | ~500 | ~3 GB | ~15 GB |
| 150 | ~1,800 | ~10 GB | ~50 GB |
| 250 | ~3,000 | ~17 GB | ~85 GB |

Derivatives add roughly 15% on top. Video is what blows the estimate — one guest
filming a whole toast can be 2 GB by itself. Check free space with
`tools/doctor.py` and give yourself a wide margin.

---

## Hardening for a public instance

The defaults are tuned for a private event, but once the URL is on the internet:

- Set a long `ADMIN_PASSWORD` rather than using the generated one.
- Keep `ALLOW_GUEST_DOWNLOAD=false` unless you want originals downloadable.
- Consider `MODERATION=all` for the post-event instance if the link may spread.
- Lower `UPLOAD_RATE_PER_HOUR` from 400 if you're worried about abuse.
- The admin cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` whenever
  `BASE_URL` is HTTPS — so make sure it is.
