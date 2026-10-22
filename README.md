# OCS-in-a-Kit installer

One script that installs **OCS-in-a-Kit** on your computer or server:

| Part | Repository | What it is | Where it runs |
|---|---|---|---|
| Flask backend | [OCS-Kit-Flask](https://github.com/CSA-Admin-OCS/OCS-Kit-Flask) | Python API, admin pages, socket server, code runner | Docker on this computer, ports 8587 (+8500) |
| Spring backend | [OCS-Kit-Spring](https://github.com/CSA-Admin-OCS/OCS-Kit-Spring) | Java API, admin pages, code runner | Docker on this computer, ports 8585 (+8589) |
| Website | [OCS-Kit-Pages](https://github.com/CSA-Admin-OCS/OCS-Kit-Pages) | Jekyll site students and staff open | GitHub Pages, and/or a local preview on port 4500 |

The installer needs only Python 3.8+ (no extra packages). It asks a few questions in a web page
(or in the terminal), then downloads, configures and starts everything.

## Backend vs. website: what gets installed where

- **Backend server** (Flask + Spring) stores accounts and data, so it has to run somewhere
  permanently. The installer clones both repositories into your install folder, writes their
  `.env` files (secrets, passwords, school name, kit, CORS) and starts them with
  `docker compose up -d --build`. In production you put nginx in front of them; the installer
  writes ready-to-use nginx configs and tells you the exact `sudo`/`certbot` commands.
- **Website** (Pages) is a static site. It does **not** need your server: GitHub hosts it for free.
  The installer can **publish** it with the GitHub CLI (`gh`): it forks OCS-Kit-Pages into your
  account or organization, writes `_config.yml`, turns on Actions and GitHub Pages (with the custom
  domain `pages.<your-domain>` in production) and starts the first build. It can also run a
  **local preview** on `http://localhost:4500` inside Docker, so you don't need Ruby.
- A site published on GitHub Pages can only talk to **production** backends (https). In
  "Try it on this computer" mode, use the local preview to log in to your localhost backends.

## Requirements

| | Backend | Website on GitHub Pages | Local website preview |
|---|---|---|---|
| All | Python 3.8+, Git | Python 3.8+, Git | Python 3.8+, Git |
| | Docker with Compose, running | [GitHub CLI](https://cli.github.com/) signed in (`gh auth login`) | Docker (recommended), or Ruby + Bundler + make + Python 3 |

- **Windows**: install [Python](https://www.python.org/downloads/) (tick "Add to PATH"),
  [Git](https://git-scm.com/downloads) and [Docker Desktop](https://docs.docker.com/get-docker/) (WSL 2).
- **macOS**: `xcode-select --install` (Git + Python), [Docker Desktop](https://docs.docker.com/get-docker/),
  `brew install gh`.
- **Linux**: `sudo apt install git python3`, Docker via `curl -fsSL https://get.docker.com | sh` and
  `sudo usermod -aG docker $USER`, `gh` from <https://cli.github.com/>.

The installer's **Check** page tests all of this and shows how to fix anything missing.

## Run it

```bash
git clone https://github.com/CSA-Admin-OCS/OCS-Kit.git
cd OCS-Kit
./install.sh             # macOS / Linux
install.bat              # Windows (or: powershell -ExecutionPolicy Bypass -File install.ps1)
```

### Web-page wizard (default)

The installer starts a small web server on `127.0.0.1` (random port) and opens it in your browser.
The link contains a one-time token; requests without it are refused. Pages:

1. **Kit**: Vanilla, Education or Business. Every feature of the catalog is listed by category,
   with labels such as `needs-api-key` or `privacy-sensitive`. Switch on **Customize** to add or
   remove features; a live summary shows e.g. "OCS Education + Finance simulation − Hall pass".
2. **Install**: backend, website or both; "Try it on this computer" or "Production server with a domain".
3. **School**: name, admin email, install folder (default `~/ocs-kit`), domain (production).
4. **Accounts**: passwords for the kit's roles (e.g. Admin/Teacher/Student, Admin/Manager/Member).
   Blank = auto-generate. Spring's rules apply: 8+ characters, upper, lower, digit and one of `~!@#%^&*()`.
5. **Features**: the settings each enabled feature needs (API keys etc.), with help and "get it" links.
   All optional; missing required ones are listed at the end.
6. **Website**: GitHub Pages (owner + repository name) and/or local preview.
7. **Check**: prerequisites with fixes per OS.
8. **Install**: live log and step checklist.
9. **Done**: URLs, accounts, per-feature "ready / needs setup" list, DNS records and nginx commands
   (production), and how to stop/start/update.

No display (e.g. an SSH session on a server)? The installer falls back to the terminal wizard.
To use the web page anyway, run with `--gui --no-browser` and forward the port it prints:
`ssh -L <port>:127.0.0.1:<port> you@server`, then open the printed link on your computer.

### Terminal wizard

```bash
./install.sh --cli
```

Same questions, with defaults in `[brackets]`; passwords are typed hidden.

### Unattended

```bash
OCS_ADMIN_PASSWORD='Admin#2024ok' OCS_SETTING_GEMINI_API_KEY=... ./install.sh --answers answers.json
```

`answers.json` (every key optional except `school_name` and `admin_email`):

```json
{
  "kit": "education",
  "features": ["+finance-sim", "-hallpass"],
  "install_backend": true,
  "install_website": true,
  "mode": "local",
  "school_name": "Example High School",
  "admin_email": "cs-admin@example.org",
  "install_dir": "~/ocs-kit",
  "domain": "",
  "admin_password": "",
  "teacher_password": "",
  "default_password": "",
  "settings": {"kit.voicerss_api_key": ""},
  "website_preview": true,
  "website_publish": false,
  "github_owner": "",
  "github_repo": "ocs-kit-pages",
  "preview_port": 4500
}
```

- Empty passwords are generated (or kept on a re-run). They can also come from
  `OCS_ADMIN_PASSWORD`, `OCS_TEACHER_PASSWORD`, `OCS_DEFAULT_PASSWORD`.
- `settings` holds feature settings from the catalog, by `KEY` or `service:KEY`
  (`flask:GEMINI_API_KEY`). Each can also come from `OCS_SETTING_<KEY>`
  (`kit.voicerss_api_key` -> `OCS_SETTING_KIT_VOICERSS_API_KEY`).
- `mode: "production"` needs `domain` (e.g. `myschool.org`): backends become
  `flask.<domain>` and `spring.<domain>`, the website `pages.<domain>`.

## Kits and features

All kits, features, categories, labels, roles and per-feature setup instructions come from
[`kits/catalog.json`](kits/catalog.json). Add a feature there and it appears in the installer.

```bash
./install.sh --list-features
./install.sh --kit business --features +code-runner,-social      # overrides relative to the kit
```

The backends get `KIT=<kit>` and `FEATURES=+x,-y` in their `.env`; the website gets
`kit: name:` and `kit: features: ["+x", "-y"]` in `_config.yml`. After installing, Admins can
change features from the website's admin dashboard.

## Re-running: update, change kit

Answers (without passwords or secret keys) are saved to `<install>/ocs-kit-installer.json`, so a
re-run pre-fills everything:

- **Update / reinstall**: fetches the chosen version (`--ref`), keeps `.env` secrets
  (`SECRET_KEY`, `JWT_SECRET`, `RESET_TOKEN_SECRET`), passwords (unless you type new ones) and data.
- **Change kit / features only**: rewrites `KIT`/`FEATURES` (+ any feature settings you type) and
  `_config.yml`, then restarts. No downloads, secrets untouched:

  ```bash
  ./install.sh --change-features --kit education --features +finance-sim,-hallpass
  ```

Seed passwords and account names only apply when a backend creates a new, empty database.

## What ends up in the install folder

```
~/ocs-kit/
  flask/  spring/          backend checkouts with .env (chmod 600) and data (instance/, volumes/)
  pages/                   website checkout for the local preview
  github-site/             clone of your published site (GitHub Pages)
  nginx/flask.conf, spring.conf    production only
  CREDENTIALS.txt          accounts and passwords (chmod 600)
  FEATURE-SETUP.md         setup guide and status for every enabled feature
  ocs-kit-installer.json   saved answers (no secrets)
  install.log
```

Stop / start: `docker compose stop` / `docker compose up -d` in `flask/` and `spring/`;
the preview runs in the `ocskit-pages-preview` container (`docker stop|start ocskit-pages-preview`).

## Options

| Option | Meaning |
|---|---|
| `--cli`, `--gui`, `--answers FILE` | terminal wizard, force the web page, unattended |
| `--install-dir DIR` | install folder (default `~/ocs-kit`) |
| `--kit ID`, `--features SPEC` | kit and feature overrides (`+id,-id`, an explicit list, or `*`) |
| `--change-features` | only change kit/features of an existing install |
| `--list-features` | print kits, features, categories and labels |
| `--ref REF` | tag or branch (default the kit release; `main` = each repo's default branch) |
| `--repo-base URL-or-DIR` | where the three repositories come from (for testing forks or local copies) |
| `--preview-port N` | local website preview port (default 4500) |
| `--dry-run` | print every command and file write without doing anything |
| `--no-browser`, `--port N` | web page: don't open a browser / fixed port |

## Development

```bash
python3 -m unittest discover -s tests -v
python3 ocs_kit_installer.py --dry-run --answers answers.json   # see exactly what would run
```
