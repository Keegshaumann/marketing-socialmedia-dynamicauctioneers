# Deployment & operations runbook

The marketing platform runs on a **Hostinger VPS** (Ubuntu 24.04 LTS).

- **Live URL:** https://46.202.175.127.nip.io (temporary hostname — see "Swap to a
  real domain" below)
- **Server IP:** `46.202.175.127`  •  SSH: `ssh root@46.202.175.127`
- **App directory:** `/opt/da-marketing` (a clone of this repo)
- **Runs as:** systemd service `da-marketing`, a single uvicorn process on
  `127.0.0.1:8000`, behind nginx. Non-root user `dauction`.

## Layout on the server

| Thing | Path |
|---|---|
| Code (git clone) | `/opt/da-marketing` |
| Virtualenv | `/opt/da-marketing/venv` |
| Secrets | `/opt/da-marketing/.env` (mode 600, owner `dauction`) |
| Database | `/opt/da-marketing/engine.db` (`ENGINE_DB`) |
| Property data / uploads / artifacts | `/opt/da-marketing/DP<dp>/...` (`output_root=/opt/da-marketing`) |
| systemd unit | `/etc/systemd/system/da-marketing.service` (copy in `docs/deploy/`) |
| nginx site | `/etc/nginx/sites-available/da-marketing` (copy in `docs/deploy/`) |
| TLS cert | `/etc/letsencrypt/live/46.202.175.127.nip.io/` (auto-renews) |

## Everyday operations

```bash
systemctl status da-marketing        # is it running?
systemctl restart da-marketing       # restart (after a config or code change)
journalctl -u da-marketing -f        # live logs
journalctl -u da-marketing --since "10 min ago"
```

## Deploy a code update

The app is a git clone, so updates are a pull + restart:

```bash
ssh root@46.202.175.127
cd /opt/da-marketing
sudo -u dauction git pull
sudo -u dauction ./venv/bin/pip install -q -r requirements.txt   # only if deps changed
systemctl restart da-marketing
```

(Data — `engine.db` and the `DP<dp>/` folders — is untracked, so `git pull`
never touches it.)

## Add a user

Log in to the web app as `admin@dynamicauctioneers.co.za` and use the Settings /
users screen to create `marketing` (Nikki) and `approver` accounts. The admin
temp password is printed once to the journal on first boot:

```bash
journalctl -u da-marketing | grep -i "temp password"
```

Change the admin password on first login.

## Give a developer SSH access

Get their **public** key (`~/.ssh/id_ed25519.pub`, one line) — see
`docs/SERVER-ACCESS.md` — then:

```bash
ssh root@46.202.175.127
echo "ssh-ed25519 AAAA... theirname-dev" >> ~/.ssh/authorized_keys
```

Revoke by deleting their line from `~/.ssh/authorized_keys`.

## Swap to a real domain (marketing.dynamicauctioneers.co.za)

1. In DNS, add an **A record** for the subdomain → `46.202.175.127`. Wait for it
   to resolve (`dig +short marketing.dynamicauctioneers.co.za`).
2. On the server, set the nginx `server_name` to the new domain and reload:
   ```bash
   sed -i 's/server_name .*/server_name marketing.dynamicauctioneers.co.za;/' \
     /etc/nginx/sites-available/da-marketing
   nginx -t && systemctl reload nginx
   certbot --nginx -d marketing.dynamicauctioneers.co.za --redirect \
     --non-interactive --agree-tos -m keegs.haumann@gmail.com
   ```
3. `ENGINE_HTTPS=true` is already set, so nothing else changes. The old nip.io
   cert can be left to expire.

## First-time provisioning (how this box was built)

For rebuilding from scratch: install `python3-venv python3-pip nginx certbot
python3-certbot-nginx git`; clone the repo to `/opt/da-marketing`; create the
venv and `pip install -r requirements.txt`; copy `.env` (never committed) and
append `ENGINE_DB`, a generated `APP_SECRET`, and `ENGINE_HTTPS`; create the
`dauction` system user and `chown -R dauction:dauction /opt/da-marketing`;
install the systemd unit and nginx site from `docs/deploy/`; set the
`output_root` DB setting to `/opt/da-marketing`; then `certbot --nginx`.

## Backups (TODO)

Not yet configured. Options: Hostinger's paid daily VPS auto-backup (simplest),
or a cron job that runs `sqlite3 engine.db ".backup ..."` and pushes the copy
off-box (rclone to Backblaze B2, or OneDrive). The DB is small.
