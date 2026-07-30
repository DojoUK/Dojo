# GDPR TODO

Internal tool — most processing covered by contract/legitimate interests. Priority items only.

## Must do

- [ ] Add member erasure / anonymisation view — currently archive only sets `is_active=False`, no way to honour a deletion request without manually editing the DB
- [ ] Auto-delete or anonymise records past the 3-year retention mark — the UI flags them but nothing acts on them (`members/views.py:603-619`)
- [ ] Document lawful basis for medical data processing — add a note to the membership paperwork or README (basis: vital interests / member safety)
- [ ] Purge rejected `MemberApplication` records after a reasonable period — currently retained indefinitely with address, medical info, and signature

## Nice to have

- [ ] Register `MemberNote` with `django-auditlog` — notes can contain sensitive info but aren't currently audited (`members/apps.py`)
- [ ] Stop collecting postal address on signup if it's not transferred to the `Member` record — currently collected then silently discarded (`members/views.py:362-369`)
- [ ] Add self-service "download my data" option to the member portal
- [ ] Set `SESSION_COOKIE_SECURE = True` and `CSRF_COOKIE_SECURE = True` in production settings
- [ ] Lock down `ALLOWED_HOSTS` to the actual domain(s) in production (`dojo/settings.py:13`)
- [ ] Add portal token expiry or revocation mechanism

## Out of scope (handled offline)

- Privacy notices / Art. 13 — cover in the club's physical membership paperwork
- Lawful basis records for standard processing — contract basis covers name, contact, payment, attendance
