# Self-Hosting Dojo

This guide covers running your own Dojo instance on your own infrastructure with Docker Compose — from a fresh clone to a domain with TLS in front of it.

If you just want to try Dojo locally, the three commands in the [README Quick Start](./README.md#getting-started) are enough. This guide is for taking that instance further: a real domain, backups, and updates.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/)
- A server (VPS, home server, NAS — anything that can run Docker) if you're deploying outside your own laptop
- Optional: a domain name pointed at that server, if you want Dojo reachable somewhere other than `localhost`

No Python, MySQL, or Node install is required on the host — everything runs inside containers.

---

## Quick start

```bash
git clone https://github.com/DojoUK/dojo.git
cd dojo
docker compose up -d
```

That's it. No `.env` file is required to get running:

- `SECRET_KEY` generates itself on first boot and is persisted to a local `.secret_key` file (already gitignored), so it survives container restarts without you setting anything.
- The database name, user, and passwords default to `dojo` / `dojo_user` / `dojo_password` (see `docker-compose.yml`) — fine for a single self-hosted instance, but override them (see [Environment variables](#environment-variables) below) if you want your own.
- `entrypoint.sh` waits for MySQL to accept connections, then runs `python manage.py migrate` automatically before the server starts. You never need to run migrations by hand.

Open **http://localhost:8000/** (or your server's address). Since no organisation exists yet, you'll land on the **setup wizard** — fill in your organisation name and your own admin username/password, and you're in. There's no separate `createsuperuser` step and no need to go via `/admin/` first; the wizard creates your organisation and admin account together.

Want to explore Dojo with realistic data before setting up your own club? Visit `/setup/?demo=1` instead — it shows a second button that seeds a fictional club ("Mockingham Martial Arts Club") with members, classes, and invoices, and logs you in as `admin` / `admin`. A "Reset demo data" button stays available in the sidebar for any org bootstrapped this way.

**Stopping Dojo:**

```bash
docker compose down
```

Your data (MySQL volume and anything under `media/`) is untouched by `down` — only `down -v` would remove the database volume.

---

## Environment variables

Nothing here is required for a basic instance — everything has a sane default. Set what's relevant to you in a `.env` file at the project root (`cp .env.example .env` as a starting point); Compose reads it automatically.

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | auto-generated, persisted to `.secret_key` | Only set this yourself if you need a specific key (e.g. restoring onto a new host without copying `.secret_key` across) |
| `DEBUG` | `True` | See [Static files and DEBUG](#static-files-and-debug) below before turning this off |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_ROOT_PASSWORD` | `dojo` / `dojo_user` / `dojo_password` / `dojo_root_password` | Change these for anything beyond local/trusted use |
| `ALLOWED_HOSTS` | `*` (all hosts accepted) | Comma-separated list, e.g. `dojo.yourclub.com`. Tightening this is optional today — the codebase notes a browser-configurable allow-list is planned to replace setting it via the environment |
| `SITE_URL` | `http://localhost:8000` | Used to build links in emails and Stripe redirects. **Also controls security headers** — see below |
| `EMAIL_*` | console backend | **Fallback only.** Each club chooses how its email is sent in the setup wizard or **Settings → Email** (see [Sending email](#sending-email)). These variables are used only by clubs that haven't chosen yet. Leave them unset to print that mail to `docker compose logs -f web` |
| `MAIL_HOSTNAME` | `localhost` | For Dojo's own mail service: the name the `mail` container introduces itself as, e.g. `mail.yourclub.org.uk`. Set it, and your server's reverse DNS, to a name that points at your server |
| `DOJO_MAIL_HOST` / `DOJO_MAIL_PORT` | `mail` / `25` | Where Dojo finds its own mail service. Only change these if you run your own relay instead of the bundled container |
| `STRIPE_PUBLIC_KEY` / `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | unset | Only needed if you want online payments; get these from your Stripe dashboard |
| `DOJO_BUILD_REPO` / `DOJO_BUILD_COMMIT` / `DOJO_BUILD_TAG` | unset (read from `.git`) | The version shown under Settings → General. Dojo reads it from the `.git` folder; set these only if your image is built without one. A repository other than `DojoUK/Dojo` shows as "Unofficial Dojo fork" |
| `DOJO_LICENCE_KEY` / `DOJO_LICENCE_HOLDER` | unset | Self-hosters on the default AGPL-3.0 licence leave both blank |

### `SITE_URL` and security headers

Secure cookies, CSRF, HSTS, and the HTTPS redirect are all keyed off whether `SITE_URL` starts with `https://` — **not** off `DEBUG`. This is deliberate: a lot of self-hosted instances run behind a reverse proxy on plain HTTP internally, and forcing HTTPS redirects in that setup would break them. Once you have TLS terminated in front of your instance (see below), set `SITE_URL=https://yourdomain.com` and those protections switch on automatically.

### Static files and `DEBUG`

Leave `DEBUG=True` for now. With it on, Django serves `/static/` (Bootstrap, HTMX, the app's own CSS/JS — all vendored locally, no CDN calls) itself via the dev server. There's no `STATIC_ROOT`/`collectstatic` step wired into the Docker image yet, so setting `DEBUG=False` currently means static assets stop being served. This is a known gap, not something to work around per-instance — track/pick up [DojoUK/Dojo#26](https://github.com/DojoUK/Dojo/issues/26) or file a new issue if you want to help close it. In the meantime, `DEBUG=True` + an `https://` `SITE_URL` still gets you the full set of security headers above; you're not choosing between "secure" and "static files work."

---

## Running behind a reverse proxy

Compose exposes the app on port `8000` on the host. Put a reverse proxy in front of it to get a real domain and TLS.

### Caddy (automatic HTTPS)

Simplest option if you can point a domain at the server. `Caddyfile`:

```
dojo.yourclub.com {
    reverse_proxy localhost:8000
}
```

Caddy handles the Let's Encrypt certificate and renewal for you.

### nginx + Certbot

```nginx
server {
    listen 80;
    server_name dojo.yourclub.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then run [Certbot](https://certbot.eff.org/) against that server block to get HTTPS and have nginx redirect port 80 → 443 for you.

Either way, once TLS is live, set `SITE_URL=https://dojo.yourclub.com` in your `.env` and restart (`docker compose up -d`).

---

## Updating Dojo

```bash
git pull
docker compose up -d --build
```

Migrations run automatically on container start via `entrypoint.sh` — you don't need a separate migrate step after pulling.

---

## Backups

Two things to back up:

1. **The database.** `mysql_data` is a named Docker volume. Either back up the volume directly, or take a logical dump you can restore from:

   ```bash
   docker compose exec db mysqldump -u root -p"$DB_ROOT_PASSWORD" dojo > dojo-backup-$(date +%F).sql
   ```

2. **The `media/` directory.** Uploaded member documents, signed waivers, and organisation logos live under `media/` in the project directory (bind-mounted into the container, so they're already on the host — just include the folder in whatever you use for filesystem backups).

`.secret_key` is also worth keeping alongside your backups if you ever plan to restore onto a different host — without it, a restored instance would generate a new `SECRET_KEY`, which invalidates existing sessions and signed tokens (including member portal links), and makes staff's two-step sign-in recovery codes stop working.

---

## Sending email

Choose how Dojo sends email (welcome emails, invoices, reminders, announcements) in the setup wizard, and change it any time under **Settings → Email**. Use **Send test email** there to check it works.

| Option | Good for |
|---|---|
| **Dojo's own mail service** | Sending straight from your server, free. Needs outgoing port 25 open (many hosts block it until you ask) and a few DNS records, which Settings → Email lists and checks for you |
| **Brevo**, **Mailgun**, **Postmark**, **Resend** | Sending services with web APIs. They work even where your host blocks email ports |
| **Amazon SES** | Cheap at volume. Uses SES SMTP credentials |
| **Gmail / Google Workspace**, **Microsoft 365** | Sending from a club mailbox. Google and Microsoft limit how many emails a day they'll send |
| **Another SMTP server** | Anything else: your host's mail server, IONOS, Fastmail, Zoho, SendGrid |
| **Not yet** | Emails are printed to `docker compose logs -f web` instead of being sent |

Passwords and API keys are stored encrypted with a key derived from `SECRET_KEY`. If `SECRET_KEY` changes (for example, restoring without `.secret_key`), re-enter them in Settings → Email.

**Dojo's own mail service** is the `mail` container (Postfix). Dojo signs each email with your domain's DKIM key and hands it over; Postfix queues it and delivers it, retrying if a receiving server is busy. No ports are opened to the internet. For good delivery:

1. Ask your host to allow outgoing port 25 if they block it.
2. Set `MAIL_HOSTNAME` in `.env` to a name that points at your server (e.g. `mail.yourclub.org.uk`), and ask your host to set your server's reverse DNS to the same name.
3. Add the DKIM, SPF and DMARC records shown under Settings → Email, then press **Check DNS**.

To see what the mail service is doing: `docker compose logs -f mail`.

---

## Two-step sign-in

Staff can protect their accounts with a code from an authenticator app (Google Authenticator, Microsoft Authenticator, a password manager). Each club chooses in **Settings → Security** whether it's required for all staff, admins only, or optional. New clubs start with it required; clubs that existed before the feature start optional.

A club admin can reset another person's two-step sign-in from the **Staff** page (for a new or lost phone). If the only admin has lost both their phone and their recovery codes, reset it from the server:

```bash
docker compose exec web python manage.py reset_two_factor <username>
```

If their club requires two-step sign-in, they're asked to set it up again the next time they sign in.

---

## Troubleshooting

- **Stuck on "waiting for database"** — `entrypoint.sh` retries for up to 60 seconds. If it still times out, check `docker compose logs db` for a MySQL startup failure (bad `DB_ROOT_PASSWORD` on an existing volume is a common cause after changing `.env`).
- **Redirected to `/setup/` after you already set up an organisation** — this only happens when no `Organisation` row exists in the database. If you're seeing it unexpectedly, confirm you're pointed at the same `mysql_data` volume you set up against originally.
- **Emails not arriving** — by default `EMAIL_BACKEND` is the console backend, which prints emails to `docker compose logs -f web` instead of sending them. Configure the SMTP variables to send for real.
