# Dojo - Project Context

This file exists to carry full project context between AI sessions. If you are a new Claude session, read this entire file before doing anything.

---

## What is Dojo?

Dojo is an open source, multi-tenant club and class management platform. It was conceived by the sole developer, who runs a judo club and wanted to replace a collection of Excel spreadsheets and a Wix website with a single, properly built system.

The name Dojo comes from the Japanese word 道場 (dōjō), meaning a martial arts training space. It was chosen because it fits the judo roots of the project, is broadly understood as a place of practice, is not locked to any one sport, and works naturally as a platform name.

The goal is for Dojo to be sport and activity agnostic. It should work equally well for a judo club, a dance school, a boxing gym, a music centre, or any organisation that runs classes with members, coaches, attendance, and billing.

---

## Licence

Dojo is licenced under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This was a deliberate decision. AGPL means:
- Anyone can self-host and modify Dojo freely
- Anyone who runs a modified version as a network service (SaaS) must release their modifications under the same licence
- The original developer, as copyright holder, is not bound by the AGPL in the same way and can run a commercial hosted version (Dojo Cloud) and sell commercial licences to organisations who want to deploy privately without AGPL obligations

This is a dual licensing model, similar to GitLab, MongoDB, and Grafana.

---

## Deployment Model

Dojo is designed to support both:

1. **Self-hosted** - a club or centre deploys their own instance on their own infrastructure. They own their data entirely.
2. **SaaS (Dojo Cloud)** - the developer runs a hosted version that organisations can sign up to. This is a future commercial offering. No domain has been decided yet. Anyone interested in contributing or following the project should contact the developer via GitHub.

---

## Tech Stack

Every decision here was deliberate. Do not suggest changing the stack without good reason.

| Layer | Choice | Reason |
|---|---|---|
| **Framework** | Django (Python) | Developer has prior Django experience. Batteries included - auth, ORM, admin, migrations, file handling all built in. Fast to build with. |
| **Database** | MySQL | Developer preference. Django's ORM abstracts most of the difference from PostgreSQL. |
| **ORM** | Django's built-in ORM | Comes with Django, no separate choice needed. |
| **Frontend** | Django Templates + HTMX | Keeps things simple. No separate JS frontend, no npm build pipeline. HTMX adds interactivity where needed without a full SPA. |
| **Auth** | Django's built-in auth | Handles coach/admin login. Extended with custom permission logic for class-level access control. |
| **Payments** | Stripe + Stripe Connect | Stripe Connect allows each organisation to connect their own Stripe account. The platform can optionally take a fee in SaaS mode. PayPal was considered and rejected - Stripe has a better API, better webhooks, better UK support, and Stripe Connect is far superior to PayPal's equivalent. |
| **File storage** | AWS S3 or Cloudflare R2 | For storing signed health and safety documents per member. Not yet implemented. |
| **E-signatures** | DocuSeal (open source) | For sending, signing, and storing health and safety documents. Not yet implemented. |
| **Containerisation** | Docker + Docker Compose | Project runs in containers from day one. MySQL runs in a container, Django app runs in a container. This ensures self-hosting instructions in the README reflect the actual development environment. |
| **Hosting** | Not yet decided | Railway and Render are candidates. |

---

## Architecture Decisions

### Multi-tenancy

Everything in the database is scoped to an **Organisation**. A coach or admin at one organisation can never see or touch another organisation's data. This is enforced at the model level and must be enforced at the view level on every relevant endpoint.

Each organisation gets its own slug-based URL (e.g. `/org/bath-judo-club/`). In self-hosted mode, the single instance is effectively one organisation. In SaaS mode, multiple organisations share one instance.

### Roles and Permissions

There are four roles in the system:

1. **Super Admin** - platform level, only relevant in SaaS mode. This is the developer/operator.
2. **Org Admin** - full access within their organisation. Equivalent to a head coach or club secretary.
3. **Class Coach** - access only to the specific classes they are assigned to. Cannot view or modify members, attendance, or data for classes they are not assigned to. This was an explicit requirement - a gym might run multiple classes (e.g. judo and boxing) with different coaches, and coaches must be siloed to their own classes.
4. **Member/Parent** - no login. Access via tokenised links only (see below).

### Tokenised Member Portal

There is **no login system for members**. This was a deliberate decision to keep things simple for a small club context (70 members was the original scale). A full login system adds overhead (password resets, forgotten accounts, support burden) for something members might use a handful of times a year.

The portal is called the **Member Portal** — not the "parent portal". Most members are adults managing their own subscriptions. Guardians managing on behalf of a child are a subset, not the primary case.

Instead, the system generates a **secure unique token per member**. A link containing this token is emailed to the member (or their guardian if they are a child). The link gives them access to:
- Their attendance history
- Their invoices
- A Stripe-powered payment page to pay outstanding invoices
- Their signed/unsigned health and safety documents

Stripe already supports this model natively with hosted payment links. The token-based route sits at something like `/p/<token>/`.

### Custom Fields

Member profiles are not hardcoded to any sport. An Org Admin can define **custom fields** for their organisation. For example:
- A judo club adds a "Belt Grade" field (select, with options White, Yellow, Orange, Green, Blue, Brown, Black)
- A dance school adds an "Exam Level" field
- A football club adds a "Position" field

Custom fields are stored as a `CustomField` model per organisation and values are stored as JSON on the member record or in a related `MemberFieldValue` table.

### Custom Progression System

Grading and progression is fully configurable per organisation. An Org Admin defines the stages of their progression system (e.g. White Belt, Yellow Belt, Orange Belt - or Grade 1, Grade 2, Grade 3). Each stage has a name and an order. Member progression is tracked as a separate record linking a member to a stage with a date achieved.

### Billing Model

The original use case was a UK school termly billing model (teaching only during term time, not during holidays). The developer's club is moving to a **monthly billing model** and Dojo should support this. Invoices are generated per member per billing period, tracked in the database, and payments are handled via Stripe. Stripe webhooks automatically update invoice status in the database when a payment is made.

In SaaS mode, each organisation connects their own Stripe account via Stripe Connect. The platform operator can optionally take a percentage fee.

---

## Database Schema

These are the core models. All models below are implemented and migrated **except** Document. See notes on Session — the model exists but needs updating for scheduled sessions.

**Session scheduling design decision (agreed):** Sessions are auto-generated from the class schedule (e.g. every Tuesday and Thursday for Backwell Judo Club). Each session is individually editable and can be cancelled. One-off extra sessions can be added outside the normal schedule. The `Session` model needs `is_cancelled` and `is_extra` boolean fields. The `Class.schedule` field needs to store structured recurrence data (days of week + time), not just free text — implementation TBD.

```
Organisation
- id
- name
- slug
- settings (JSON)
- subscription_tier
- created_at

User (Django's built-in auth user)
- extended with a profile linking to OrganisationMember

OrganisationMember
- user (FK to User)
- organisation (FK to Organisation)
- role (choices: org_admin, coach)

Class
- id
- organisation (FK to Organisation)
- name
- description
- schedule

ClassCoach
- class (FK to Class)
- user (FK to User)
- Links coaches to specific classes they are permitted to manage

ClassMember
- class (FK to Class)
- member (FK to Member)
- Links members to the classes they are enrolled in

Member
- id
- organisation (FK to Organisation)
- name
- date_of_birth
- email
- phone
- emergency_contact_name
- emergency_contact_phone
- is_active
- token (unique, used for tokenised parent portal links)
- joined_date
- custom_field_values (JSON or related table)

Guardian
- id
- member (FK to Member)
- name
- email
- phone
- relationship

CustomField
- id
- organisation (FK to Organisation)
- name
- field_type (text, date, select, boolean)
- options (JSON, for select fields)
- order

ProgressionStage
- id
- organisation (FK to Organisation)
- name
- order

MemberProgression
- id
- member (FK to Member)
- stage (FK to ProgressionStage)
- achieved_date
- notes

Session
- id
- class (FK to Class)
- date
- notes

Attendance
- id
- session (FK to Session)
- member (FK to Member)
- present (boolean)

Invoice
- id
- organisation (FK to Organisation)
- member (FK to Member)
- amount
- period (e.g. "January 2026")
- due_date
- status (choices: unpaid, paid, overdue)
- created_at

Payment
- id
- invoice (FK to Invoice)
- stripe_payment_id
- amount
- paid_at

Document
- id
- organisation (FK to Organisation)
- member (FK to Member)
- type (e.g. health_and_safety, medical)
- signed (boolean)
- signed_at
- file_url (points to S3/R2)

Coach (handled via OrganisationMember and ClassCoach, not a separate model)
```

---

## Project Structure (Current State)

The project has a working Django + MySQL stack running in Docker with a full admin UI.

### Infrastructure

- GitHub repo: public, AGPL-3.0 licenced, at `github.com/DojoUK/Dojo`
- GitHub issues cover the full roadmap
- `Dockerfile` — `python:3.12-slim`, installs MySQL client libs, copies and installs dependencies
- `docker-compose.yml` — two services: `db` (MySQL 8.0) and `web` (Django). MySQL data persisted via named volume.
- `.env` — created locally, not committed. `.env.example` committed with variable names.
- `.gitignore` — covers `.env`, `__pycache__/`, `*.pyc`, `.DS_Store`, `venv/`, `.idea/`, `staticfiles/`
- `requirements.txt` — `django`, `mysqlclient`, `python-dotenv`, `django-htmx`, `django-auditlog`
- `docker compose up -d` to start. App at `http://localhost:8000`. Superuser: `admin` / `admin`.

### settings.py

Fully configured:
- `python-dotenv` loads `.env`
- `SECRET_KEY`, `DEBUG`, and all DB credentials read from environment variables
- MySQL database backend (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`)
- `ALLOWED_HOSTS = ['*']` for development
- `LANGUAGE_CODE = 'en-gb'`, `TIME_ZONE = 'Europe/London'`
- `DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'`
- `django_htmx` and `auditlog` in `INSTALLED_APPS`
- `HtmxMiddleware` and `AuditlogMiddleware` in `MIDDLEWARE`

### Django Apps and Models

All apps are in `INSTALLED_APPS`. All models are migrated.

| App | Models | Migrated | Views built |
|---|---|---|---|
| `organisations` | Organisation, OrganisationMember, Announcement | ✅ | ✅ Dashboard, staff management (add/edit/remove/qualifications), audit log, custom fields, announcements, calendar, financial report, org settings + theme |
| `members` | Member, Guardian, CustomField, MemberApplication, MemberNote | ✅ | ✅ List (HTMX search + filters), add, detail, edit, archive, bulk actions (email/invoice/archive), CSV import/export, notes, applications queue (approve/reject), welcome email |
| `classes` | Class, ClassCoach, ClassMember, WaitingList, Session, Attendance | ✅ | ✅ List, add, detail, edit, enrol/unenrol, waiting list, session generation, attendance register, print register, cancel sessions, coach views (siloed), attendance analytics |
| `progression` | ProgressionSystem, ProgressionStage, MemberProgression | ✅ | ✅ System/stage CRUD, reorder stages, set default, bulk apply, record promotions, delete records, CSV import |
| `billing` | Invoice, Payment | ✅ | ✅ Invoice list/create/detail, mark paid/unpaid, record payment, bulk billing run, send invoice email, send reminder, chase overdue, CSV export, Stripe Checkout + Subscriptions + webhook handler |
| `documents` | Document, WaiverTemplate, SignedWaiver | ✅ | ✅ Document upload/download/delete per member, waiver template management, signed waiver download, offline waiver upload |

### Permission Layer

`dojo/mixins.py` contains:
- `OrgMixin` — login required, resolves `self.org` and `self.org_membership` from URL `org_slug`
- `OrgAdminMixin` — extends OrgMixin, enforces org_admin role (superusers bypass)
- `ClassCoachMixin` — extends OrgMixin, enforces coach assignment to specific class; admins bypass the restriction

Public/unauthenticated routes:
- `/join/<org_slug>/` — public member signup page (no login)
- `/p/<token>/` — tokenised member portal (no login)

### URL Structure

```
/                              → root_redirect (sends to org dashboard)
/admin/                        → Django admin
/login/, /logout/, /password-reset/
/join/<slug>/                  → public membership signup (no login)
/p/<token>/                    → member portal (no login)
/p/<token>/checkout/<inv_pk>/  → Stripe Checkout for invoice
/p/<token>/subscribe/          → Stripe subscription signup
/p/<token>/billing-portal/     → Stripe billing portal
/stripe/webhook/               → Stripe webhook handler

/org/<slug>/                   → org dashboard
/org/<slug>/members/           → member list (HTMX search)
/org/<slug>/members/add/
/org/<slug>/members/import/
/org/<slug>/members/export/
/org/<slug>/members/bulk/
/org/<slug>/members/applications/
/org/<slug>/members/<pk>/
/org/<slug>/members/<pk>/edit/
/org/<slug>/members/<pk>/archive/
/org/<slug>/members/<pk>/promote/
/org/<slug>/members/<pk>/note/add/
/org/<slug>/members/<pk>/note/<note_pk>/delete/
/org/<slug>/members/<pk>/document/upload/
/org/<slug>/members/<pk>/waiver/offline/
/org/<slug>/classes/
/org/<slug>/classes/add/
/org/<slug>/classes/<pk>/
/org/<slug>/classes/<pk>/edit/
/org/<slug>/classes/<pk>/enrol/
/org/<slug>/classes/<pk>/unenrol/<member_pk>/
/org/<slug>/classes/<pk>/waitlist/<member_pk>/remove/
/org/<slug>/classes/<pk>/generate-sessions/
/org/<slug>/classes/<pk>/sessions/<session_pk>/register/
/org/<slug>/classes/<pk>/sessions/<session_pk>/cancel/
/org/<slug>/classes/<pk>/sessions/<session_pk>/print/
/org/<slug>/classes/<pk>/coaches/add/
/org/<slug>/classes/<pk>/coaches/<coach_pk>/remove/
/org/<slug>/classes/coach/        → coach class list
/org/<slug>/classes/coach/<pk>/   → coach class detail
/org/<slug>/classes/analytics/
/org/<slug>/billing/
/org/<slug>/billing/create/
/org/<slug>/billing/bulk/
/org/<slug>/billing/export/
/org/<slug>/billing/chase-overdue/
/org/<slug>/billing/<pk>/
/org/<slug>/billing/<pk>/pay/
/org/<slug>/billing/<pk>/unpay/
/org/<slug>/billing/<pk>/record-payment/
/org/<slug>/billing/<pk>/email/
/org/<slug>/billing/<pk>/reminder/
/org/<slug>/progression/
/org/<slug>/progression/import/
/org/<slug>/staff/
/org/<slug>/audit/
/org/<slug>/calendar/
/org/<slug>/calendar/events/
/org/<slug>/finance/
/org/<slug>/settings/
/org/<slug>/settings/fields/
/org/<slug>/waivers/
```

### Templates

Base layout: `templates/org/base.html` — dark sidebar, Bootstrap 5.3, Bootstrap Icons, HTMX.

All templates extend `org/base.html`. Partials in `templates/members/partials/` for HTMX responses.

### Audit Logging

`django-auditlog` installed. All models registered. Every create/update/delete is logged with actor, timestamp, and field diffs. Viewable at `/org/<slug>/audit/`.

### What Has NOT Been Done Yet

- **Stripe Connect** — per-org Stripe accounts not yet wired; currently uses global keys only
- **S3 / Cloudflare R2** — file uploads use local filesystem; no cloud storage yet
- **DocuSeal** — originally planned for e-signatures; replaced in practice by a canvas-drawn signature captured on the signup page and stamped onto waiver PDFs via reportlab. DocuSeal integration no longer planned unless requirements change.

### Additional packages now in use (beyond original requirements.txt)

- `stripe` — Stripe SDK
- `reportlab` — PDF generation / signature stamping
- `Pillow` — image handling
- `django-auditlog` — audit logging (was already planned)
- `django-htmx` — HTMX middleware (was already planned)

---

## Developer Notes

- The developer is working solo on this as a spare time project
- They are using PyCharm (JetBrains, licensed) on Linux
- They are comfortable with Django from prior experience
- They are using this project partly to improve their TypeScript skills - however the decision was made to use Django for this project for speed and familiarity. TypeScript is not in use here.
- The developer runs **Backwell Judo Club** with approximately 70 members and 5 coaches/leadership team members. This is the primary real-world use case driving requirements. The club trains on **Tuesdays and Thursdays**.
- Claude Code is being used in the terminal to assist with development
- All decisions about stack, architecture, naming, and licencing documented above were made deliberately. Do not second-guess them without being asked.
