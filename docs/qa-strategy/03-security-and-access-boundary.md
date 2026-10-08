# 3. Security and access-boundary testing

> Child page of the [Dojo QA strategy](README.md). **This is the gating suite.** Nothing
> here is optional for a Tier 1 product that holds children's medical data.

## Why this suite carries the highest bar

Dojo's boundary is application code. There is no row-level security, no per-tenant
database, no middleware that scopes queries. Each of the 93 admin views, 6 org views and 3
coach views adds `organisation=self.org` (or a join to it) by hand on every lookup. The
failure mode of a missed filter is not an exception; it is organisation B's admin reading
organisation A's child's medical note with a 200. Only enumerated positive and negative
cases can catch that, which is why "no errors" is never evidence here.

## The fixture

`dojo/testing.py` builds, once per test class:

- Organisations **A** and **B**, each with one `org_admin` user, one `coach` user, one
  class with the coach assigned, and one class with no coach.
- Per organisation: an adult member with an email, a child member with a guardian who has
  an email, a sentinel medical note (`MED-SENTINEL-A-7f3c` / `-B-`), a sentinel guardian
  phone (`07700 900A71` / `B71`), one unpaid invoice, one waiver template, one signed waiver
  file written under `media/`, one document, one progression system with two stages, one
  product with one variant.
- A **superuser** with no membership.
- An **anonymous** client.

## The tenant matrix

For every named route in the project (143 today), the matrix records the expected status
for each caller. The route sweep test walks `urlpatterns` and fails if a route has no row.

| Caller | Org A admin route with A's object | Same route with B's object | Portal route with A's token | B's token | Anonymous |
|---|---|---|---|---|---|
| A admin | 200 or 302 | **403 or 404** | n/a | n/a | n/a |
| A coach, assigned | 200 on coach routes for their class; 403 on admin routes | 403 or 404 | n/a | n/a | n/a |
| A coach, not assigned | 403 on the class | 403 or 404 | n/a | n/a | n/a |
| B admin | 403 or 404 | 200 | n/a | n/a | n/a |
| Superuser | 200 (bypass, by design) | 200 | n/a | n/a | n/a |
| Member with A's token | n/a | n/a | 200 | **404** | n/a |
| Anonymous | **302 to login** | 302 | 404 on a bad token | | 200 only on signup, login, setup when no org, webhook (400) |

Rules of the matrix:

1. **Every deny case also asserts the sentinels are absent** from the response body, not
   just the status code. A 403 page that leaks a name in a breadcrumb is a leak.
2. **Every POST deny case asserts the database did not change**: count rows before and
   after. A 404 that was raised after `save()` is a tamper.
3. **Object references inside a POST are tested too.** `member_id`, `discount_id`,
   `template_id`, `user_id`, `class_pk`, `stage_id`, `holiday_pk`, `variant_id`: each is
   sent with B's value against A's route and must be refused. This is where `AddCoachView`
   fails today.
4. **Superuser bypass is a documented design choice** for the SaaS operator, recorded in
   the decision log, and the matrix pins it so a change is deliberate.

## The doors without a login

- **The portal token.** 32 bytes from `secrets.token_urlsafe`: unguessable, and the test
  suite does not pretend otherwise. What it proves is scope: a valid token opens exactly
  one member's data, never another's; an archived or anonymised member's token is dead; a
  regenerated token kills the old one immediately; the token never appears in the audit log
  or in any page other than the member's own portal and emails.
- **The media directory.** With the documented settings, `/media/<path>` is served by
  Django to anyone. The test fetches the fixture's signed waiver and document paths with the
  anonymous client and with B's admin and expects **404 or 403 on both**. On `main` today
  this test fails, which is the evidence for the Phase 1 change: remove the `static()`
  mount for `MEDIA_URL`, serve files only through the existing authenticated download views
  (and for S3 or R2 later, through signed URLs with short expiry).
- **The public signup.** Proves the application lands in the organisation in the URL and
  nowhere else, that the page never renders another applicant's data, that a missing
  required signature is refused, and that a honeypot or rate limit (decision item) rejects
  a burst.
- **The Stripe webhook.** Unsigned, badly signed and unconfigured requests all return 400
  and change nothing. A correctly signed event for an invoice in organisation A updates only
  that invoice.
- **Password reset and the Host header.** With `ALLOWED_HOSTS=['*']` the reset email's link
  is built from the request `Host`. The test sends a reset with `Host: evil.example` and
  asserts the mail body does not contain that host once `SITE_URL` is used to build the
  link (Phase 1 change), or documents the current behaviour as accepted for self-hosters
  behind a proxy (decision item). The SaaS must not ship with the wildcard.

## Destructive actions

| Action | Who can trigger it | What the test proves |
|---|---|---|
| `seed_demo --flush` via `ReseedDemoView` | Any admin of a demo-flagged org | After the fix: only the demo org's rows are deleted; B's counts unchanged. Today: fails, B is wiped |
| `EraseMemberView` and `enforce_retention` | Admin; cron | Irreversible by design; refuses active members and retention overrides; second run no-op; sentinels gone from the row, guardians and notes deleted, invoices intact |
| `rotate_stale_portal_tokens` | Cron | Old links dead, new link emailed to the resolved recipient, nothing rotated inside the window |
| `purge_stale_applications` | Cron | Pending never touched; windows exact to the day |
| Bulk archive | Admin | Only selected members of this org; `archived_at` set (decision item: today it is not) |
| `DocumentDeleteView`, `WaiverDeleteView`, `SignedWaiverDeleteView` | Admin | File removed from disk and row removed; B's files untouched |

## Secrets and configuration

- `.env` is never read by the test settings; a test that observes a real `sk_live` or
  `whsec_` value fails the suite.
- `SECRET_KEY` persistence: the `.secret_key` file is created on first boot and reused;
  the test proves a second settings load reads the same key.
- `manage.py check --deploy` runs in CI as advisory, with its findings listed against the
  self-hosting guide so the two stop disagreeing (`DEBUG=True` in production is the main
  one; the fix is `collectstatic` or WhiteNoise, decision item).
- `gitleaks` scans every pull request; Dependabot security updates are on.

## What this suite is not

It does not prove the token is unguessable (that is `secrets`), it does not test Django's
own session or CSRF machinery, and it does not test Stripe's signature algorithm. It proves
that Dojo's own code uses those things correctly and that no path around them exists.
