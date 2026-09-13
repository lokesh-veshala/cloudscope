# Customer evaluation and onboarding

## Generate the AWS onboarding stack

Open **Cloud accounts**, enter the test account ID, deployment region, collector certificate CN, and the PEM contents of the verified public CA certificate. CloudScope downloads a complete CloudFormation template containing an account/region guard, a CA trust anchor, a certificate-CN-bound collector role, a Roles Anywhere profile, and a dedicated SNS topic. Private keys never belong in the form or template.

Deploy the template in the selected test account with IAM resource acknowledgement and retain its outputs. Template generation configures the AWS side only; importing the outputs and running the signing helper remain separate connection steps.

## Availability

CloudScope currently provides a demonstration dashboard. It cannot onboard a live AWS account, calculate your AWS spend, save shared team limits or send customer notifications.

The demo can be used to review the interface and agree on requirements. Do not enter AWS access keys, private keys, customer billing data or production credentials.

## Evaluate the dashboard

1. Open the dashboard link supplied by the maintainer and confirm the DEMO DATA label.
2. Use Overview to inspect the sample layout. Cards are illustrative and are not a reconciled account statement; sample sections may represent different scenarios.
3. Open Resources and combine search, service and region filters. These filter sample records only.
4. Hover over a chart point, focus it using the keyboard, or tap it to display its sample cost.
5. Open Date Range. The complete sample snapshot covers September 1–13, 2026. Other ranges return “Data unavailable”; they do not estimate missing usage. The date range accepts 1–90 days.
6. Open Team Limits to review illustrative threshold scenarios. There is no editable or enforced customer policy yet.
7. Open Cloud Accounts for onboarding prerequisites. The Shared Engineering menu identifies the demo workspace; it cannot switch real AWS accounts.
8. Open Notifications or Alert History. No real SNS delivery or Lambda execution has occurred.
9. In Settings, select table spacing and save it on this device. Loading the saved preference applies it again. This is not a shared organization setting.
10. Refresh reloads the demo indication; it does not contact AWS.

Changing a display preference does not change collection cadence, monetary limits or access permissions.

## Understanding cost labels

Estimated Cost is intended to mean a calculation based on observed usage and matched price dimensions, not an AWS invoice. The current displayed amounts are sample data, so even this live-estimate interpretation does not apply yet.

Coverage must not be interpreted as the probability that the amount is correct. A high percentage can only be meaningful with a documented denominator and complete resource discovery. The current UI percentages are examples.

Stopped compute can leave chargeable storage or other attached resources. The production design needs separate cost dimensions for those resources; the demo does not establish that they have been measured.

## Preparing a future pilot

Do not deploy IAM changes based on this guide alone. A reviewed onboarding template and working collector are prerequisites and are not included yet.

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

## Pilot acceptance sequence — not available today

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

The next engineering milestones are durable backend storage, account onboarding and collection, authenticated API integration, and validated notification delivery. A working button or a green unit-test suite is not evidence that these milestones are complete.
