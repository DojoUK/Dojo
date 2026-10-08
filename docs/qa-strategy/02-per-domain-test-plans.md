# 2. Per-domain test plans

> Child page of the [Dojo QA strategy](README.md). Risk per domain is **high** where a
> defect touches the boundary, personal data or money; **medium** where it corrupts
> records the club relies on; **low** where it is cosmetic or self-correcting.

## Organisations, setup and staff (`organisations/`)

**Risk: high** (first-run bootstrap, role changes, the demo flush).

- `SetupView` locks itself out once an organisation exists; a second visit redirects to
  login; the password rules are enforced; the demo mode seeds and logs in.
- `ReseedDemoView` refuses on a non-demo organisation (403) and, after the Phase 1 fix,
  touches only the demo organisation's rows. Until then it is marked as a known
  tenant-wide destructive action in the decision log.
- `StaffListView`: cannot change own role or remove self; cannot act on another
  organisation's membership (boundary suite); qualifications and holidays save and show;
  a holiday excludes the coach from the register for its dates only.
- `DashboardView`: the money tiles and admin alerts are absent from a coach's response
  (template gating proven by sentinel, not by reading the template); upcoming sessions for
  a coach are only their classes.
- `AuditLogView`: entries for the other organisation's objects never appear; the 7 content
  types shown versus the 20 registered is a decision item.
- `OrgSettingsView`: theme colours round-trip; `custom_css` is injected raw into every page
  for that organisation (admin-controlled, same-tenant: documented as accepted, not
  tested as a leak).
- `AnnouncementListView`: recipient selection per class and for all; count recorded matches
  sends; nothing sent to inactive members.

## Members, applications and retention (`members/`)

**Risk: high** (personal data of minors, erasure, import).

- CRUD through `MemberForm` with guardians inline and custom fields; the custom-field
  values survive an edit that does not touch them; a select option outside the list is
  rejected.
- `MemberListView` HTMX partial returns rows for the right organisation only; the
  `licence` and `waiver` filters produce the expected sets from the fixture.
- Bulk actions: archive sets `is_active` only (note: not `archived_at`, decision item);
  `invoice_create` with no amount and no monthly fee creates nothing; emails go to the
  resolved recipient.
- Import: the three date formats parse; a row without a name is skipped and reported; the
  preview creates nothing; import creates once.
- Export: contains only this organisation's members; no `medical_info` column (and a
  decision on whether it should).
- Applications: `ApproveApplicationView` copies every field (including medical and
  address), creates the guardian, stamps every active waiver, sends the welcome email,
  and is not repeatable (a second approve of the same application must not create a second
  member: currently it would, decision item). Reject records `decided_at`.
- Erasure: `EraseMemberView` refuses an active member, refuses when retention notes exist,
  is idempotent; after `anonymise()` the sentinel strings are absent from the row, the
  guardians and notes are gone, invoices and attendance still resolve to the row, and the
  old portal token no longer opens anything.
- Portal token regenerate: old token returns 404, new one works, audit log excludes the
  token field.

## The member portal and signup (`members/portal_views.py`, `signup_views.py`)

**Risk: high** (the only doors without a login).

- Wrong token, archived member's token and anonymised member's token all 404.
- The portal shows only the token holder's invoices, attendance, progression and guardian;
  the sentinel for any other member is absent.
- `DownloadDataView` contains the member's own data and never internal notes.
- Checkout refuses an invoice that is paid, belongs to another member, or when Stripe is
  unconfigured (redirects, no SDK call); with Stripe stubbed, the Checkout session carries
  the right amount in pence, the right `invoice_pk` metadata and the member's or guardian's
  email.
- Subscription: creates a customer once and reuses it; refuses without a monthly fee.
- Cancel subscription and billing portal: refuse without the respective Stripe ids.
- Signup: a required waiver demands a signature; a bad date of birth is dropped not
  crashed; the first `X-Forwarded-For` address is recorded; the application lands in the
  right organisation; the response never echoes another applicant's data; a rate limit or
  honeypot is a decision item.

## Classes, sessions, attendance (`classes/`)

**Risk: high for coach siloing, medium otherwise.**

- Coach siloing: a coach assigned to class 1 can open class 1's register, detail and print
  views and is refused on class 2 in the same organisation and on any class in the other;
  `CoachClassListView` lists only assigned classes for a coach and all for an admin.
- `AddCoachView` only accepts users who are members of this organisation (after the Phase 1
  fix); until then the test is the failing evidence.
- Enrol: full class sends to the waiting list; unenrol promotes the first waiter and emails
  them once; `unique_together` prevents double enrolment.
- Session generation: respects the schedule days, is idempotent across a second run,
  caps at 52 weeks, creates nothing for an empty schedule.
- Register: saving with no coach present is refused when coaches exist; attendance rows
  are upserted; coaches on holiday are excluded from the list; the unsigned-waiver badge
  matches the fixture.
- Cancel: toggles and emails enrolled members' resolved recipients once; reinstating sends
  nothing.
- Analytics and export: counts match hand-worked fixture values; date filters ignore
  malformed input instead of raising.
- Calendar events: scoped to the coach's classes; a malformed `start` does not 500
  (currently it would).

## Billing (`billing/`)

**Risk: high.** Detail in [Financial correctness](04-financial-correctness.md).

- Policies, terms and discounts: validation of amounts, deletion cascades (deleting a
  policy nulls member and class references), discount `amount_off` for both types.
- Calculator golden masters and properties.
- Bulk run: preview rows match the created invoices; a member already invoiced for the
  period is skipped; the override amount is used verbatim; zero or negative nets create
  nothing; emails on request only.
- Invoice create with product items: stock is locked and decremented inside one
  transaction; insufficient stock rolls everything back; `all` plus items is refused.
- Payments: a partial payment leaves the invoice unpaid; a full one marks it paid; the
  amount is `Decimal`.
- Webhook: signature guard, configuration guards, both payload shapes, redelivery, method
  value, unknown subscription ignored (PR #61).
- Export: only this organisation; one row per invoice with the latest payment.

## Inventory (`inventory/`)

**Risk: medium** (stock and price, no personal data).

- `adjust_stock` cannot go below zero, locks the row, records a movement with the actor;
  two concurrent sales of the last item produce one sale and one `InsufficientStockError`.
- Deleting a sold variant is blocked by `PROTECT` and reported; inactive products are hidden
  from the invoice form.
- Prices are `Decimal` end to end (the views currently use `float`, decision item).

## Documents and waivers (`documents/`)

**Risk: high** (the media door; signed medical waivers).

- Every file under `media/` is unreachable without a session once the Phase 1 change lands;
  the authenticated download views refuse the other organisation's documents, templates and
  signed waivers.
- `stamp_signature_on_pdf` places the overlay on the last page, accepts a data-URI prefix,
  and fails cleanly on a non-PNG payload; approval does not block when stamping fails
  (current behaviour, kept, but the failure must be logged rather than swallowed).
- Offline waiver upload records `offline=True` and the right member.

## Progression (`progression/`)

**Risk: low to medium.**

- Systems and stages: unique names per organisation and per system; deletion refused when
  records exist; reorder keeps a dense `order`; one default per system.
- Bulk default assignment skips members who already have a record in that system.
- CSV import: missing columns reported; unknown member or stage skipped with the row number;
  bad dates counted as errors; nothing created from a preview.

## Email (every `send` site)

**Risk: high** (wrong recipient; real send from a test).

- A single `resolve_recipient(member)` is the target (Phase 2). Until then each of the eight
  sites is tested against the same four-member fixture (adult with email, adult without,
  child with guardian email, child with neither) and the matrix of who receives what is
  pinned as a golden master, so the inconsistency is at least visible before it is unified.
- Every outgoing message carries the organisation contact line.
- No send site raises; every send site records a skip reason that the test can read.

## Scheduled jobs (`management/commands/`)

**Risk: high.** Detail in [security page](03-security-and-access-boundary.md) under
destructive actions.

- `enforce_retention`: 3 years minus a day keeps; 3 years and a day anonymises; retention
  notes keep; already anonymised skipped; `--dry-run` changes nothing; second run changes
  nothing.
- `purge_stale_applications`: 90 and 30 day windows; pending never touched; dry run.
- `rotate_stale_portal_tokens`: 180-day window, `--days` override, emails the new link,
  old token dead, archived members skipped.
- `send_overdue_reminders`: `--days` and `--resend-after` windows, `reminder_sent_at`
  stamped, inactive members skipped, dry run sends nothing.
- `seed_demo --flush` scoped to the demo organisation (after the fix).

## Audit log (`django-auditlog`)

**Risk: medium.**

- A change to a registered model by an admin produces one entry with the actor; the
  `token` and `signature_data` fields never appear in a diff.
- The organisation audit view shows nothing from the other organisation; the set of content
  types it shows is pinned so a model registered later must be added deliberately.
