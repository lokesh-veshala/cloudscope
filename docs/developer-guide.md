# Developer guide

## 1. Choose the workflow

Use the frontend to work on UI behavior. Use the Python test suite to work on calculations. Run the API only to inspect its current stub contract. These are not connected development services yet.

Commands below are run from the repository root in a POSIX shell. Windows contributors should use WSL2 for the shell scripts. Clone the repository URL provided by the maintainer; no customer GitHub remote has been configured as part of this documentation change.

## 2. Frontend

The repository declares Node >=22.13.0 and pnpm 11.25.0 in `package.json`. Use a Node version compatible with that pinned pnpm release as well; the engine lower bound alone is not a tested toolchain matrix.

```bash
node --version
pnpm --version
pnpm install --frozen-lockfile
pnpm dev
```

The portable wrapper selects Vinext and passes port 5173. Use the address printed by the process. In the managed Sites environment, the same script uses a different execution profile; maintainers should use that environment's supervised workflow instead of assuming the portable command applies.

Checks:

```bash
pnpm lint
pnpm build
```

Keep `pnpm-lock.yaml` authoritative. Do not generate an npm lockfile alongside it. No dependency update is required to edit the docs.

### Source layout

| Path | Responsibility |
| --- | --- |
| app/page.tsx | Main views, sample records, resource filters, chart interactions and date controls |
| app/management.tsx | Accounts/alerts explanations and device-local display settings |
| app/globals.css | Layout and responsive styles |
| components/ui/popover.tsx | Existing accessible popover primitive |
| scripts/run-framework.mjs | Managed/portable frontend command routing |
| backend/cost_engine | Calculator, input models and threshold evaluator |
| backend/providers/aws | Catalog adapters |
| backend/schema.sql | Draft PostgreSQL DDL |
| backend/tests | Domain tests and source-level UI contracts |

`db/`, Drizzle configuration and the starter auth helper are frontend-starter artifacts. They are not the Python PostgreSQL persistence layer or a completed application RBAC implementation.

## 3. Python tests without AWS access

Use Python 3.13 for consistency with the Dockerfile:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
```

This suite uses the standard library and injected fake clients; it does not need boto3, FastAPI, Docker or AWS credentials.

Expected baseline: 84 passing checks, including cost/alarm domain tests, AWS
normalization and safety tests, and source-level dashboard contracts. A source
assertion cannot prove browser behavior or live AWS/PostgreSQL integration.

### Exercise the calculator

```bash
PYTHONPATH=backend python3 - <<'PY'
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from cost_engine import CostEngine, PriceInterval, UsageInterval

start = datetime(2026, 9, 1, tzinfo=timezone.utc)
end = start + timedelta(hours=2)
usage = UsageInterval("example-resource", start, end)
price = PriceInterval(start, end, Decimal("0.25"), "test-rate")
result = CostEngine().calculate(usage, [price], require_complete=True)
assert result.amount_usd == Decimal("0.50")
assert result.is_complete
print(result)
PY
```

The rate is a test input, not an AWS quote. Service billing rules must be applied before this generic integration step.

## 4. Run the API stub

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
PYTHONPATH=backend python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Inspect in a second terminal:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/api/v1/quality
```

| Endpoint | Actual behavior |
| --- | --- |
| GET /healthz | Returns process liveness and current time; does not check dependencies |
| GET /api/v1/quality | Describes global guardrails; per-team eligibility is evaluated during collection |

**Do not use /api/v1/quality as a per-team alarm gate.** It describes policy but
does not inspect an account snapshot. Use team evaluation status and durable
alert history for operational decisions.

Account, resource, observed-cost and team routes are implemented for the local
pilot. Team endpoints are under `/api/v1/accounts/{account_id}/teams`; preview
accepts a validated filter expression and resource drill-down takes a stored
team ID. Resource interval detail is clipped to the requested 1–90 day UTC
range. The same resource response includes hourly or daily team cost buckets,
including explicit unresolved and no-observation coverage states. Manual SNS
test delivery is available at
`POST /api/v1/accounts/{account_id}/notifications/test`. Automatic threshold
evaluation runs after successful collections; `GET /api/v1/alerts` exposes its
durable event and SNS delivery history.

`POST /api/v1/accounts/{account_id}/teams` accepts an optional non-negative
`baseline_amount_usd`. For the creation month, threshold evaluation begins at the
team's `created_at` boundary and adds the declared baseline to observed cost.
Alert rows and SNS payloads retain `baseline_amount_usd`, `observed_cost_usd`,
`monitoring_started_at` and `period_basis` as separate audit fields.

The live overview requests `GET /api/v1/accounts/{account_id}/cost-trend` with
`window_minutes` between 60 and 10,080. The endpoint returns fixed five-minute
buckets. A bucket with no priced observations has `amount_usd=null`; clients
must render that value as a gap rather than zero. The query is bounded to 2,016
buckets to keep response size and browser rendering predictable.

## 5. Isolated Compose development

The Compose file runs the API, PostgreSQL and a frontend development server. It includes example database credentials, publishes API port 8001 and web port 5173 on the configured bind address (loopback by default), and does not configure TLS or authentication. Run it only on an isolated development machine with appropriate inbound restrictions. See [live integration status](live-integration-status.md) for update instructions and calculation exclusions.

```bash
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 api
```

The API waits for the database health check, initializes the current schema,
and persists live inventory, observations, schedules and team definitions.

To apply the draft DDL to a **new disposable database**, explicitly:

```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cloudscope -d cloudscope < backend/schema.sql
```

This is not a migration system or an idempotent initialization command. Reapplying it to an initialized database can fail; do not use it against an existing customer database. The extension statement may require additional database privileges.

Stop without deleting the named database volume:

```bash
docker compose down
```

Compose startup, package installation on a clean machine, and schema execution were not run during this documentation update. These instructions are derived from the checked-in definitions, not a fresh installation certification.

## 6. Contribution and verification

1. Identify whether the change affects fixtures, domain logic, an adapter or a production integration.
2. Add a focused regression test that fails for the original defect.
3. Use Decimal strings for money inputs. Do not silently fill missing prices.
4. Run the Python suite and frontend lint/build for affected surfaces.
5. Review `git diff --check` and inspect the files being staged.
6. Update the architecture/status documentation if a runtime connection or public contract changes.

Before committing, run `git status --short`. The current ignore rules do not comprehensively cover Python virtual environments and bytecode. Do not stage `.venv`, private keys, credentials, database dumps or generated caches.

### Manual UI acceptance checklist

- Open Cloud Accounts, Alert History and Settings from navigation.
- Open workspace and notification popovers; close with Escape and outside click.
- Confirm focus is usable with Tab and Shift+Tab.
- Hover, focus and tap each plotted date; compare the tooltip value to its fixture.
- Apply a custom range without data; verify that all financial views withhold figures.
- Reject reversed or greater-than-90-day ranges; restore the demo snapshot.
- Filter resources by service, region and search text; inspect the empty state.
- Save and load table spacing on this device.
- Refresh; confirm no live AWS success is claimed.
- Repeat on narrow screens and with browser zoom.

Record browser/version and results in the PR. These checks have not been executed as browser tests in the current baseline.

## 7. Extending the backend

A new provider should implement the contract in `backend/providers/base.py`, but that abstract class alone does not supply orchestration. Start by defining resource identity, usage units, timestamp semantics, required price dimensions and unavailable-data behavior.

For every new service, provide fixtures for pagination, access denial, throttling, empty results, partial inventory, missing rates, ambiguous rates and changes at interval boundaries. Add a database-backed test before claiming persistence and an AWS sandbox test before claiming live integration.

Do not wire customer alarms to the current API quality stub or MemoryAlarmState.
