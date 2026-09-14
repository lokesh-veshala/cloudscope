# Architecture

Status: prototype implementation with a production target  
Scope: repository state following the dashboard-interaction update

## 1. System boundary

CloudScope is intended to measure infrastructure consumption across cloud accounts, associate costs with team-owned dashboards, and publish threshold notifications. It is not intended to reproduce an invoice or terminate resources.

There are two distinct systems to understand:

1. **Current implementation:** a demonstration UI, independently runnable Python modules, an API stub, and a draft database schema.
2. **Target deployment:** an on-premises service with a local database, asynchronous AWS collection, authenticated dashboards, and a durable notification pipeline.

The target design is not a description of currently deployed functionality.

### Current runtime

| Component | Entry point | Dependencies actually used | Important boundary |
| --- | --- | --- | --- |
| Dashboard | `app/page.tsx` | React state, inline fixtures, SVG chart, Radix popovers | No calls to the Python API |
| Management views | `app/management.tsx` | React state, browser localStorage | Accounts and alerts are explanatory views |
| API | `backend/api.py` | FastAPI | Two endpoints; no authentication or database access |
| Calculator | `backend/cost_engine/pricing.py` | Python standard library | Called by tests, not wired into the UI/API |
| Threshold evaluator | `backend/cost_engine/alarms.py` | In-memory state | Not durable or safe for concurrent workers |
| Pricing adapters | `backend/providers/aws/pricing_catalog.py` | Injected AWS-compatible clients | Not scheduled; tests supply fake clients |
| PostgreSQL | `docker-compose.yml` | Database container and named volume | Schema is not applied automatically |

The hosted dashboard does not run the Python Dockerfile. Starting Compose does not connect the hosted dashboard to the API.

## 2. Target deployment

The planned on-premises deployment has five responsibilities:

| Responsibility | Component | Reads | Writes |
| --- | --- | --- | --- |
| Interactive requests | Authenticated REST API | Authorized local aggregates and resource records | Account configuration, dashboards, limits, audit events |
| Work dispatch | Scheduler | Account configuration and due times | Durable collection jobs |
| AWS collection | Bounded worker pool | Native inventory, usage and pricing APIs | Observations, usage buckets, price versions |
| Accounting | Calculation workers | Usage, state/tag history, price intervals | Versioned cost intervals and aggregates |
| Notification | Outbox publisher | Eligible pending threshold events | SNS and delivery-attempt records |

Dashboard requests should query local persisted data. They must not wait for a fan-out of AWS API calls.

Target capacity is 100 accounts and 90 days of history. These are sizing goals, not measured limits. The existing compute-only benchmark does not establish database throughput, AWS collection throughput, or API latency.

### Collection cadence

| Work type | Proposed cadence | Reason |
| --- | --- | --- |
| EC2 state | 2 minutes | Detect lifecycle changes |
| General inventory and tags | 5 minutes | Lower discovery overhead |
| Usage metrics | 5 minutes where source resolution permits | Separate metric freshness from inventory freshness |
| Spot history | 2–5 minutes and after relevant changes | Preserve historical rates |
| On-Demand catalog | Daily, plus change-triggered refresh | Refresh current catalog without losing older versions |
| S3 storage | 15–60 minutes, subject to source availability | Polling faster does not improve source resolution |
| UI data refresh | 10–30 seconds | Read local data without invoking collectors |

All intervals above remain unimplemented. A collector must record source observation time, collection time, and successful coverage separately. A fresh HTTP response cannot make stale upstream data fresh.

## 3. Resource identity and temporal ownership

The common model carries provider, account, region, availability zone, normalized resource type, provider-specific ID, state, and metadata. Provider-specific attributes belong in metadata; dashboard APIs should not expose an AWS-only hierarchy.

The draft `resources` uniqueness key is account + provider resource type + provider resource ID. Before supporting all services, review regional ID collisions and include region or canonical ARN as appropriate. A naming convention is not an identity guarantee.

State and tag tables use effective intervals. An ownership change from QA to HPC must split cost attribution at the change boundary. Historical cost must not be reassigned using the resource's current tag.

The draft schema enforces one open state row and one open value per tag key. It does **not** prevent overlapping closed intervals. The ingest transaction and database constraints need to establish that invariant before calculating historical ownership.

Polling introduces uncertainty. If a resource is running at 10:00 and stopped at 10:02, the exact stop time is unknown without a more authoritative event. Do not present either observation time as an exact billing transition. Preserve an uncertainty window and expose its effect on estimates.

## 4. Cost calculation contract

`CostEngine.calculate(usage, prices, require_complete=False)` operates on a single usage interval and a supplied price timeline.

- Usage and prices must have timezone-aware timestamps.
- Intervals are half-open: start is included, end is excluded.
- Inputs must have positive duration and nonnegative quantity/rate.
- Prices are sorted and overlaps are rejected.
- Each intersection contributes duration / 3600 × quantity per hour × rate.
- Missing price intervals are returned explicitly.
- Strict mode raises `PricingCoverageError` when coverage is incomplete.
- Display rounding is separate from accumulation.

Aware timestamps are accepted; the model does not force UTC normalization. The application should canonicalize timestamps before storage and deduplication.

Example, using illustrative rates:

| Usage segment | Rate per hour | Cost |
| --- | ---: | ---: |
| 11:00–12:00 | 0.20 USD | 0.20 USD |
| 12:00–15:00 | 0.24 USD | 0.72 USD |
| 15:00–16:00 | 0.18 USD | 0.18 USD |
| Total | | 1.10 USD |

The calculator is a generic integrator, not an EC2 billing specification. Minimum charges, per-second eligibility, operating-system exceptions, licensing, capacity reservations, discounts, credits, taxes, transfers and service-specific billing rules are not implemented.

### Precision and coverage limits

Amounts are accumulated with a 38-digit Decimal context. Duration currently passes through `timedelta.total_seconds()`, which returns a binary float, before conversion to Decimal. That must be replaced by integer microsecond arithmetic before claiming exact subsecond accounting.

`display_amount` uses the ambient Decimal rounding context. Explicit rounding and precision are required for reproducible exports across callers. Models also need explicit type and finite-number checks for NaN and infinity.

Current coverage is covered seconds divided by requested seconds for **one supplied usage interval**. It is not discovery completeness, service-dimension coverage, or statistical confidence. Missing usage can produce a deceptively complete result. A dashboard-level coverage measure needs a defined denominator across expected resources, time and billable dimensions.

The result currently contains amount, seconds and missing intervals. It does not retain a per-segment price-source ledger; passing a source ID into a price object is not sufficient for a later audit.

## 5. Pricing adapters

### EC2 On-Demand

`fetch_linux_shared` calls `get_products` with regionCode, instanceType, operatingSystem=Linux, tenancy=Shared, preInstalledSw=NA and capacitystatus=Used. It paginates, parses OnDemand terms, selects USD/Hrs dimensions and requires exactly one candidate.

That is a deliberately narrow adapter. Windows, dedicated tenancy, other licenses and service families need explicit support. The parser does not yet validate tier boundaries, applicable usage ranges, future effective dates, all returned attributes or finite price values. A single returned candidate alone does not prove billing applicability.

### Spot

`fetch` requests a specified zone, instance type, product description and time window, then paginates results. `to_intervals` converts change points to intervals. When no rate exists at the requested start, the leading period stays unresolved.

The caller must supply a homogeneous price series. Conflicting duplicate timestamps and mixed instance/zone series are not rejected today. Before production, validate series identity and historical completeness and persist old records; a present-day rate must never backfill unknown historical usage.

### Other services

EBS, EFS, FSx, RDS, S3, public IPv4, NAT and load balancer cost models are still planned. Each requires separately defined billable units and evidence sources. An hourly adapter must not be reused for GB-month storage, tiered requests or throughput without an explicit conversion contract.

No scheduled price refresh, complete instance-type inventory, price checksum generation or live catalog reconciliation exists.

## 6. Monthly limits and notification semantics

### Implemented live team scope

The pilot stores a team definition and monthly USD limit in
`pilot_team_limits`. A filter is a bounded JSON expression tree: a group uses
`AND` or `OR`, and leaves compare AWS tag keys with `EQUALS`, `NOT_EQUALS`,
`EXISTS`, or `NOT_EXISTS`. The API accepts no more than 20 leaves and four
nested levels. Keys and values are always SQL bind parameters; only validated
operators and fixed JSONB expressions enter generated SQL.

Preview and resource drill-down evaluate the expression against the latest
`resources.metadata.tags`. Cost attribution evaluates the same expression
against `observed_costs.tags`, which is the tag snapshot stored for that usage
interval. A later tag change therefore does not move earlier observed cost to
the new owner. Team definitions are account-scoped in every lookup.

The current UI creates flat ALL/ANY rule sets; the persisted representation and
backend evaluator also support nested groups for future UI composition. Team
totals can overlap when filter definitions overlap and must not be summed as an
account total.

Team deletion is a local soft delete. It removes the definition from active
queries while retaining its record for later audit and recovery work; it never
calls an AWS resource mutation API.

The account overview uses a separate bounded trend query for interactive
operations. It returns five-minute buckets for a caller-selected rolling window
between one hour and seven days. The backend emits every bucket, including
`NO_OBSERVATION`, `UNRESOLVED`, and `PARTIAL` states. `amount_usd` remains null
when no priced input exists, allowing the UI to break the line instead of
manufacturing a zero-cost point. The seven-day bound limits the response to
2,016 buckets.

The domain policy carries dashboard ID, limit version, amount, threshold, period boundaries, maximum data age and required confirmation count.

The evaluator checks numeric bounds, complete supplied pricing coverage, freshness, evaluation period and duplicate state. It compares cost / limit × 100 with the threshold. Two qualifying observations with strictly increasing observation times produce READY by default. Repeating the same snapshot does not advance confirmation.

The deduplication key contains dashboard, period start, limit version and threshold. Limit changes therefore permit a new event. Defaults are a ten-minute maximum age and two confirmations.

READY creates only an in-memory marker. It does not insert a database event, publish SNS, or invoke Lambda. Restarting loses the state. Concurrent evaluation is not protected.

The live pilot exposes a separate manual notification test. It records a
`PENDING` delivery, publishes `CLOUDSCOPE_NOTIFICATION_TEST` to the SNS topic
registered for that account, and records `PUBLISHED` or `FAILED`. The payload
contains a stable local event ID and explicitly states that it is not an
automatic threshold alert. A subscribed Lambda is invoked by SNS; CloudScope
does not request `lambda:InvokeFunction`. This test path is not evidence that
cost inputs are eligible for automatic limit evaluation.

### Required durable delivery design

Evaluate against a consistent input revision. In one database transaction, update confirmation state, insert the uniquely keyed threshold event, and enqueue an outbox entry. Commit before contacting SNS. A separate publisher claims entries, records attempts and retries transient failures with bounded backoff.

A crash after SNS accepts a message but before local acknowledgement can cause retransmission. Database deduplication cannot guarantee exactly-once delivery across that boundary. Include a stable event ID and require idempotency in downstream customer Lambda handlers.

Incomplete or stale inputs should produce a visible blocked evaluation, not a budget breach and not a silent success. Blocking reduces some false positives but can delay genuine warnings. Neither zero false positives nor zero missed alerts is guaranteed.

## 7. Persistence and query design

| Draft table | Purpose | Missing implementation |
| --- | --- | --- |
| cloud_accounts | Provider identity, connection and collection schedule | Secret/certificate lifecycle and RBAC |
| resources | Latest inventory | Deletion reconciliation and full service coverage |
| resource_state_history | Observed state intervals | Transitions hidden between polls |
| resource_tag_history | Observed ownership intervals | Transitions hidden between polls |
| pricing_catalog | Price dimensions and effective versions | Ingestion, provenance and invalidation |
| cost_intervals | Versioned calculated amounts | Materialization and aggregation |
| pilot_team_limits | Account-scoped filter expression and limit | Update/delete workflow, RBAC and durable policy versions |
| threshold_events | Unique threshold records | Transactional evaluator and publisher |
| collection_jobs | Durable scheduled work with leases and retries | Concurrent worker-pool capacity validation |

There are no user/role/grant tables, usage metric tables, audit-log table,
outbox-attempt table, or schema migration runner. The JSONB team-filter
language is implemented, but authorization and query-plan validation at target
cardinality remain release gates.

Proposed indexes are starting points. Validate query plans against representative resource/tag cardinality. Retention must retain baseline state/tag/price records needed to calculate intervals crossing the retention boundary; deleting everything older than 90 days is incorrect.

## 8. Security and operations

The production collector should use temporary role credentials via IAM Roles Anywhere. Private keys remain on the collector host, outside source control and browser forms. Credential refresh, revocation, expiration and hostname/network restrictions need operational ownership.

Inventory permissions should be read-only. SNS publication must be scoped to explicitly approved topics and separated from customer automation permissions. The application should not obtain permission to launch, stop or terminate customer resources.

Server-side authorization must constrain account and dashboard queries before applying user filters. A role label in the current sidebar has no authorization effect. Shared-resource costs need a documented allocation policy; independent dashboard totals can overlap and must not be summed as an account bill.

See [production readiness](production-readiness.md) for concrete release blockers.
