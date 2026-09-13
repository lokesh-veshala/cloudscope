# Customer evaluation and onboarding

## Connect the first test account

1. Generate and deploy the CloudFormation template from **Cloud accounts**.
2. Configure an AWS CLI profile on the collector host whose
   `credential_process` uses the Roles Anywhere signing helper and collector
   certificate. Keep the private key readable only by the collector operator.
3. Copy `.env.example` to `.env` and replace every path with an absolute path on
   the collector VM. For private-IP testing, set `CLOUDSCOPE_BIND_IP` to that IP
   and `CLOUDSCOPE_ALLOWED_ORIGINS` to the exact `http://IP:5173` browser origin.
   Set `CLOUDSCOPE_UID` and `CLOUDSCOPE_GID` to the output of `id -u` and `id -g`;
   this lets the API read the operator-owned `0600` collector key without making
   the key broadly readable.
4. Run `docker compose up -d --build`, then verify `/healthz` on port 8001.
5. Enter the CloudFormation outputs and mounted profile name under **Register
   deployed stack outputs**, then select **Register account locally**.
6. Select **Test connection**. Collection stays disabled unless identity,
   inventory, CloudWatch, tagging, and strict catalog lookup checks pass.
7. Select **Collect now** and inspect resource counts, service failures, and EC2
   pricing coverage.

No certificate, private key, AWS profile, inventory, or account-specific output
belongs in Git. Collection is read-only. SNS publication and limit evaluation
remain disabled until complete usage and pricing intervals are implemented.

If historical Spot coverage is unavailable, CloudScope records a provisional
Spot estimate equal to 58% of the exact matching On-Demand rate (a 42% assumed
discount). It is labeled as an assumption and cannot be used for threshold
alerts. It is not evidence of the Spot price charged by AWS.

## Generate the AWS onboarding stack

Open **Cloud accounts**, enter the test account ID, deployment region, collector certificate CN, and the PEM contents of the verified public CA certificate. CloudScope downloads a complete CloudFormation template containing an account/region guard, a CA trust anchor, a certificate-CN-bound collector role, a Roles Anywhere profile, and a dedicated SNS topic. Private keys never belong in the form or template.

Deploy the template in the selected test account with IAM resource acknowledgement and retain its outputs. Template generation configures the AWS side only; importing the outputs and running the signing helper remain separate connection steps.

## Availability

CloudScope currently combines a demonstration dashboard with a test-account
collector. It can register the deployed stack and collect inventory, but it
cannot yet calculate AWS spend, save shared team limits, or send notifications.

The demo can be used to review the interface and agree on requirements. Do not enter AWS access keys, private keys, customer billing data or production credentials.

## Evaluate the dashboard

1. Open the dashboard link supplied by the maintainer and confirm the DEMO DATA label.
2. Use Overview to inspect the sample layout. Cards are illustrative and are not a reconciled account statement; sample sections may represent different scenarios.
3. Open Resources and combine search, service and region filters. These filter sample records only.
4. Hover over a chart point, focus it using the keyboard, or tap it to display its sample cost.
5. Open Date Range. The complete sample snapshot covers September 1–13, 2026. Other ranges return “Data unavailable”; they do not estimate missing usage. The date range accepts 1–90 days.
6. Open Team Limits to review illustrative threshold scenarios. There is no editable or enforced customer policy yet.
7. Open Cloud Accounts to register and test one non-production AWS account. The
   Shared Engineering menu still identifies the demo workspace and does not yet
   switch the main dashboard to live data.
8. Open Notifications or Alert History. No real SNS delivery or Lambda execution has occurred.
9. In Settings, select table spacing and save it on this device. Loading the saved preference applies it again. This is not a shared organization setting.
10. Refresh reloads the demo indication; it does not contact AWS.

Changing a display preference does not change collection cadence, monetary limits or access permissions.

## Understanding cost labels

Estimated Cost is intended to mean a calculation based on observed usage and matched price dimensions, not an AWS invoice. The current displayed amounts are sample data, so even this live-estimate interpretation does not apply yet.

Coverage must not be interpreted as the probability that the amount is correct. A high percentage can only be meaningful with a documented denominator and complete resource discovery. The current UI percentages are examples.

Stopped compute can leave chargeable storage or other attached resources. The production design needs separate cost dimensions for those resources; the demo does not establish that they have been measured.

## Preparing a future pilot

Use the generated IAM stack only in the authorized test account until its
permissions and collected results have been reviewed. Production onboarding is
not approved by this guide.

| Customer input | Why it is needed | Handling |
| --- | --- | --- |
| AWS account IDs and approved regions | Define discovery scope | Share through the agreed onboarding channel |
| Team ownership tags and historical allocation rules | Define which usage belongs to each team | Resolve missing/shared tags before chargeback |
| Identity provider and user groups | Establish server-side access policy | Coordinate with the identity administrator |
| Collector host and approved outbound access | Run collection inside the customer's environment | Coordinate with infrastructure/network owners |
| Roles Anywhere trust and credential configuration | Obtain temporary role credentials | Keep private key material on the approved collector host |
| Monthly limit amounts and billing timezone | Define policy boundaries | Confirm units and effective dates |
| Approved SNS topic and customer Lambda owner | Define notification destination and automation ownership | No arbitrary execution or resource deletion permissions |
| Billing exclusions and tolerance | Define what estimates can and cannot support | Document discounts, taxes and unsupported dimensions |

The intended alert period is a calendar month with explicit timezone handling. UTC is the proposed V1 baseline; a different business timezone requires engineering support and boundary tests.

## Production acceptance sequence — not available today

1. Engineering provides a tested release, reviewed permissions and rollback instructions.
2. Customer authorizes one non-production account and a small region set.
3. Verify expected account identity and every required read permission.
4. Reconcile native inventory, including untagged resources.
5. Reconcile sample usage and historical rates against agreed reference evidence.
6. Resolve incomplete coverage and confirm delayed-data behavior.
7. Test notifications against an isolated topic and non-destructive Lambda.
8. Test duplicate delivery, collector outages and month rollover.
9. Approve the resulting report before adding production accounts.

Do not attach automatic stop/termination workflows to an unvalidated estimate. Even after integration, notification delivery must be treated as potentially repeated; customer automation needs an idempotency key.

## Reporting an issue

Provide the selected view, date range, resource/filter context, expected result, actual result and reproduction steps. Include a screenshot if useful. Redact credentials and confidential identifiers before sharing outside the authorized project group.

For a cost discrepancy, specify whether the comparison is to a test fixture, public list price, negotiated rate or billed amount. Those are different references. For a missed alert, distinguish threshold evaluation from SNS publication and downstream Lambda execution.

## What happens next

The next engineering milestones are complete usage/cost intervals, authenticated
API integration, dashboard queries, scheduling, and validated notification
delivery. A working button or green unit-test suite is not evidence that these
milestones are complete.
