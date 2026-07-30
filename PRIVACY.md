# Data protection notes

Internal reference for how Dojo processes personal data. This is not the
member-facing privacy notice (see the notice shown on the public signup form,
and the club's own membership paperwork) — it's the lawful-basis record for
whoever operates a given Dojo instance.

## Lawful basis by data category

| Data | Fields | Basis |
|---|---|---|
| Contact & membership details | name, DOB, email, phone, guardian details, attendance, invoices | Contract — necessary to run the membership and deliver classes |
| Medical information | `Member.medical_info`, `MemberApplication.medical_info` | Vital interests / legitimate interests — coaches need this to respond safely to a medical event during activity. Only collected with the member's (or guardian's) knowledge at signup, and visible only to org admins and coaches. |
| Staff DBS / coaching licence numbers | `OrganisationMember.dbs_number`, `dbs_expiry`, `coaching_licence*` | Legal obligation / legitimate interests — safeguarding checks required to run activities involving minors |
| Payment data | Stripe customer/subscription IDs, invoices | Contract — required to collect membership fees. Card details never touch Dojo directly; Stripe (a sub-processor, US-based, GDPR-compliant via SCCs) handles those. |

## Retention

- Active members: retained for the duration of membership.
- Archived members: retained 3 years past archiving for insurance, medical,
  and safeguarding purposes, then anonymised automatically
  (`python manage.py enforce_retention`) unless an admin has recorded a
  reason to keep the record longer (`retention_notes`).
- Membership applications: rejected applications are purged 90 days after
  the decision; approved applications are purged 30 days after approval,
  once their data has been copied onto the member record
  (`python manage.py purge_stale_applications`).

Both commands support `--dry-run` and are intended to run on a schedule
(cron / Docker Compose scheduled job) — see the self-hosting guide.

## Portal link security

Each member's self-service portal is protected by a bearer token in the URL
rather than a login. To limit how long a leaked or forwarded link stays
valid:

- An org admin can invalidate a member's link immediately via "Reset portal
  link" on the member detail page.
- Links also auto-rotate after 180 days of no reset
  (`python manage.py rotate_stale_portal_tokens`, also supports `--dry-run`
  and `--days`) — the member is emailed a fresh link when this happens, so
  the old one can be retired without breaking their access.

## Email communications (PECR)

Every email Dojo sends — welcome, invoices/reminders, announcements, session
cancellations, portal-link refreshes — carries a footer line pointing the
recipient at the organisation's contact email to ask that non-essential
communications stop (`org_email` in `templates/emails/base.html`, or inline
in the two email templates that don't extend it). Announcements are free-text
and admin-controlled, so if a given instance uses them for anything
promotional rather than club-operational, this is the mechanism that
satisfies PECR reg. 22's requirement for a simple means of refusal.

## Third-party assets

All CSS, JS, icon, and font assets (Bootstrap, Bootstrap Icons, Inter,
HTMX, FullCalendar, Chart.js) are self-hosted under `static/vendor/` rather
than loaded from a CDN or Google Fonts. No visitor — including on the public,
unauthenticated signup form — has their IP address sent to a third party
just by loading a page.

## Third-party processors

- **Stripe** — payment processing, subscriptions, billing portal (US-based, SCCs in place)
- **Email provider** — configured per-instance via `EMAIL_HOST` in `.env`; self-hosters choose and are responsible for their own SMTP provider

## Member rights

- **Access / portability** — members can self-serve a full export via "Download my data" in their member portal; an org admin can also export on request.
- **Erasure** — archive the member, then use the "erase data" action on the
  Former Members page (`python manage.py enforce_retention` also does this
  automatically once the retention window passes).
- **Rectification** — edit the member record directly.
