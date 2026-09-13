# CloudScope

Cloud resource consumption and team cost governance.

**Status: development prototype.** The hosted dashboard uses sample data. It is not connected to AWS, the Python API, or PostgreSQL. No SNS messages are published. Do not use its figures for chargeback, budget enforcement, or production decisions.

## Sample dashboard and features

These screenshots show the running frontend with illustrative September 2026 data. Costs, coverage percentages and alert states are sample values. Overview and team cards demonstrate separate scenarios, not a reconciled ledger.

### Cost overview

Estimated spend, active resources, Spot savings, cost by service and monthly-limit progress. Select a chart point to inspect its date and amount.

![CloudScope sample overview with cost chart tooltip and service breakdown](docs/images/cloudscope-overview.jpg)

### Team limits

Sample teams demonstrate normal usage, an 80% warning and a monthly-limit overrun. Cards show remaining budget or excess spend and 80%/100% thresholds. No SNS message or Lambda invocation occurred.

![CloudScope sample team limits with normal, warning and exceeded states](docs/images/cloudscope-team-limits.jpg)

| Feature | Available in the prototype | Remaining integration |
| --- | --- | --- |
| Overview | Sample KPIs, service breakdown and chart tooltips | Live cost aggregates |
| Resources | Search by name, ID or team; service and region filters | Native AWS inventory collection |
| Team limits | Sample progress and threshold states; separate in-memory Python evaluator | Persisted configuration, scheduled evaluation and SNS delivery |
| Data quality | Sample coverage and freshness presentation | Measured pricing and collection health |
| Cloud accounts | Management view explaining integration status | Roles Anywhere onboarding and connection tests |
| Alert history and notifications | Empty-state views | Durable alert history and SNS-to-Lambda integration |
| Settings | Device-local display preferences | Shared configuration persistence |
| Date selection | Ordered ranges up to 90 days; unavailable state outside the demo range | Historical data queries |
| Cost engine | Decimal interval calculations and fixture-tested EC2 pricing adapters | Verified live catalog and complete service billing dimensions |

### Run the sample on a development VM

Use the Node and pnpm versions described in the [developer guide](docs/developer-guide.md#2-frontend). No AWS credentials are required.

```bash
git clone https://github.com/lokesh-veshala/cloudscope.git
cd cloudscope
pnpm install --frozen-lockfile
pnpm dev
```

For a container or trusted remote development VM, bind the development server explicitly:

```bash
CLOUDSCOPE_DEV_HOST=0.0.0.0 pnpm dev
```

Open the address printed by the server. For a remote VM, forward its development port over SSH instead of exposing the development server publicly. In the portable workflow the default port is 5173:

```bash
ssh -L 5173:127.0.0.1:5173 YOUR_USER@YOUR_VM
```

Then open `http://localhost:5173` on your computer. Explore **Overview**, select a chart point, search for `hpc` in **Resources**, and open **Team limits**. Only the default September 1–13, 2026 date range has a complete demo snapshot.

The [hosted sample](https://cloud-governance-control.karankiran422.chatgpt.site) is access-restricted; repository visitors can use these screenshots or run the frontend locally.

**Testing against AWS is not available yet.** Cloning and starting this frontend does not discover your resources. Collectors, temporary-credential onboarding, pricing ingestion, persistence and frontend/API integration still need implementation. Do not enter AWS keys into the demo or treat its values as account spend. See the [release gates](docs/production-readiness.md).

## Documentation

| Document | Audience | Contents |
| --- | --- | --- |
| [Architecture](docs/architecture.md) | Engineers and reviewers | Current implementation, target boundaries, data model, cost calculations, failure modes |
| [Developer guide](docs/developer-guide.md) | Contributors | Setup, tests, API inspection, code layout, extension workflow |
| [Customer guide](docs/customer-guide.md) | Evaluators and account owners | Dashboard walkthrough, limitations, pilot prerequisites |
| [Production readiness](docs/production-readiness.md) | Maintainers and operators | Known defects, acceptance gates, deployment and incident requirements |

## What is implemented

- React/TypeScript dashboard with sample resource filters, date controls, chart tooltips, and management views.
- Python interval calculator using Decimal arithmetic.
- EC2 Linux/shared On-Demand catalog adapter and Spot history adapter, exercised with fixtures.
- In-memory threshold evaluator.
- Draft PostgreSQL schema and a two-service Compose definition.

The target system adds native AWS inventory collectors, usage ingestion, historical pricing persistence, server-side authorization, durable scheduling and SNS delivery. Those components are not present merely because their tables or interface names exist.

## Quick verification

From the repository root, using Python 3.13:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
```

The current suite contains 17 domain tests and six source-level UI checks. No AWS account is required. The UI checks are not browser tests.

See the [developer guide](docs/developer-guide.md) for frontend and API setup. The existing Compose file is for isolated local development only; it includes example credentials and exposes an unauthenticated API.

## Repository provenance

The frontend was scaffolded with a Sites/Vinext starter. The current UI is React on Vinext/Vite, not a standalone React SPA, and the hosted frontend is separate from the Python container. Existing `.openai/hosting.json` belongs to the original hosted project; contributors must not reuse that identity to publish a different project.

No current AWS price list is bundled or verified. Numbers in fixtures are test inputs, not price quotations.
