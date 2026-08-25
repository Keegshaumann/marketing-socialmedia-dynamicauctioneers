# 4. Operations Runbook

Everything needed to run, deploy and troubleshoot the live platform.

## 4.1 The server

| | |
|---|---|
| **Host** | Hostinger VPS, Ubuntu 24.04 LTS |
| **IP** | `46.202.175.127` |
| **Live URL** | `https://46.202.175.127.nip.io` — **temporary**, see 4.7 |
| **SSH** | `ssh root@46.202.175.127` |
| **Application directory** | `/opt/da-marketing` (a clone of the repository) |
| **Runs as** | systemd service `da-marketing` — one uvicorn process on `127.0.0.1:8000`, behind nginx, as the non-root user `dauction` |
| **Verified** | Responding HTTP 200 on 20 August 2026 |

### Layout on the box

| Thing | Path |
|---|---|
| Code | `/opt/da-marketing` |
| Virtual environment | `/opt/da-marketing/venv` |
| Secrets | `/opt/da-marketing/.env` — mode `600`, owner `dauction` |
| Database | `/opt/da-marketing/engine.db` — mode `600` |
| Property data, uploads, artifacts | `/opt/da-marketing/DP<dp>/...` |
| systemd unit | `/etc/systemd/system/da-marketing.service` (copy in `docs/deploy/`) |
| nginx site | `/etc/nginx/sites-available/da-marketing` (copy in `docs/deploy/`) |
| TLS certificate | `/etc/letsencrypt/live/46.202.175.127.nip.io/` — auto-renews |

## 4.2 Everyday commands

```bash
systemctl status da-marketing        # is it running
systemctl restart da-marketing       # restart after a config or code change
journalctl -u da-marketing -f        # live logs
journalctl -u da-marketing --since "10 min ago"
```

## 4.3 Deploying an update

Push to GitHub, then from the repository root on the development machine:

```bash
./scripts/deploy.sh
```

It SSHes in, pulls `main` fast-forward only, reinstalls dependencies **only if
`requirements.txt` changed**, restarts the service, and confirms the site is
back at HTTP 200. The SSH key must be loaded in the agent
(`ssh-add --apple-use-keychain`).

By hand, the same thing:

```bash
ssh root@46.202.175.127
cd /opt/da-marketing
sudo -u dauction git pull
sudo -u dauction ./venv/bin/pip install -q -r requirements.txt   # only if deps changed
systemctl restart da-marketing
```

`engine.db` and the `DP<dp>/` folders are untracked, so a pull never touches
data.

## 4.4 Users and roles

| Role | Can do |
|---|---|
| `admin` | Everything, including the Settings screens that hold API credentials |
| `marketing` | Run properties end to end — intake, photographs, edits, regeneration |
| `approver` | Sign the gates. Can act from the approval email without logging in |

The split exists so operational staff run everything but **never touch
credentials** (D34).

**Adding a user:** log in as `admin@dynamicauctioneers.co.za` and use the
Settings screen. The admin temporary password is printed **once** to the journal
on first boot:

```bash
journalctl -u da-marketing | grep -i "temp password"
```

Change it on first login. A forced first-login password change is a recommended
improvement that has not been built.

**Sign-in hardening in place (D44):** bcrypt with per-password salts;
HMAC-signed HttpOnly SameSite=lax session cookies; a brute-force throttle keyed
on the account email (8 failures in 5 minutes → 15 minute lockout, tunable with
`LOGIN_THROTTLE_MAX_FAILS`, `LOGIN_THROTTLE_WINDOW`, `LOGIN_THROTTLE_LOCKOUT`);
constant-time login so response timing does not reveal which emails are
registered; a generic error message; per-request user reload so a deleted or
role-changed account takes effect immediately; and failed logins logged with
email and source IP, never the password.

The throttle is keyed on the **account**, not the client IP, because behind the
nginx proxy the peer is localhost and `X-Forwarded-For` is attacker-controlled.
The accepted trade-off is that someone who knows an email can nuisance-lock it;
the window self-clears and the signed email approval links keep working.

## 4.5 Developer SSH access

Full instructions, including key generation on macOS and Windows, are in
`docs/SERVER-ACCESS.md`. In short: each developer generates their **own** key
pair and sends only the **public** half (`~/.ssh/id_ed25519.pub`, one line).

```bash
ssh root@46.202.175.127
echo "ssh-ed25519 AAAA... theirname-dev" >> ~/.ssh/authorized_keys
```

Revoke by deleting their line. One key per person, no shared secrets. Private
keys are never emailed, pasted into chat, or committed.

## 4.6 Environment variables

Held in `/opt/da-marketing/.env` (mode 600). The template is `.env.example` in
the repository, which is kept blank.

### Core

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Extraction, verification and copy. Without it the system falls back to templates and deterministic checks rather than failing |
| `ENGINE_DB` | Database path. Defaults to `./engine.db` |
| `APP_SECRET` | Signing secret for sessions and approve-by-email tokens. Generated at provisioning |
| `ENGINE_ALLOW_INSECURE_COOKIE` | **Local development only.** Set to `1` to stop the session cookie being marked Secure. Leave unset in production |

> **The session cookie is Secure by default.** Setting
> `ENGINE_ALLOW_INSECURE_COOKIE` logs a loud warning on every boot, so a
> misconfigured production box is obvious in the journal.
>
> **Documentation drift, worth knowing:** `.env.example`, `docs/deploy/DEPLOY.md`
> and decision D44 all refer to `ENGINE_HTTPS=true` as the thing that marks the
> cookie Secure. **No code reads `ENGINE_HTTPS`.** The behaviour is correct and
> secure by default — the variable is simply vestigial and the older documents
> are stale. Do not rely on setting it.

### Rendering

| Variable | Purpose |
|---|---|
| `ENGINE_RENDERER` | `html` (default), `canva`, or `mixed` |
| `ENGINE_PDF_EXPORT` | PDF export control |
| `ENGINE_AI_CACHE` | Location of the content-addressed cache for the paid calls |
| `EXTRACT_PDF_MODE` | `pdf` (default, native document blocks) or the text fallback |
| `EXTRACT_PACE_SECONDS` | Call pacing. A legacy workaround for the old 10k tokens/minute tier; not needed now |

### Distribution — GoHighLevel

| Variable | Purpose |
|---|---|
| `GHL_API_TOKEN` | The `pit-...` token. Normally entered in the platform Settings screen and stored in the database, not here |
| `GHL_LOCATION_ID` | Location |
| `GHL_USER_ID` | User |
| `GHL_ACCOUNT_MAP` | JSON mapping channel to account id, e.g. `{"facebook":"acc_id","instagram":"acc_id"}` |
| `GHL_POST_STATUS` | **The guard rail.** `draft` overrides any per-post choice made in the UI |

### Canva (optional)

`CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REFRESH_TOKEN`,
`CANVA_REDIRECT_URI`, `CANVA_TEMPLATE_MAP`, `CANVA_STATE_FILE`.

Only needed with `ENGINE_RENDERER=canva` or `mixed`. `CANVA_TEMPLATE_MAP` is
JSON, either flat (one design set) or named sets, where the **first set is the
default** and defines which formats route through Canva. Refresh-token rotation
is persisted to the state file. Authorisation helpers are in
`scripts/canva_authorize.py` and `scripts/canva_reauth.py`.

### Microsoft Graph (optional, not on the critical path)

`MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_TENANT_ID`,
`MS_GRAPH_SITE_ID`, `MS_GRAPH_DRIVE_ID`.

## 4.7 Cutting over to the real domain

The platform should be on `marketing.dynamicauctioneers.co.za`. The nip.io
hostname is a stand-in. This needs whoever holds the DNS.

1. Add an **A record** for the subdomain pointing at `46.202.175.127`. Wait for
   it to resolve: `dig +short marketing.dynamicauctioneers.co.za`.
2. On the server:

```bash
sed -i 's/server_name .*/server_name marketing.dynamicauctioneers.co.za;/' \
  /etc/nginx/sites-available/da-marketing
nginx -t && systemctl reload nginx
certbot --nginx -d marketing.dynamicauctioneers.co.za --redirect \
  --non-interactive --agree-tos -m keegs.haumann@gmail.com
```

3. Nothing else changes. The old nip.io certificate can be left to expire.

## 4.8 Rebuilding the box from scratch

Install `python3-venv python3-pip nginx certbot python3-certbot-nginx git`;
clone the repository to `/opt/da-marketing`; create the virtual environment and
`pip install -r requirements.txt`; run `playwright install chromium` and
`playwright install-deps chromium`; copy `.env` across and append `ENGINE_DB`, a
generated `APP_SECRET`; create the `dauction` system user and
`chown -R dauction:dauction /opt/da-marketing`; install the systemd unit and
nginx site from `docs/deploy/`; set the `output_root` database setting to
`/opt/da-marketing`; then run `certbot --nginx`.

## 4.9 Troubleshooting

| Symptom | Where to look |
|---|---|
| Site down | `systemctl status da-marketing`, then `journalctl -u da-marketing --since "10 min ago"` |
| A property stuck in a state | The job worker. Failed jobs park the record with the raw model output attached — check the journal for the job kind (`extract`, `verify`, `render`, `post`) |
| Extraction returning nothing | Check `ANTHROPIC_API_KEY` is present. Without it the system degrades to deterministic checks and template copy rather than erroring |
| Adverts render but the PNG does not | Playwright Chromium is missing. `playwright install chromium` in the virtual environment |
| Posts not appearing on a page | Check `GHL_POST_STATUS` — if it is `draft`, everything lands as a draft in the planner regardless of the UI choice. This is intended |
| Cookie warnings on boot | `ENGINE_ALLOW_INSECURE_COOKIE` is set. It should not be, in production |
| OTP or levy figures read as missing | Should no longer happen (D77). If it does, confirm PyMuPDF is installed — the readers no longer use `pdftotext`, which the server does not have |

## 4.10 Backups — the open gap

**Not yet configured.** This is the most significant operational gap in the
system.

The database is small and holds every record, approval and audit event. The
`DP<dp>/` folders hold the source documents, photographs and artifacts.

Two options, either acceptable:

- Hostinger's paid daily VPS auto-backup — simplest.
- A cron job running `sqlite3 engine.db ".backup ..."` and pushing the copy
  off-box, for example rclone to Backblaze B2 or to OneDrive.

Until one is in place, a lost VPS is a lost system.
