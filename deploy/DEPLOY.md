# Deploying OCS-in-a-Kit from Docker Hub

Images live in the single repo `wolfjack20/ocs-in-kit`, as `flask-<version>`, `spring-<version>`,
`flask-runner-<version>`, `spring-runner-<version>`, `socket-<version>`, each also `-latest`.
The domain `example.org` is a placeholder: **change it before going live**. To run next to
another OCS on one server, see [Deploying on cockpit](#deploying-on-cockpit).

## 1. Build and push (on your machine)

    cd OCS-Kit/deploy
    ./build-images.sh 1.0.0              # prints the docker login + docker push commands; run them
    # or, multi-arch and pushing directly:
    docker login -u wolfjack20
    ./build-images.sh 1.0.0 --platform linux/amd64,linux/arm64 --push

Automated alternative: `workflows/publish-images.flask.yml` and `.spring.yml` build on `v*` tags. Copy
each to its repo's `.github/workflows/publish-images.yml` and add the secrets `DOCKERHUB_USERNAME`
and `DOCKERHUB_TOKEN`.

## 2. Copy to the server

Needed: `docker-compose.yml`, `setup.py`, `templates/`, `profiles/`, `DEPLOY.md`. Python 3.8+, Docker
and Compose v2 are the only requirements. Not needed there: `build-images.sh`, `workflows/`.

    scp -r deploy myserver:~/ocs-kit-deploy

## 3. Generate the configuration

As a normal user in the docker group (setup refuses root):

    cd ~/ocs-kit-deploy
    ./setup.py --domain myschool.org --kit education --admin-email it@myschool.org --image-tag 1.0.0
    ./setup.py --check-ports

This writes `.env`, `flask.env`, `spring.env`, `CREDENTIALS.txt` (all mode 600), `out/nginx/*.conf`,
`out/next-steps.txt` and the `data/` folders. Re-running keeps every secret and password and your
edits to the env files, and rewrites what depends on the options. So a new domain is just
`./setup.py --domain newname.org`. Then run `docker compose up -d` and redo nginx and certbot.

The containers run as your user, never as root. If `data/` holds files you do not own, setup stops and
prints the one-time `docker run --rm -v <dir>:/d alpine chown -R <uid>:<gid> /d` to run yourself.

To add API keys, edit `flask.env` or `spring.env`, then `docker compose up -d`.

## 4. DNS

A records to the server: `flask.<domain>`, `spring.<domain>`, `socket.<domain>` (only if you enable the
optional socket site). `pages.<domain>` points at wherever the website is hosted. If that is another
URL, such as GitHub Pages, pass `--pages-url https://org.github.io/site`, so CORS and `FRONTEND_URL` follow.

## 5. Start

    docker compose pull && docker compose up -d
    docker compose ps            # wait for "healthy"; first boot seeds the databases (1-3 minutes)

## 6. nginx and certbot

`out/next-steps.txt` holds these commands with your hostnames filled in:

    sudo cp out/nginx/ocs-kit-flask.conf out/nginx/ocs-kit-spring.conf /etc/nginx/sites-available/
    sudo ln -sf /etc/nginx/sites-available/ocs-kit-flask.conf /etc/nginx/sites-enabled/
    sudo ln -sf /etc/nginx/sites-available/ocs-kit-spring.conf /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo certbot --nginx -d flask.myschool.org -d spring.myschool.org

Flask's site already routes `/socket.io/` to the socket service; `ocs-kit-socket.conf` is optional.

## 7. Firewall

Allow only 80 and 443 from outside (plus your SSH port). Compose binds everything to 127.0.0.1.
On ufw: `sudo ufw allow 80,443/tcp && sudo ufw allow OpenSSH && sudo ufw enable`

## 8. Verify

    curl -s https://flask.myschool.org/api/kit
    curl -s https://spring.myschool.org/api/kit

Each reports your kit. Admin login (password in `CREDENTIALS.txt`) must answer 200 with a `Set-Cookie`:

    curl -si https://flask.myschool.org/api/authenticate -H 'Content-Type: application/json' \
      -d '{"uid":"admin","password":"..."}' | head -20
    curl -si https://spring.myschool.org/authenticate -H 'Content-Type: application/json' \
      -d '{"uid":"admin","password":"..."}' | head -20

The first boot also seeds sample accounts with the default password, and on Spring an extra admin,
`myuser`. Sign in as admin and delete them or change their passwords before real use.

## 9. Point the website at the backends

In the website's `_config.yml` (snippet in `out/next-steps.txt`), then rebuild it:

    kit:
      backend:
        flask: "https://flask.myschool.org"
        spring: "https://spring.myschool.org"

## Test the feature library

Log in as admin on the website, open **Admin -> Manage features**, and in turn: apply **Vanilla**,
customize a feature on or off, apply **Education**, apply **Business**. After each, check that the site's
menus and `GET /api/kit` change on both backends.

The same with curl (repeat for Spring, with `/authenticate` and its cookie):

    B=https://flask.myschool.org
    C=$(curl -si $B/api/authenticate -H 'Content-Type: application/json' \
          -d '{"uid":"admin","password":"..."}' | grep -i '^set-cookie: .*jwt' | sed 's/^[^:]*: //; s/;.*//')
    curl -s -H "Cookie: $C" -H 'Content-Type: application/json' -X PUT $B/api/kit/admin/kit -d '{"kit":"vanilla"}'
    curl -s -H "Cookie: $C" -H 'Content-Type: application/json' -X PUT $B/api/kit/admin/features -d '{"features":{"hallpass":true}}'
    curl -s -H "Cookie: $C" -H 'Content-Type: application/json' -X PUT $B/api/kit/admin/kit -d '{"kit":"education"}'
    curl -s -H "Cookie: $C" -H 'Content-Type: application/json' -X PUT $B/api/kit/admin/kit -d '{"kit":"business"}'
    curl -s $B/api/kit

## Backups

All state is in `./data` plus the generated secrets. With the stack stopped (or accepting a SQLite
snapshot taken live):

    docker compose stop flask spring
    tar czf ~/ocs-kit-backup-$(date +%F).tgz data .env flask.env spring.env CREDENTIALS.txt
    docker compose start flask spring

Keep the archive private: it holds passwords and password hashes. To restore, unpack it into the folder and
`docker compose up -d`.

## Update

Back up first, then:

    ./setup.py --image-tag 1.1.0
    docker compose pull && docker compose up -d

## Rollback

Same with the previous version: `./setup.py --image-tag 1.0.0 && docker compose pull && docker compose up -d`.
If the new version changed the database, stop the stack and restore `data/` from the backup taken before the update.

## Deploying on cockpit

Cockpit runs production OCS from its own directories, on 8587, 8585, 8589 and 8500. This profile is a
separate stack that shares nothing with it:

| | production | this stack |
|---|---|---|
| Folder | its own | `~/ocs-kit-deploy` |
| Compose project | its own | `ocs-kit` (containers `ocs-kit-*`, networks `ocs-kit_*`) |
| Images | its own | `wolfjack20/ocs-in-kit:*`, pulled from Docker Hub |
| Host ports (127.0.0.1) | 8587, 8585, 8589, 8500 | flask 8687, spring 8685, websocket 8689, socket 8600 |
| Hostnames | its own | `kit-flask.<domain>`, `kit-spring.<domain>`, `kit-pages.<domain>`, `kit-socket.<domain>` |
| nginx files | its own | `ocs-kit-*.conf` |
| Login cookies | its own | `kit_jwt_python_flask`, `kit_sess_python_flask`, `kit_jwt_java_spring`, `kit_sess_java_spring`, host-only |

Nothing here reads or writes the production directories. Every command below runs in `~/ocs-kit-deploy`.

**Warning: `docker compose` acts on the project of the folder you are in. Run any `docker compose down`
only in `~/ocs-kit-deploy`; in a production folder it would stop production.**

From `OCS-Kit/` on your machine, bootstrap in one line (the folder must not exist yet):

    scp -r deploy cockpit:~/ocs-kit-deploy && ssh cockpit 'cd ~/ocs-kit-deploy && ./setup.py --profile cockpit --domain YOURDOMAIN --image-tag 1.0.0 && ./setup.py --check-ports'

`deploy/` should hold no generated files when you copy it (`git status --ignored deploy` shows any); only
the list in step 2 is needed.

`--check-ports` lists everything listening and any compose project already using the name. It
exits 1 on a conflict. Any port can be changed, for example `./setup.py --flask-port 8787` (also
`--spring-port`, `--spring-ws-port`, `--socket-port`), and nginx follows. Then:

    cd ~/ocs-kit-deploy
    docker compose pull && docker compose up -d
    docker compose ps

nginx: test before every reload, so a mistake cannot take the production sites down. Check that no
existing site uses these hostnames (`sudo nginx -T | grep server_name`):

    sudo cp out/nginx/ocs-kit-flask.conf out/nginx/ocs-kit-spring.conf /etc/nginx/sites-available/
    sudo ln -s /etc/nginx/sites-available/ocs-kit-flask.conf /etc/nginx/sites-enabled/
    sudo ln -s /etc/nginx/sites-available/ocs-kit-spring.conf /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx
    sudo certbot --nginx --cert-name ocs-kit -d kit-flask.YOURDOMAIN -d kit-spring.YOURDOMAIN

Website: `kit.backend.flask` = `https://kit-flask.YOURDOMAIN`, `kit.backend.spring` =
`https://kit-spring.YOURDOMAIN`. If the site is not at `https://kit-pages.YOURDOMAIN`, pass `--pages-url`.

Cookies are host-only, so they cannot reach production whatever the parent domain. To share them across
`kit-*` hosts anyway, use `./setup.py --cookie-domain .YOURDOMAIN`; the `kit_*` names still keep them apart.

Stop or remove only this stack:

    cd ~/ocs-kit-deploy
    docker compose stop                  # stop; data stays
    docker compose down                  # remove its containers and networks; data stays
    sudo rm /etc/nginx/sites-enabled/ocs-kit-*.conf && sudo nginx -t && sudo systemctl reload nginx

Never use `docker system prune`, `docker compose down -v` or `docker stop $(docker ps -q)` on this server.
