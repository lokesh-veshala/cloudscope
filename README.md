# CloudScope

Cloud resource consumption and team cost governance.

**Status: development prototype.** The hosted dashboard uses sample data. It is not connected to AWS, the Python API, or PostgreSQL. No SNS messages are published. Do not use its figures for chargeback, budget enforcement, or production decisions.

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
