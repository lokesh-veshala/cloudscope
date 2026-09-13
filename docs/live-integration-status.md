# Read-only integration status

The default screen reads inventory and observed EC2 subtotals from the local API.
The sample dashboard is accessible separately. AWS records, credentials and
certificates are not packaged with the application or committed to Git.

## What the pilot calculates

Each manual collection stores normalized inventory and changes in state and tags.
Two observations no more than ten minutes apart can produce a compute interval
when the instance remains running, pricing and ownership dimensions match, and
the prior price is valid and fresh. This assumes continuous operation between
polls. It cannot detect every transition between observations.

Amounts use decimal arithmetic. UTC month boundaries clip intervals when querying
subtotals. No usage is reconstructed before the first observation. Gaps, changed
tags, changed rates and unsupported dimensions produce unresolved records, not
zero-dollar costs. Spot fallback assumes 42% off the matching On-Demand rate and
is visibly separated from other estimates.

Team limits persist in PostgreSQL and select interval ownership by exact tag key
and value. They display partial observed subtotals. They do not evaluate warning
thresholds or publish notifications: complete cost coverage is not available.

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

In the GUI, select the registered account, test its connection, then collect twice
within ten minutes. Review service counts, unresolved intervals and the observed
EC2 subtotal. Add a local monthly limit using a tag that exists in your account.
No additional AWS write permissions are required.

## Verification and remaining work

The unit suite covers collector request validation, identity checks, profile
failures, partial service failures, pricing and conservative interval calculations.
Frontend lint and production compilation are checked separately. This release has
not been exercised against PostgreSQL or a live AWS account in the development
environment; deployment acceptance must verify those integrations.

Multi-service usage costs, historical Spot integration, automatic scheduling,
retention cleanup, RBAC, TLS termination and notification delivery remain
incomplete. The Compose frontend is a development server and the API has no
authentication. Keep this pilot on a restricted network. A read-only collector
does not establish that its deployed IAM policy is read-only; review that policy
separately.
