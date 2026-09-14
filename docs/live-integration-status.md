# Read-only integration status

The default screen reads live observed KPIs, service subtotals, a cumulative
trend, highest-cost resources, inventory and local limits from the local API.
The sample dashboard is accessible separately. AWS records, credentials and
certificates are not packaged with the application or committed to Git.

## What the pilot calculates

Each manual or scheduled collection stores normalized inventory and changes in state and tags.
Two observations no more than ten minutes apart can produce a compute interval
when the instance remains running, pricing and ownership dimensions match, and
the prior price is valid and fresh. This assumes continuous operation between
polls. It cannot detect every transition between observations.

Amounts use decimal arithmetic. Supported EBS provisioned dimensions are
included; EFS and FSx currently contribute storage-only partial intervals. The
exact coverage and proration rules are documented in
[Storage cost models](storage-cost-models.md). UTC month boundaries clip
intervals when querying subtotals. No usage is reconstructed before the first
observation. Gaps, changed tags, changed rates and unsupported dimensions
produce unresolved records, not zero-dollar costs. Spot fallback assumes 52%
off the matching On-Demand rate and is visibly separated from other estimates.

Team limits persist in PostgreSQL and select interval ownership by exact tag
rules. Every successful collection evaluates 80% and 100% thresholds. In the
creation month, the policy uses a declared baseline plus complete and fresh
observations after team creation. Partial observed subtotals remain visible but
cannot advance confirmation or publish SNS. Two distinct qualifying
observations are required, and each team/month/version/threshold is emitted once.

## Deployment update

Preserve local source edits before pulling. The port-8001 and CloudWatch fixes are
included, so do not automatically reapply older versions of those files:

```sh
git stash push -m cloudscope-before-live-update -- docker-compose.yml app/management.tsx backend/providers/aws/collector.py
git pull --ff-only
sudo docker compose up -d --build
sudo docker compose ps
```

Keep the existing local .env and certificate/config mounts. Do not run
`docker compose down -v`: that deletes the database volume. API host port is 8001;
the internal port stays 8000. The web service uses a temporary .vinext directory
so a stopped container's development lock is not reused on restart.

In the GUI, select the registered account and test its connection. Choose a 2,
5 or 10-minute cadence and enable or update automatic collection; two minutes is
the default for new accounts. Existing account rows retain their saved cadence
until **Update cadence** is selected. The scheduler keeps durable jobs, blocks
overlapping collection for one account, retries failures three times with backoff,
and recovers expired leases. The Job history button shows the last 20 attempts.
Review service counts, unresolved intervals and the observed EC2/storage
subtotals in the default live dashboard. Date selection accepts one to 90
inclusive UTC days and does not fill unobserved time with zero. The first
storage-aware collection can record a metadata transition;
allow two later stable collections before assessing coverage. Add a local
monthly limit using a tag that exists in your account. No additional AWS write
permissions are required.

## Verification and remaining work

The unit suite covers collector request validation, identity checks, profile
failures, partial service failures, pricing and conservative interval calculations.
Frontend lint and production compilation are checked separately. This release has
not been exercised against PostgreSQL or a live AWS account in the development
environment; deployment acceptance must verify those integrations.

RDS, S3 and network usage costs, complete EFS/FSx billing dimensions,
historical Spot integration, concurrent worker-pool scaling, retention cleanup,
RBAC and TLS termination remain incomplete. Automatic SNS delivery requires VM
acceptance testing with a complete eligible team snapshot. The Compose
frontend is a development server and the API has no authentication. Keep this
pilot on a restricted network. A read-only collector does not establish that
its deployed IAM policy is read-only; review that policy separately.
