# Installing OCS-in-a-Kit

OCS-in-a-Kit gives your school or organization a complete coding platform: a website (lessons,
guides, games, profiles, an admin dashboard) plus two backend servers that store accounts and data.
The installer sets everything up from one wizard. Plan on about 30 minutes for a first try on your
own computer, and about an hour to go live on a server with your own domain.

## How the pieces fit

| Part | What it does | Where it runs |
|---|---|---|
| **Website** ([OCS-Kit-Pages](https://github.com/CSA-Admin-OCS/OCS-Kit-Pages)) | The pages people open: lessons, guides, profile, admin dashboard | GitHub Pages (free), or a preview on your computer |
| **Flask backend** ([OCS-Kit-Flask](https://github.com/CSA-Admin-OCS/OCS-Kit-Flask)) | Python API: logins, sections, AI chatbot proxy, code runner | Docker on your server, port 8587 |
| **Spring backend** ([OCS-Kit-Spring](https://github.com/CSA-Admin-OCS/OCS-Kit-Spring)) | Java API: people, groups, assignments, calendar, admin pages | Docker on your server, port 8585 |

The website is static, so GitHub hosts it. The backends hold your data, so they run on a machine
you control. The installer handles both, and you can install them on different machines.

## 1. Choose a kit

| Kit | For | Roles | Features on by default |
|---|---|---|---|
| **Vanilla** | A simple site you can hand to a student | Admin, Editor, User | Core: accounts, profile, blog and notebooks |
| **Education** | A CS classroom | Admin, Teacher, Student | Core, lessons (Python, Java, guides), code runners, assignments and grading, game engine, AI chatbot, AI grading, calendar, analytics, hall pass, social, resume |
| **Business** | A company or organization serving its own users | Admin, Manager, Member | Core, AI chatbot, social, calendar, analytics, resume |

You are not locked in: any kit can add or remove features during install, and Admins can turn
features on and off later from the website's admin dashboard. Educational features (lessons,
grading, hall pass) are only in Education by default.

## 2. What you need

| | For the backends | For publishing the website | For a local website preview |
|---|---|---|---|
| Software | Python 3.8+, Git, Docker (running) | Python 3.8+, Git, [GitHub CLI](https://cli.github.com/) signed in with `gh auth login` | Python 3.8+, Git, Docker |
| Machine | 2 CPU, 4 GB RAM, 20 GB disk | any | any |

- **Windows:** install [Python](https://www.python.org/downloads/) (tick "Add to PATH"),
  [Git](https://git-scm.com/downloads) and [Docker Desktop](https://docs.docker.com/get-docker/).
- **macOS:** run `xcode-select --install`, install [Docker Desktop](https://docs.docker.com/get-docker/), then `brew install gh`.
- **Linux:** `sudo apt install git python3`, install Docker with `curl -fsSL https://get.docker.com | sh`,
  then `sudo usermod -aG docker $USER` and log out and back in.

Run the installer as your normal user, never with `sudo`; it refuses to run as root.

## 3. Run the installer

```bash
git clone https://github.com/CSA-Admin-OCS/OCS-Kit.git
cd OCS-Kit
./install.sh        # macOS and Linux
install.bat         # Windows
```

A page opens in your browser and walks you through:

1. **Kit**: pick Vanilla, Education or Business, and optionally customize the features.
2. **Install**: backend, website or both, and either *Try it on this computer* or *Production server with a domain*.
3. **School**: your school or organization name, the admin email and the install folder.
4. **Accounts**: passwords for the Admin and staff accounts. Leave them blank to have strong ones generated.
   Passwords need 8+ characters with an uppercase letter, a lowercase letter, a digit and one of `~!@#%^&*()`.
5. **Features**: the settings each feature needs, such as a Gemini API key for the AI chatbot.
   Everything here is optional; you can add keys later.
6. **Website**: publish to GitHub Pages and/or run a local preview.
7. **Check**: the installer tests your computer and tells you how to fix anything missing.
8. **Install**: a live log while it downloads, configures and starts everything.

On a server without a screen, the installer asks the same questions in the terminal. Scripted
installs use an answers file. See the [installer README](../README.md) for every option.

## 4. After installing

The final page lists your links and accounts. They are also saved in your install folder
(default `~/ocs-kit`):

- `CREDENTIALS.txt`: account names and passwords. Only you can read it; store the passwords in
  a password manager and change the Admin password after your first login.
- `FEATURE-SETUP.md`: every enabled feature, whether it is ready, and the steps to finish setting it up.

**Try it:**

1. Open the website (local preview: <http://localhost:4500>) and log in as `admin`.
2. Open **Admin** in the menu. The dashboard shows your kit, your users by role and which
   features still need setup.
3. Open **Manage features** to turn features on or off. Each feature has a **Setup guide** that
   lists the settings it needs, where each one goes, a link to get it, and how to check it works.

**Who can do what:**

| Role (Education / Business / Vanilla) | Can |
|---|---|
| Admin | Everything, including turning features on and off and managing users and roles |
| Teacher / Manager / Editor | Day-to-day work: content, groups, grading, approvals. Sees which features are on but cannot change them |
| Student / Member / User | Uses the site. New sign-ups get this role |

**Adding an API key or setting later:** add the line from the setup guide to `flask/.env` or
`spring/.env` in your install folder, then restart that backend with `docker compose up -d` in
that folder. Website settings go in `_config.yml` of your website repository.

## 5. Going live with your domain

Choose *Production server with a domain* in the installer. It uses three subdomains:
`pages.<domain>` for the website, `flask.<domain>` and `spring.<domain>` for the backends.
Keeping all three on one domain matters: browsers block login cookies between unrelated domains.

1. **DNS:** at your domain registrar, create the records from the installer's final page:
   `A` records for `flask` and `spring` pointing at your server's IP, and a `CNAME` for `pages`
   pointing at `<github-account>.github.io`.
2. **HTTPS:** the installer writes nginx configs to `<install>/nginx/` and prints the exact commands
   to install them and get free certificates with certbot. Run those commands with `sudo`.
3. **Firewall:** open ports 80 and 443 only. Don't expose 8585, 8587, 8589 or 8500 directly.
4. **Website:** in your website repository on GitHub, under **Settings → Pages**, confirm the custom
   domain `pages.<domain>` and tick **Enforce HTTPS** once it's offered.

## 6. Updates and backups

- **Update:** re-run the installer and choose the new version. It keeps your secrets, passwords
  and data. To only change the kit or features: `./install.sh --change-features`.
- **Website updates:** your website is a fork, so GitHub shows a **Sync fork** button when the kit
  releases changes. Your own lessons and posts are kept.
- **Back up** `flask/instance/`, `spring/volumes/` and both `.env` files regularly. Stop the
  backends first (`docker compose stop` in each folder) so the databases are copied consistently.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| The installer says it refuses to run as root | Run it as your normal user. On Linux, add yourself to the `docker` group (step 2) |
| "Docker is not running" | Start Docker Desktop (Windows/macOS) or `sudo systemctl start docker` (Linux) |
| A password is rejected | Use 8+ characters with an uppercase letter, a lowercase letter, a digit and one of `~!@#%^&*()` (not `$`) |
| A backend doesn't start | In its folder run `docker compose logs --tail 50 web`; the error names the missing setting |
| Login works but you're logged out on the next page | Use HTTPS everywhere and keep all three parts on subdomains of one domain |
| AI chatbot says "AI assistant not configured" | Add `GEMINI_API_KEY` to `flask/.env` and restart Flask (see the feature's setup guide) |
| A feature's page is missing on the website | Its pages aren't in your website build. Add it to `kit: features:` in `_config.yml` and push, or re-run the installer |
| Port 4500, 8585 or 8587 is already in use | Stop the other program, or re-run with `--preview-port` / `--port-offset` |
