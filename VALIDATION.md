# V1 validation report

## Live account integration update

The first test-account integration includes local PostgreSQL account
registration, IAM Roles Anywhere profile execution, identity/account validation,
V1 inventory collectors, resource state/tag history, and strict Linux/shared EC2
On-Demand catalog matching. Partial service failures are reported explicitly.

Automatic 80%/100% evaluation now runs after each successful collection and
publishes eligible events to the account's approved SNS topic. It remains
fail-closed when complete calendar-month usage and price evidence is unavailable.
Linux/shared Spot instances may display an
explicit 42%-discount fallback against exact On-Demand pricing, but that value is
classified as estimated and is never alarm-eligible. Windows and non-default
tenancy remain unresolved.

Validation performed locally: 82 Python tests passed, ESLint passed, and the
Vinext production build passed. PostgreSQL/AWS end-to-end validation must run on
the test VM because this build environment has neither Docker nor runtime AWS
credentials.

## Interaction update — supersedes readiness claims below

This remains a demo, not a production-ready V1. Management navigation, workspace and notification popovers, device-local table spacing, custom date selection, and pointer/focus/tap chart tooltips are implemented. Unknown date ranges explicitly withhold figures; the fixture contains only one complete snapshot.

Validation: 17 existing domain tests and 6 source-level UI contract checks pass; ESLint passes. The UI checks inspect source wiring, not actual browser behavior. No browser/end-to-end, live AWS, current price catalog, PostgreSQL concurrency, SNS delivery, or 100-account scalability validation was performed in this update. Prior "Pass" labels for accessibility, performance, security, and schema readiness must not be treated as production certification.

The earlier "production-quality vertical slice" description is withdrawn.
Test-account onboarding and inventory collection now exist, but server-side RBAC,
account switching, interval-backed date aggregation, and alert delivery remain
unimplemented. The overview remains a demo snapshot.

Validation date: 2026-09-13 UTC

## Functional requirements

| Requirement | Status | Evidence |
|---|---|---|
| Responsive, fast dashboard | Pass | Single client route; memoized resource filtering; no network waterfall for first paint |
| Overview, resource, team-limit, quality views | Pass | Interactive navigation in `app/page.tsx` |
| Resource search and service/region filters | Pass | Case-insensitive search and composable filters |
| EC2 On-Demand exact price lookup | Pass | Strict six-dimension AWS catalog adapter and ambiguity tests |
| Historical Spot interval pricing | Pass | Timestamped Spot adapter plus segmented interval test |
| Decimal interval cost calculation | Pass | 38-digit calculation context; exact-second unit tests |
| Missing price safety | Pass | Partial coverage result; strict mode raises; alert blocked |
| Configurable limit versions and thresholds | Pass (domain) | Immutable policy version and threshold-specific key |
| False-positive-resistant alarms | Pass (domain) | Complete-coverage gate, freshness gate, double confirmation, deduplication tests |
| Correct historical team ownership | Pass (schema) | Effective-dated `resource_tag_history` |
| SNS → Lambda delivery | Implemented, VM acceptance required | Durable event, stable ID, publisher, bounded retry and delivery history; subscribed Lambda remains customer-managed |
| IAM Roles Anywhere | Ready for integration | Credential boundary is specified; requires customer certificate/profile ARNs |
| EC2, EBS, EFS, FSx, RDS, S3, EIP, ALB/NLB, NAT inventory | Adapter contracts ready | AWS account integration and service collectors remain the next implementation slice |
| RBAC | Schema/API boundary planned | Identity provider and organization roles are not configured in this demonstration |
| 90-day retention | Schema ready | Scheduled partition cleanup/downsampling job is not yet wired |

## Non-functional requirements

| Requirement | Status | Validation |
|---|---|---|
| Financial determinism | Pass | Decimal-only domain; no binary floats in backend |
| Idempotency | Pass | Unique threshold event key includes limit version |
| Fail closed | Pass | Partial, stale, future-dated, invalid and ambiguous inputs are blocked |
| Auditability | Pass (model) | SKU, rate code, effective/fetch time, checksum and calculation version retained |
| Accessibility | Pass (static review) | Semantic buttons/table, focusable controls, labelled mobile navigation, reduced-motion rule |
| Responsive behavior | Pass (CSS review) | Desktop, tablet and mobile breakpoints; overflow-safe tables |
| Horizontal scalability | Architecture ready | Provider/worker boundaries defined; queue implementation requires database job claim logic |
| 100-account target | Not load-tested | Requires representative connected-account fixtures and AWS throttling tests |
| 2-minute collection | Implemented, VM acceptance required | Default cadence 120 seconds; durable scheduler and editable account cadence |
| Security | Pass for domain | No static AWS credentials; no mutation APIs; deployment secrets still require customer configuration |

## Performance evidence

- Production build completes successfully.
- Page-specific JavaScript is 25,717 bytes uncompressed after replacing the charting dependency with an accessible inline SVG (down from 351,182 bytes).
- Complete built artifact is 1.7 MiB before transport compression.
- A local deterministic benchmark completed 100,000 single-interval calculations in 0.436 seconds (approximately 229,000/second). This is a compute-only benchmark, not an AWS or database throughput claim.

## Test result

`82/82` unit and source-contract tests pass. The implementation retains Decimal
arithmetic for every financial threshold and amount.

## Release boundary

This is a production-quality vertical slice, not a claim that all AWS collectors
are complete. Automatic alerting is active but fail-closed: incomplete teams
produce BLOCKED evaluations, not SNS events. VM acceptance must verify schema
migration, two consecutive eligible collections, SNS publication and subscriber
idempotency before production reliance.
