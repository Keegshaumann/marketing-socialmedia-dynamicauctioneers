# Deployment & operations runbook

The marketing platform runs on a **Hostinger VPS** (Ubuntu 24.04 LTS).

- **Live URL:** https://studio.dynamicauctioneers.co.za (since 24 Nov 2026). The
  old https://46.202.175.127.nip.io still serves the same app and is deliberately
  not redirected, so nobody using it is signed out (see "Addresses" below).
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
| nginx sites | `/etc/nginx/sites-available/da-studio` (studio.dynamicauctioneers.co.za) and `da-marketing` (nip.io); base copies in `docs/deploy/nginx-da-*.conf` |
| TLS certs | `/etc/letsencrypt/live/studio.dynamicauctioneers.co.za/` and `/etc/letsencrypt/live/46.202.175.127.nip.io/` (both auto-renew) |

## Everyday operations

```bash
systemctl status da-marketing        # is it running?
systemctl restart da-marketing       # restart (after a config or code change)
journalctl -u da-marketing -f        # live logs
journalctl -u da-marketing --since "10 min ago"
```

## Deploy a code update

**Fastest:** push to GitHub, then from the repo root on your Mac run:

```bash
./scripts/deploy.sh
```

It SSHes in, pulls `main`, reinstalls deps only if `requirements.txt` changed,
restarts the service, and confirms the site is back at HTTP 200.

**By hand** (the same thing, if you prefer):

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

There is no users screen. Accounts are made on the server with
`scripts/create_user.py`, which generates the password, prints it once and
stores only its bcrypt hash. Roles: `marketing` (Nikki) and `approver` run the
marketing pipeline; `properties` opens auction proposals and nothing else (D104).

```bash
ssh root@46.202.175.127
cd /opt/da-marketing
sudo -u dauction ./venv/bin/python scripts/create_user.py someone@dynamicauctioneers.co.za properties
```

`--reset` gives an existing account a new password. The bootstrap admin's temp
password is printed once to the journal on first boot:

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

## Addresses

Both names reach the same uvicorn process and the same database:

- **studio.dynamicauctioneers.co.za**: an A record at Xneelo (the
  dynamicauctioneers.co.za DNS, Dynamic Auctioneers account, next to `clerk` and
  `director`) pointing at `46.202.175.127`, served by the `da-studio` nginx site
  with its own Let's Encrypt certificate.
- **46.202.175.127.nip.io**: the original address, still served by the
  `da-marketing` site for the properties team's bookmarks.

Sign-in cookies belong to one host name, so anyone moving to the new address
signs in once there. To retire the nip.io name later, change the `da-marketing`
site to `return 301 https://studio.dynamicauctioneers.co.za$request_uri;` and
let its certificate lapse.

When checking a new Xneelo record, ask Xneelo's own nameserver
(`dig +short @ns1.host-h.net A <name>`): a public resolver asked before the
record exists caches the "no such name" answer for 30 minutes.

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
