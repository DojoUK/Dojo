# 6. Open items and decision log

> Child page of the [Dojo QA strategy](README.md). Each open item becomes a GitHub issue
> titled `[Decision] ...` with the `qa-strategy` label. Decided items move to the log below
> with the date and the reasoning.

## Open items

| # | Decision needed | Why it blocks | Blocks |
|---|---|---|---|
| 1 | **Accept this strategy and the Tier 1 classification.** | Nothing in the rollout starts until the maintainer records acceptance. | Everything |
| 2 | **Test runner: `pytest` with `pytest-django`, or Django's runner.** PR #61 uses `TestCase`, which `pytest-django` runs unchanged. Recommended: pytest, for fixtures, parametrisation, `hypothesis` and coverage integration. | Phase 0 tooling | Phase 0 |
| 3 | **Test database access.** Root credentials in the test settings (recommended for Phase 0) or a Compose init grant. | Nobody can run a test from a clean checkout today. | Phase 0 |
| 4 | **Serve `media/` only through authenticated views.** Remove the `static(MEDIA_URL)` mount; the download views already exist. For S3 or R2 later, signed URLs. Recommended: yes, in Phase 1, with a note in `SELF_HOSTING.md`. | Signed waivers and documents are world-readable on the documented deployment. | Phase 1 |
| 5 | **Pin dependencies.** `requirements.txt` with exact versions and `requirements-dev.txt`; Dependabot proposes bumps; `stripe.api_version` set in settings. | Unpinned builds already broke the webhook (issue #60); fixtures cannot be recorded against a moving target. | Phase 0 |
| 6 | **Branch rules on `main`** for a solo maintainer: require the `ci` checks and pull requests, no review requirement, admin bypass kept. | Without it CI is advisory and self-hosters can pull a red `main`. | Phase 1 |
| 7 | **`MarkPaidView` creates a `Payment` row** so revenue and invoice status agree. Recommended: yes, method `manual`, amount = remaining balance. | Two sources of truth for "paid". | Phase 1 |
| 8 | **`Invoice.Status.OVERDUE`**: drop the persisted choice and compute, or set it from the reminder job. Recommended: drop and compute; the list filter uses `is_overdue`. | Dead filter; misleading status. | Phase 1 |
| 9 | **Rounding rule for per-session discounts**: round half up to the penny per line, or keep the current unrounded multiply. | The golden master pins one answer. | Phase 1 |
| 10 | **Checkout payment amount source**: Stripe `amount_total` or `invoice.amount`. | They can differ if an invoice is edited after the session is created. | Phase 2 |
| 11 | **One recipient rule.** Member's own email first, guardian as fallback (welcome's rule), or guardian first when any guardian exists (invoice's rule). Recommended: if the member is under 18 and has a guardian with an email, the guardian; otherwise the member; written once in `members/recipients.py`. | Eight copies, two rules. | Phase 2 |
| 12 | **Demo reset scope.** `seed_demo --flush` must delete only the demo organisation. Recommended: scope the flush by organisation and refuse when more than one organisation exists. | Tenant-wide data loss from one button. | Phase 1 |
| 13 | **`AddCoachView` must only accept members of the organisation.** | Cross-tenant reference. | Phase 1 |
| 14 | **`ApproveApplicationView` must be idempotent** (a second approve must not create a second member). | Double-click creates a duplicate member and a second welcome email. | Phase 2 |
| 15 | **Bulk archive should set `archived_at`** like the single archive does, or the retention job will never see bulk-archived members. | Retention policy silently not applied. | Phase 2 |
| 16 | **Signup abuse control**: honeypot field, rate limit per IP, or both. | Public form stores medical text from anyone. | Phase 1 (test), Phase 2 (control) |
| 17 | **`ALLOWED_HOSTS` wildcard and password-reset links.** Build reset links from `SITE_URL`, or document the wildcard as self-hoster-only and forbid it in the SaaS settings. | Host header poisoning of reset emails. | Phase 1 |
| 18 | **`DEBUG=True` in production.** Wire `collectstatic` plus WhiteNoise (or serve static from the proxy) so `DEBUG=False` works, then flip the guide. Existing issue #26. | `check --deploy` cannot pass; tracebacks shown to the public on error. | Phase 3 |
| 19 | **Audit view coverage.** Show the 20 registered models or document why 7. | Billing and document changes are logged but invisible. | Phase 2 |
| 20 | **Member CSV export and `medical_info`.** Include (it is the member's data and an admin can see it anyway) or exclude (minimise what leaves as a file). | Pinned either way. | Phase 2 |
| 21 | **Superuser bypass of the organisation boundary** is the SaaS operator's door. Keep and pin, or require an explicit membership. | The matrix encodes one answer. | Phase 1 |
| 22 | **Where the weekly Stripe test-mode journey runs** and who holds the test-mode keys as repository secrets. | Secrets on a public repo. | Phase 3 |
| 23 | **Release tagging** so self-hosters pull tags, not `main`. | Rollback story for a self-hoster. | Phase 2 |
| 24 | **Merge PR #61.** | Eleven tests and a real production fix waiting since 31 August. | Phase 0 |

## Decision log

| Date | Item | Decision | By |
|---|---|---|---|
| 2026-10-08 | Delivery format | In-repo under `docs/qa-strategy/`, GitHub issues as the backlog, root stub, PR template. No Confluence or Jira for an open source repo. | Matt Wood |
| 2026-10-08 | Tier | 1, pending acceptance (item 1). | Matt Wood |
| 2026-10-08 | Method | The Vanaways risk-weighted method, unchanged: surfaces, hard rules, lightest layer, five phases, Phase 1 gates, re-verify after each phase. | Matt Wood |
