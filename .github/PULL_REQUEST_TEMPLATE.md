<!--
  The checklist below is the QA strategy's hard rules and surfaces, in the same words.
  Strategy: docs/qa-strategy/README.md. Change the rule there first if the rule moves.
-->

## What does this PR do?

<!-- One paragraph. Link the issue. -->

## Checklist

- [ ] Lint and tests pass locally (`ruff check . && pytest`)
- [ ] New or changed logic has tests, and a new suite has been **made to fail** (output pasted below)
- [ ] No test reaches Stripe or sends real email: Stripe stubbed, mail asserted through the outbox
- [ ] Any new or changed route that takes an `org_slug`, a token or an object key has its row in the tenant matrix (allow for the right caller, deny for the other organisation, the unassigned coach, a wrong token and anonymous)
- [ ] No personal data is reachable through an unauthenticated door (no new `static()` mounts, no new public views without a decision)
- [ ] Money paths use `Decimal`; a change to the calculator, the webhook or invoice state keeps the money modules at 100 percent and updates the golden masters deliberately
- [ ] A scheduled or destructive job is tested against a fake clock, including a second run
- [ ] Model changes include a migration and `makemigrations --check` is clean
- [ ] New environment variables are documented in `.env.example` and `SELF_HOSTING.md`
- [ ] No debug code, no `except Exception: pass` without a recorded skip reason

## Made to fail

<!-- Paste the failing output of the new or changed suite against the defect it guards. -->

## Anything reviewers should pay extra attention to?

<!-- Optional -->
