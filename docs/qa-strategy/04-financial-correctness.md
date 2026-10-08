# 4. Financial correctness

> Child page of the [Dojo QA strategy](README.md). Money here is real: a family's monthly
> fee, charged by card through Stripe or chased by email. A wrong figure is a refund, a
> dispute, or a child quietly dropped for non-payment of an invoice that was never right.

## The money modules

These hold **100 percent line and branch coverage**, reported as their own number, from
Phase 1:

- `billing/calculator.py` (`calculate_invoice_amount`, `_estimate_sessions`)
- `billing/models.py` (`PolicyDiscount.amount_off`, `Invoice.is_overdue`, `Invoice.items_total`,
  `InvoiceItem.line_total`)
- `billing/stripe_views.py` (every handler)
- The state transitions in `billing/views.py`: `MarkPaidView`, `MarkUnpaidView`,
  `RecordPaymentView`, the transactional block in `InvoiceCreateView`, and the creation loop
  in `BulkInvoiceView.post`
- `inventory/models.py` (`adjust_stock`)

## Golden masters

Expected figures are worked by hand in a fixture file and never derived from the code.

| Case | Inputs | Expected |
|---|---|---|
| Flat monthly | policy flat £45.00, no discounts | gross 45.00, discount 0.00, net 45.00 |
| Per session, one class | £8.50 per session, 9 sessions in the month | 76.50 |
| Per session, two classes with 50 percent multi-class discount | 9 and 8 sessions | 76.50 + (8 × 4.25 = 34.00) = 110.50 |
| Member percentage discount | flat 45.00, 10 percent | discount 4.50, net 40.50 |
| Member fixed discount larger than gross | flat 20.00, fixed 25.00 | discount 20.00, net 0.00 (never negative) |
| Family 15 percent on top of member 10 percent | flat 45.00 | discounts 4.50 and 6.75 (both from gross, not compounded), net 33.75 |
| Rounding | per session £7.99, 7 sessions, 33 percent multi-class on second class with 5 sessions | pinned to the penny as the code rounds today, with the rule written down |
| Schedule estimate fallback | no sessions created, 2 days a week, 31-day month | round(2 × 31 / 7) = 9 |
| Term estimate | 2 days a week, 70-day term | 20 |

The rounding row exists because `round(Decimal, 2)` uses banker's rounding and the
`session_rate * (1 - pct/100)` line is not rounded at all before multiplying. The strategy
does not decide the rule; it pins the current answer so a change is a conscious one (decision
item: round half up to the penny per line, or keep).

## Properties

Over generated inputs (`hypothesis`), with a reported seed on failure:

- `net == max(0, gross - sum(discounts))` and `net >= 0` always.
- Each discount is computed from `gross`, never from a previously discounted value.
- Adding a second enrolled class never decreases gross.
- A percentage discount of 0 and a fixed discount of 0 change nothing.
- Every returned amount has at most two decimal places and is a `Decimal`.
- The bulk-run amount for a member equals the calculator's net for the same inputs unless an
  override was supplied, in which case it equals the override exactly.

## The bulk run

- Preview and create agree row for row.
- A member already invoiced for the period label is skipped and counted, even when selected.
- Legacy `monthly_fee` members with no policy are invoiced at the fee.
- Override amounts arrive as strings and are converted with `Decimal`, not `float`
  (code change in Phase 1).
- `send_emails` sends one invoice email per created invoice to the resolved recipient and
  none for skipped members.

## Invoice state

There are two truths today: `Invoice.status` and the `Payment` rows. The suite pins the
relationship so the decision in the log can be made with the behaviour visible:

- `RecordPaymentView` with a partial amount leaves `unpaid`; reaching the total flips to
  `paid`; over-payment is recorded and flagged (decision).
- `MarkPaidView` today creates no `Payment`, so the dashboard's revenue tile and the finance
  report exclude it while the outstanding tile also excludes it. The test names this and
  fails once the decision is to create a `Payment` on mark-paid (recommended).
- `Invoice.Status.OVERDUE` is never assigned; the list filter for it returns nothing. The
  test asserts the current behaviour and is updated when the decision (compute, never
  persist, and drop the choice; or set it from the reminder job) lands.
- `MarkUnpaidView` after a payment leaves an orphan payment; pinned and decided.

## The Stripe webhook

PR #61 is the Phase 1 suite for this handler and is adopted as written: both payload
shapes, the expanded subscription object, redelivery, the method value, non-subscription
invoices ignored, unknown subscriptions ignored, signature and configuration guards. Added
on top:

- `checkout.session.completed` for an already-paid invoice creates no second `Payment`.
- The recorded `Payment.amount` for a checkout equals the Stripe `amount_total` in pounds,
  not `invoice.amount` (they can differ if the invoice was edited after the session was
  created; decision item on which wins).
- `stripe.api_version` is pinned in settings and the fixtures are recorded at that version;
  a recorded fixture at the next version lives beside it so the handler is proven against
  both before the pin moves.

## Figures shown to the club

- Dashboard: `revenue_month`, `outstanding`, `attendance_rate`, `at_risk_count`,
  `new_members_month` computed by hand from the fixture and asserted.
- Finance report: twelve months of labels end on the current month; the revenue map sums
  payments by month in the organisation's time zone; the outstanding-by-member list sums
  per member.
- CSV exports: the billing export's total of the `Amount` column equals the screen total
  for the same filter.
- Portal: `outstanding_total` equals the sum shown to the member and excludes paid invoices.

## What is explicitly out of scope

Stripe's own arithmetic, currency conversion (GBP only), VAT (none charged), and the
future Stripe Connect platform fee until it exists. When Connect lands, the per-organisation
account id becomes a boundary field and joins the tenant matrix.
