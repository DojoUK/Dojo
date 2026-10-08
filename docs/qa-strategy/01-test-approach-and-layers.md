# 1. Test approach and layers

> Child page of the [Dojo QA strategy](README.md).

## The layers

Use the lightest layer that can demonstrate the behaviour. State what each may not touch.

| # | Layer | Tooling | What it covers in Dojo | May not touch |
|---|---|---|---|---|
| 1 | **Unit** | `pytest` with `pytest-django`, `SimpleTestCase` where no database is needed | `billing/calculator.py`, `PolicyDiscount.amount_off`, `_estimate_sessions`, `Class.schedule_display`, `_parse_schedule`, `extract_custom_field_values`, `stamp_signature_on_pdf`, the darken helper, date and period arithmetic | The database, the network, the clock |
| 2 | **Model and database** | `TestCase` against the throwaway MySQL test database | Model invariants under MySQL strict mode: field lengths, choice values, `unique_together`, `PROTECT` on sold variants, `select_for_update` on stock, `anonymise()` leaving foreign keys intact, migrations applying from zero (`migrate` then `makemigrations --check`) | SQLite (the SOP rule: test on the engine you ship; the `method` overflow in issue #60 only shows on MySQL) |
| 3 | **View and boundary** | Django test client with a two-organisation fixture | Every route through the real URL conf and mixins: allow and deny per role, per organisation, per token; HTMX partial responses; form validation; the Stripe webhook endpoint with a signed payload | Real Stripe, real SMTP |
| 4 | **Email** | `locmem` outbox (`django.core.mail.outbox`) | Who gets each email, what it contains, that the portal link is the member's own, that nothing is sent when no address exists, that the PECR footer is present | Any real backend |
| 5 | **Jobs and resilience** | `call_command` with `freezegun` (or an injected `now`) | The four cron commands and the demo flush: day-before, day-of, second run, `--dry-run` changes nothing | Wall-clock time |
| 6 | **Contract** | Recorded Stripe event fixtures (from the Stripe CLI) asserted against the handler | Both payload shapes for subscription invoices, checkout completion, subscription updated and deleted, with `stripe.api_version` pinned | Live Stripe on a pull request |
| 7 | **Journeys and non-functional** | Playwright (Python) against the Compose stack, `axe-core` via Playwright, `manage.py check --deploy` | Five journeys (setup wizard, signup with drawn signature through approval to portal, bulk invoice to portal payment, coach register, member erasure), accessibility baseline on the two public pages, deploy-check parity, member list and bulk run at 1,000 members | Production data |

## The pyramid shape

Thin unit base for the pure helpers, a **heavy view-and-boundary band** because the whole
tenant boundary lives in view code and nowhere else, a solid email and jobs band because
those are where Dojo acts on the outside world, and a thin journey top. We are not chasing
browser coverage. We are chasing proof that organisation B cannot read organisation A, that
nothing personal is reachable without a session or the right token, and that the number on
an invoice is right.

## The starting condition (8 October 2026)

| | State |
|---|---|
| Test files | One placeholder (`documents/tests.py`). `Found 0 test(s)` |
| Runner | Django's default. Cannot create the test database with the Compose credentials |
| Pending tests | PR #61: 11 webhook tests, 6 failing against `main` |
| CI | None. No `.github/` directory |
| Lint, format, hooks | None |
| Dependencies | Nine unpinned names; the image resolves Django 6.0.7 and `stripe` 15.3.1 today |
| Coverage | Not measured |

## Conventions

- **Tests live in each app as a package**: `members/tests/test_views.py`,
  `members/tests/test_boundary.py`, and so on, so the suite grows per domain and the
  boundary suite is one file per app that a reviewer can find.
- **One shared fixture module** (`dojo/testing.py`) builds the two-organisation world:
  organisation A and B, an admin and a coach in each, a class per org with one coach
  assigned, two members per org (one adult with an email, one child with a guardian), a
  sentinel medical note and sentinel guardian phone number on each child, a billing policy,
  one unpaid invoice each. Every boundary test starts from it.
- **Sentinels, not inspection.** The leak tests look for the sentinel strings in response
  bodies, in mail bodies, in files under `media/`, never for the absence of an exception.
- **The route sweep** iterates `get_resolver().url_patterns` recursively and fails if a
  named route is not present in the boundary matrix. New routes therefore cannot be added
  without a decision about who may call them.
- **Clock injection** for every date comparison in a job or an overdue rule: the tests pass
  `now`, or freeze time, and never depend on the day they run.
- **Money in `Decimal`.** A lint rule (ruff `flake8-builtins` plus a small custom check in
  CI) fails on `float(` in `billing/`, `inventory/` and the finance views.
- **Email through the outbox.** `EMAIL_BACKEND` is forced to `locmem` by the test settings
  regardless of `.env`; a test that finds the outbox empty when it expected mail fails loudly.
- **Test settings** (`dojo/settings_test.py`) import the base settings and override the
  database user, the email backend, `STRIPE_*` to sentinel values and `SECRET_KEY` to a
  constant, so no test depends on a developer's environment file.
- **MySQL strict mode on**, in tests as in production, so overflows raise rather than
  truncate.
- **Made to fail.** Each new suite's pull request includes the output of the suite failing
  against the defect it guards (revert the fix, run, paste).
