# Production readiness and release gates

## Browser capture observation — September 13, 2026

The development preview reported a React hydration mismatch around the cost chart's SVG `title` content. React recovered on the client. README screenshots show the rendered interface after dismissing that development overlay. Chart point selection and Resources/Team limits navigation were exercised during capture; this is not a clean initial-load or production browser test. Investigate the server/client SVG rendering before release.

## Release decision

Current decision: **not approved for production cost governance**.

The UI is a prototype and the backend modules are a starting point. This document records blockers rather than assigning unsupported “production-ready” labels.

## Known risks from source review

| Priority | Finding | Consequence | Required action |
| --- | --- | --- | --- |
| P0 | API quality response is hardcoded eligible=true | A consumer could enable alarms without evidence | Return explicit unconfigured status until computed readiness exists |
| P0 | No API authentication or account/dashboard authorization | Financial data cannot be safely exposed | Implement and test server-side identity and RBAC |
| P0 | In-memory alert state | Restart loss and concurrent duplicates | Transactional persisted evaluation and outbox |
| P0 | No live usage ingestion or price persistence | No customer cost can be established | Implement collectors, reconciliation and provenance |
| P1 | Generic hourly cost integration lacks service billing rules | Rates × seconds can differ from billable usage | Implement service-specific dimensions and charging rules |
| P1 | No finite-number validation; duration uses float conversion | Invalid or precision-sensitive inputs | Decimal/type checks, integer duration arithmetic and regression tests |
| P1 | Invalid-input paths do not consistently clear confirmation state | A rejected observation can leave an earlier confirmation active | Define ordered state transitions for all rejection paths |
| P1 | Source IDs are not retained in result segments | Computed amounts are not independently auditable | Store calculation inputs and a per-segment ledger |
| P1 | Table constraints allow closed temporal overlaps | Historical ownership can be double counted | Temporal exclusion/reconciliation constraints |
| P1 | Example Compose credentials and exposed unauthenticated API | Unsafe deployment defaults | Secret injection, restricted binding, TLS and ingress controls |
| P1 | No tenant isolation, migrations, backup or retention implementation | Operational and recovery risk | Implement, exercise and document restore/upgrade paths |

These findings are documented, not fixed by the documentation change.

## Required acceptance evidence

### Financial calculations

- Independent reference cases for every supported service, OS, purchase model, tier and region.
- Missing price, missing usage and failed discovery tested separately.
- Exact month and timezone boundaries, leap-day cases and partial-period requests.
- Tag changes and shared-resource allocation reconciled without double counting.
- Historical rates retained across refresh and outage recovery.
- Deterministic results with explicit rounding, input bounds and calculation versions.
- Reconciliation output states exclusions and error tolerance; no unsupported “zero error” claim.

### Alarms

- Distinct observations required, with stale/out-of-order/replayed inputs handled consistently.
- Failed observation or invalid input cannot preserve an inappropriate confirmation.
- Concurrent workers produce one logical event per approved deduplication key.
- Restart preserves confirmation and event state.
- Limit/filter changes have explicit version semantics.
- Crash before publish, during publish and after SNS acceptance tested.
- Repeated SNS delivery does not repeat customer side effects.
- Blocked evaluations are visible and distinguishable from “under budget.”

### Security

- Cross-account/dashboard parameter tampering is denied server-side.
- Viewer permissions cannot be elevated by client state.
- Cloud credentials never enter browser state or logs.
- Certificate expiration/revocation and temporary credential refresh exercised.
- SNS topic restrictions verified.
- Read-only inventory permissions are separated from any customer enforcement workflow.
- Dependency and container scanning performed on the locked release.
- Audit records exist for account, role, filter, limit and destination changes.

### Reliability and capacity

The planned 100-account/90-day target needs an agreed load profile: resources per account, region count, dashboards, concurrent users, metric density and query ranges. Record p50/p95/p99 API latency, error rate, queue delay and database utilization against that profile.

No API latency SLO has been measured. Decide numerical acceptance thresholds before benchmarking, not after observing results. A single-process cost-loop benchmark is not an application load test.

Test throttling, expired credentials, partial pagination, worker death, duplicate scheduling, database restart and an extended AWS outage. Failed or incomplete inventory must not mark unseen resources as deleted.

### Frontend

Run browser tests for navigation, popovers, keyboard operation, chart tooltips, date validation and empty/error states. Verify mobile and zoom behavior. Source-level string assertions are useful regression checks but cannot replace these tests.

## Operational runbook requirements

Before pilot release, identify an operator and an escalation path for each component.

| Symptom | Initial checks | Safe behavior |
| --- | --- | --- |
| Collection age increasing | Credentials, source errors, queue backlog | Show stale state; withhold affected financial alerts |
| Price match unavailable | Full lookup dimensions and catalog provenance | Preserve gap; do not insert guessed or zero price |
| Repeated notifications | Event ID, outbox attempts, consumer idempotency | Disable publication pending diagnosis; retain evidence |
| Cost jumps after tag change | Temporal attribution and filter version | Compare ledgers before altering ownership |
| Database recovery required | Backup age, restore point and schema revision | Restore to isolated environment before cutover |

Rollback must account for both application and schema versions. Backups require a tested restore, not just successful backup jobs. Retention must preserve calculation baselines and auditability. Runbooks must never advise deleting historical price or tag records merely to clear an error.

## Evidence available now

The baseline suite has 23 passing checks: 17 domain tests and six source-contract UI checks. Prior frontend builds and lint passed. No live AWS, clean-machine installation, schema migration/restore, concurrency, SNS end-to-end or browser acceptance evidence is included.

The earlier validation report is historical. Its broad “Pass” labels and “production-quality” wording were withdrawn. This document and the architecture's implementation boundaries govern release discussions.
