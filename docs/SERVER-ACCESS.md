# Server Access (SSH)

How a developer gets access to the Dynamic Auctioneers marketing-platform
server (Hostinger VPS, Ubuntu 24.04 LTS).

## How SSH keys work (read this first)

An SSH key comes in **two halves**:

- **Private key** (`id_ed25519`, no extension) — stays on your machine. It is a
  secret. It is never shared, emailed, pasted into chat, or committed to git.
- **Public key** (`id_ed25519.pub`) — safe to share. This is the half that gets
  installed on the server.

Every developer generates their **own** key pair and sends the admin only the
**public** half. Anyone whose public key is on the server can log in with their
matching private key, so there is one key per person and no shared secrets.

---

## 1. Generate your key

### macOS / Linux

Open a terminal and run (replace the label with your own name):

```bash
ssh-keygen -t ed25519 -C "yourname-dev"
```

- Press **Enter** to accept the default location (`~/.ssh/id_ed25519`).
- Set a **passphrase** when prompted (recommended — it encrypts the private key
  so a lost laptop does not equal a lost server).
- Note: the terminal shows **nothing** while you type the passphrase (no dots or
  asterisks). That is normal. Type it, Enter, type it again to confirm.
- If it warns the file **already exists**, choose **no** and use your existing
  key — do not overwrite it.

### Windows

Windows 10/11 has OpenSSH built in — run the same command in **PowerShell** or
**Windows Terminal**. If OpenSSH is missing, install it (Settings → Optional
Features) or use **PuTTY** (`puttygen`) to generate an Ed25519 key.

---

## 2. Copy your public key

### macOS
```bash
pbcopy < ~/.ssh/id_ed25519.pub
```
(Copies it straight to the clipboard.)

### Linux
```bash
cat ~/.ssh/id_ed25519.pub
```

### Windows (PowerShell)
```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard
```

The content is a single line that starts with `ssh-ed25519 AAAA…` and ends with
your label. That whole line is your public key.

---

## 3. Get your key onto the server

Send your **public** key line to the server admin (Keegan). The admin installs
it one of two ways:

- **Hostinger SSH Keys manager** — hPanel → VPS → SSH Keys → add the key (it can
  hold more than one, one per developer), or
- **Directly on the box** — append the line to `~/.ssh/authorized_keys`.

Do **not** send your private key. If anyone ever asks for your private key, that
is wrong — only the `.pub` half is ever shared.

---

## 4. Connect

Once your key is installed:

```bash
ssh <user>@<SERVER_IP>
```

- `<user>` — the login the admin gives you (e.g. `root`, or your own user).
- `<SERVER_IP>` — the VPS IP address (or the domain once one is pointed at it,
  e.g. `marketing.dynamicauctioneers.co.za`).

If you set a passphrase, you will be asked for it. On macOS you can save it to
the Keychain so you only type it once:

```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519
```

---

## Security rules (non-negotiable)

- **Private keys never leave the machine they were made on.** Never email,
  message, paste, or commit one.
- **One key per person.** Do not share a single key between developers — access
  must be individually revocable.
- **If a private key is ever exposed** (laptop lost, key pasted somewhere),
  tell the admin immediately: the public key is removed from the server and you
  generate a fresh pair.
- **Secrets live on the server only.** The `.env` (Anthropic + GoHighLevel keys,
  `GHL_POST_STATUS=draft` guard rail) is never committed to git.

---

## Admin: managing developer access

**Add a developer** — with their public key line:

```bash
# on the server
echo "ssh-ed25519 AAAA… theirname-dev" >> ~/.ssh/authorized_keys
```

or add it via Hostinger's SSH Keys manager.

**Revoke a developer** — remove their line from `~/.ssh/authorized_keys` (and
delete it from Hostinger's SSH Keys manager). Access is gone immediately; no
other developer is affected.

**List who has access** — each line in `~/.ssh/authorized_keys` ends with a
label, so keep labels descriptive (`keegan-dev`, `brad-dev`) to see at a glance
whose key is whose.
