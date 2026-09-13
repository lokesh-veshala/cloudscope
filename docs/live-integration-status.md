# Read-only integration status

This working increment adds a live inventory panel to Cloud Accounts. It reads
the account-scoped resource endpoint; it does not copy AWS inventory into source
files or use the demo records as a fallback. Selecting an account clears the
previous results, and late responses from earlier selections are ignored.

Resource search includes IDs, names, services, regions and collected tags. The
table distinguishes matched EC2 hourly rates, assumed Spot rates and unavailable
pricing. It deliberately does not sum hourly rates into month-to-date spend.

Connection validation now handles an unreadable/missing profile as a structured
failure and validates CloudWatch request arguments against the installed SDK in
a regression test. Identity mismatches stop subsequent connection probes, and
each collection checks the account identity again before inventory requests.
Partially retrieved service pages are not persisted when that service fails.

## Still incomplete

- Main Overview, Resources, Team Limits and Data Quality routes remain demo views.
- Complete historical usage, multi-service cost calculation, scheduling and
  durable limit evaluation are not connected.
- HTTPS and API authentication must be configured before broader access.
- No SNS or Lambda actions are executed.
- This increment has not been tested against a live account or PostgreSQL here.

The collector's read-only API usage does not establish that the deployed IAM
policy is read-only; operators should review that policy separately.
