# QA & Testing Strategy: Dojo

`PROPOSED` Drafted 8 October 2026 against `main` at `a84b04a`. Becomes `ACCEPTED` when the
maintainer records the decision in the [decision log](06-open-items-and-decision-log.md).

| | |
|---|---|
| **QA owner (this strategy)** | Matt Wood |
| **Project owner / approver** | Matt Wood (sole maintainer) |
| **Engineering** | Matt Wood, plus any contributor through pull requests |
| **Tier** | **1**: customer-facing portal and signup, Stripe payments, personal data including children's medical information |
| **Status** | Proposed |
| **Last reviewed** | 2026-10-08 |

**What this is.** The QA and testing strategy for Dojo, the open source club management
platform. It follows the risk-weighted method used for the Vanaways products: name the few
ways the product can do lasting damage, set a hard bar on those, let everything else be
ordinary. Because the project owner and the QA owner are the same person, this document
decides rather than proposes; contributors are asked to follow it, and the pull request
template carries its rules in the same words.

## The one thing to take away

Dojo holds the kind of data a club is trusted with and cannot take back: children's dates
of birth, medical conditions, guardians' phone numbers, signed waivers, staff safeguarding
numbers, and the money a family pays each month. It is multi-tenant by design and will be
hosted for many clubs at once, so one organisation's admin must never see another's
members, and no one without a login or a valid portal link must see anyone's. There is no
database-level isolation: the whole access boundary is 93 view classes each remembering to
filter by organisation, 6 token-gated portal views and one public form. A missed filter
returns the wrong club's data with no error, and the documented deployment serves every
uploaded document to anyone who knows the path. The strategy is therefore weighted the
same way as the CRM's: **the access boundary and personal data first, the money second,
everything else ordinary.**

## The single biggest finding

**The test suite cannot run, and the one place real tests exist is an unmerged pull
request.** The repo has one empty test file; `manage.py test` finds nothing. With the
Compose defaults the application's database user cannot create Django's test database,
so a contributor who writes a test cannot run it without hand-granting rights, which is
exactly what the author of PR #61 had to do to run the 11 webhook tests that have sat open
since 31 August. Six of those tests fail against `main`: subscription payments are silently
dropped because the unpinned Stripe SDK moved a field. Phase 0 is therefore not "write
tests"; it is make the runner work from a clean `docker compose up`, pin what the image
installs, put a workflow in front of `main`, and merge the tests that already exist.

## What we are protecting

In order of blast radius. Each is a defect, not a feature.

1. **One club seeing or changing another club's data.** The boundary is `OrgMixin`,
   `OrgAdminMixin`, `ClassCoachMixin` and a hand-written `organisation=` filter on every
   lookup. Two gaps found in the audit: `AddCoachView` accepts any user in the database, and
   the demo reset wipes every organisation. *Automated today: nothing.*
2. **Personal data leaving through an unauthenticated door.** Signed waivers and uploaded
   documents are served directly from `media/` with `DEBUG=True`, which the self-hosting
   guide tells operators to keep. The portal token is a bearer credential in a URL and
   unlocks a full data export. The public signup form takes medical information from
   strangers. `ALLOWED_HOSTS=['*']` lets a forged `Host` header poison password-reset links.
   *Automated today: nothing.*
3. **A wrong invoice, a payment recorded twice, or a payment never recorded.** The
   calculator's per-session maths and three stacked discounts; the bulk run's duplicate
   guard and float overrides; the webhook that drops subscription payments and would create
   duplicates once fixed (issue #60); "mark as paid" that records no payment so the revenue
   figure and the invoice disagree. *Automated today: 11 tests in PR #61, unmerged.*
4. **A real email reaching the wrong person, or reaching anyone from a test.** Eight send
   sites, two different member-versus-guardian rules, every failure swallowed. *Automated
   today: nothing; the console backend is the only safety.*
5. **A scheduled job destroying or keeping data on the wrong day.** Retention anonymisation
   is irreversible and deletes guardians and notes; token rotation locks members out of
   their links; reminders can nag twice. None takes an injected clock. *Automated today:
   nothing.*
6. **An upstream shape change found in production.** Unpinned Django and Stripe SDK; no
   `stripe.api_version`; the webhook parses whatever the SDK's default version sends on
   build day. *Automated today: nothing.*

## Hard rules

A change that breaks one of these is a defect regardless of what else it does.

- **No test reaches Stripe or sends a real email.** Stripe is stubbed at the SDK boundary;
  mail is asserted through Django's `locmem` outbox; the test settings never load `.env`;
  CI carries no secrets. The one exception is the scheduled Stripe test-mode journey, which
  runs on a timer with repository secrets and never on a pull request.
- **Never run tests against a database that holds real members.** Tests run only against
  Django's throwaway test database on a MySQL service that exists for that purpose. The
  Compose file provides it; nobody grants rights by hand.
- **Prove the boundary with enumerated allow and deny cases.** Every route that takes an
  `org_slug`, a token or an object key ships with a test that the other organisation's
  admin, a coach outside the class, an anonymous visitor and a wrong token are refused, and
  a control that the right caller is admitted. A route sweep fails the build when a new
  URL pattern has no entry in the matrix.
- **Personal data never leaves through an unauthenticated door.** A sentinel medical note
  and guardian phone number seeded for organisation A are asserted absent from every
  response to organisation B, to an anonymous request, and to a wrong token, and from
  every file under `media/` reachable without a session.
- **Money is proven, not observed.** `billing/calculator.py`, the webhook handlers and the
  invoice state transitions hold 100 percent line and branch coverage, reported as their
  own number. Expected figures are worked by hand in fixtures and never produced by the
  code under test. `Decimal` only; a `float` on a money path fails lint.
- **Destructive jobs are proven against a fake clock before they are scheduled.**
  Retention, purge, rotation, reminders and the demo flush each have a test for the day
  before the cutoff, the day of, and a second run that changes nothing.
- **A suite is not finished until it has been made to fail.** The failing output goes in
  the pull request, because it is the one part of a test a reviewer cannot check by reading.

## Topology note

One MySQL 8 database, one Django process, local filesystem under `media/`. Tests use
Django's generated `test_dojo` database on the Compose `db` service (or a CI service
container), created and dropped by the runner. There is no staging environment: `main`
is what a self-hoster pulls. Stripe is global-key today; Stripe Connect per organisation
is planned and will move the money boundary when it lands. The hosted SaaS does not exist
yet, so the two-organisation fixture is proving the future, as the CRM's did; the public
repo and the self-hosters are the present.

## How the strategy is organised

| Page | Covers |
|---|---|
| [Audit notes](audit-2026-10-08.md) | What was found at `a84b04a`, with the evidence behind every claim above |
| [1. Test approach and layers](01-test-approach-and-layers.md) | The seven layers for a Django monolith, the pyramid shape, conventions and the starting condition |
| [2. Per-domain test plans](02-per-domain-test-plans.md) | Organisations and staff, members and applications, the portal, classes and attendance, billing, inventory, documents, progression, email, jobs, audit log |
| [3. Security and access boundary](03-security-and-access-boundary.md) | The gating suite: the tenant matrix, coach siloing, the token door, media, signup, host header, demo flush |
| [4. Financial correctness](04-financial-correctness.md) | Calculator golden masters and properties, the bulk run, the webhook, invoice state, figures on the dashboard |
| [5. CI/CD and phased rollout](05-ci-cd-and-phased-rollout.md) | The workflow, branch protection for a public solo-maintained repo, coverage ladder, the five phases |
| [6. Open items and decision log](06-open-items-and-decision-log.md) | Decisions the maintainer has to make, and the record of those made |
| [Backlog](backlog.md) | The GitHub issues to create, one milestone per phase |

## Rollout at a glance

Phase 1 is the only phase that gates merges. Phase 0 adds visibility and costs nothing but
a day.

| Phase | Theme | Gates? |
|---|---|---|
| 0 | Visible: runnable tests from a clean checkout, pinned dependencies, CI workflow, PR template, Dependabot and secret scanning, merge PR #61 | No gates |
| 1 | Protect the boundary, the personal data and the money: tenant matrix, media door, token door, calculator and webhook suites, required on `main` | **Gating** |
| 2 | Resilience: jobs against a fake clock, one email resolver, Stripe redelivery, concurrency on stock and invoices | Ratchet |
| 3 | Journeys and non-functional: Playwright on five journeys, axe on signup and portal, `check --deploy` parity, performance at realistic member counts | Ratchet |
| 4 | Contract and ratchet: Stripe fixture drift job, dependency updates by bot, mutation testing on the calculator, coverage floor raised toward 80 percent | Target |

## Live work

GitHub issues in `DojoUK/Dojo` labelled `qa-strategy` plus `phase-0` to `phase-4`, grouped
under milestones "QA Phase 0" to "QA Phase 4". Decisions are issues too, titled
`[Decision] ...`. The issue list to create is in [backlog.md](backlog.md).
