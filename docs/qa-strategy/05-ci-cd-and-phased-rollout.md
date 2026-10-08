# 5. CI/CD and phased rollout

> Child page of the [Dojo QA strategy](README.md).

## Where things stand

| | Today | Target |
|---|---|---|
| Workflow | none | `ci.yml` on every pull request and push to `main` |
| Branch rules on `main` | none | pull request required, the `ci` check required, no force-push; admin bypass kept for the solo maintainer until a second maintainer exists |
| Test database | app user cannot create it | provided by Compose and by a CI service container |
| Dependencies | unpinned | pinned with a lock, updated by Dependabot |
| Local checks | none | `pre-commit` with ruff (lint and format) and a money-path float check |
| Deploy | `git pull` and `docker compose up --build` by each self-hoster; migrations on boot | unchanged, plus a release tag and a changelog so self-hosters know what they are pulling |

## The workflow

One job per command so the check list on the pull request names what failed. No secrets:
the repo is public and forks open pull requests.

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12', cache: pip }
      - run: pip install -r requirements-dev.txt
      - run: ruff check . && ruff format --check .
  migrations:
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8.0
        env: { MYSQL_ROOT_PASSWORD: root, MYSQL_DATABASE: dojo }
        ports: ['3306:3306']
        options: --health-cmd "mysqladmin ping -proot" --health-interval 5s --health-retries 10
    env: { DB_NAME: dojo, DB_USER: root, DB_PASSWORD: root, DB_HOST: 127.0.0.1 }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12', cache: pip }
      - run: pip install -r requirements-dev.txt
      - run: python manage.py migrate --noinput
      - run: python manage.py makemigrations --check --dry-run
      - run: python manage.py check
      - run: python manage.py check --deploy || true   # advisory until the decision log closes it
  test:
    runs-on: ubuntu-latest
    services:
      mysql: # as above
    env: { DJANGO_SETTINGS_MODULE: dojo.settings_test, DB_HOST: 127.0.0.1 }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12', cache: pip }
      - run: pip install -r requirements-dev.txt
      - run: pytest --cov --cov-report=xml --cov-fail-under=0
      - run: python scripts/money_coverage.py   # 100 percent on the money modules, Phase 1
  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t dojo:ci .
```

A separate `stripe-journey.yml` runs weekly and on manual dispatch only, with
`STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from repository secrets (test mode), and
drives the portal payment journey against the Compose stack with the Stripe CLI forwarding
events. It never runs on a pull request.

## Test database provisioning

Two options, decided in the log:

1. **Compose init script**: a file under `docker/mysql-init/` run by the MySQL image on
   first boot that grants `dojo_user` `ALL ON test_dojo.*` (and `test_dojo%` for parallel
   runs). Works for self-hosters' existing volumes only after a re-init, which is the
   drawback.
2. **Test settings use root**: `dojo/settings_test.py` sets the database user to root with
   `DB_ROOT_PASSWORD`. Simpler, works on an existing volume, and the test settings are
   never used in production.

Option 2 is recommended for Phase 0; option 1 can follow.

## Coverage ladder

Measured from Phase 0, gated from Phase 1 at the module level, repo-wide floor from Phase 2.

| Phase | Repo-wide | Money modules | Boundary suite |
|---|---|---|---|
| 0 | reported | reported | route sweep reported |
| 1 | reported | **100 percent line and branch, gating** | **every route in the matrix, gating** |
| 2 | floor at the Phase 1 measurement, no regression | 100 | gating |
| 3 | floor raised each release | 100 | gating |
| 4 | 80 percent destination; mutation score on the calculator reported | 100 plus mutation | gating |

## Branch and merge rules for a public, solo-maintained repo

- Every change by pull request, including the maintainer's own, so CI runs before `main`
  moves and self-hosters never pull a red build.
- The `ci` checks required on `main`; no review requirement until a second maintainer
  exists (a rule nobody can satisfy is a rule that gets bypassed).
- Fork pull requests get the same checks and no secrets.
- A release is a tag plus a changelog entry; `SELF_HOSTING.md`'s update section points at
  tags rather than `main`.

## The phases

### Phase 0: Visible (no gates)

- Test settings module; test database access from Compose; `pytest` with `pytest-django`
  and `coverage`; `requirements.txt` pinned and `requirements-dev.txt` added.
- `ci.yml` as above; `.github/PULL_REQUEST_TEMPLATE.md`; Dependabot for pip and Actions;
  gitleaks.
- Merge PR #61 (11 tests) once it runs green in CI.
- `pre-commit` with ruff.
- Two-organisation fixture in `dojo/testing.py` and the route sweep in reporting mode (lists
  uncovered routes, does not fail).
- `QA_STRATEGY.md` stub and this folder.

### Phase 1: Protect the boundary, the data and the money (gating)

- Tenant matrix complete for all routes, including POST object references.
- Media served only through authenticated views (code change, decision 4).
- Token door suite; signup suite; Host header decision implemented or recorded.
- `AddCoachView` membership check; `ReseedDemoView` scoped to the demo organisation.
- Calculator golden masters and properties; webhook suite (PR #61) plus the additions;
  invoice state pinned; `Decimal` on every money path; money-module coverage gate.
- Branch rules applied on `main` with the `ci` checks required.

### Phase 2: Resilience (ratchet)

- The four jobs and the demo flush against a frozen clock; dry-run and second-run proofs.
- One `resolve_recipient` used by every send site; the recipient matrix as a golden master.
- Stripe redelivery for every event type; checkout amount source decided and tested.
- Concurrency: two sales of the last unit; two bulk runs for the same period.
- Repo-wide coverage floor.

### Phase 3: Journeys and non-functional (ratchet)

- Playwright: setup wizard; signup with drawn signature, approval, welcome email, portal;
  bulk invoice to portal payment (Stripe stubbed on PR, real test mode weekly); coach
  register with siloing; archive and erase.
- `axe` on `/join/<slug>/` and `/p/<token>/`, the two pages the public sees.
- `check --deploy` findings closed or recorded; `DEBUG=False` works with static files served.
- Member list, import and bulk run at 1,000 members: query count asserted with
  `assertNumQueries`, not wall time.

### Phase 4: Contract and ratchet (target)

- Stripe fixtures re-recorded by a scheduled job at the pinned API version and at the
  next one; drift reported as a pull request.
- `mutmut` on the calculator and webhook, score reported.
- Coverage destination 80 percent; floors only rise.
- Stripe Connect, when it lands, inherits the bar: the account id joins the matrix, the fee
  joins the golden masters.
