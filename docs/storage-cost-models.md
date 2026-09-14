# Storage cost models: EBS, EFS and FSx

This document defines the storage pricing slice implemented by the read-only
pilot. It is an estimator built from observed resource state and the AWS Price
List API. It is not a reconstruction of the AWS invoice.

## Safety contract

Every stored rate has a service, Region, SKU, rate code, unit, effective time
and fetch time. A lookup succeeds only when the catalog returns exactly one
finite, positive On-Demand price dimension. Zero, missing and ambiguous results
are stored as `UNRESOLVED`; the collector does not choose the first result or
substitute zero.

An interval is calculated only when two observations are at most ten minutes
apart and all dimensions affecting price or ownership are unchanged. The prior
and current observations must carry the same price, status, source and
proration rule. A state, tag, size, performance or price transition therefore
withholds the interval because its exact transition time is unknown.

Storage estimates are not alert eligible. Team-limit evaluation remains off
until every material billing dimension is implemented and end-to-end coverage
is measured.

## Coverage matrix

| Service | Included | Deliberately unresolved or excluded | Result label |
| --- | --- | --- | --- |
| EBS gp2, gp3, st1, sc1, standard | Provisioned GB; gp3 IOPS above 3,000; gp3 throughput above 125 MB/s | Snapshots, Fast Snapshot Restore, volume initialization and clone charges | `EBS_PROVISIONED` / complete for the volume dimensions collected |
| EBS io1 and io2 | None | Tiered provisioned-IOPS pricing is not implemented | `UNRESOLVED` |
| EFS Regional | Standard, IA and Archive stored bytes reported by `DescribeFileSystems` | Elastic/provisioned throughput, data access, tiering, early deletion, small-file overhead, backup and transfer | `EFS_STORAGE_PARTIAL` |
| EFS One Zone | None | Separate One Zone price dimensions | `UNRESOLVED` |
| FSx Lustre | Provisioned SSD/HDD storage when deployment, throughput-per-unit and drive-cache dimensions match one catalog dimension | Throughput capacity charges, metadata IOPS, backups, data access, monitoring and transfer | `FSX_STORAGE_PARTIAL` |
| FSx Windows, ONTAP and OpenZFS | Exact catalog matching is attempted | Family-specific deployment combinations not yet fixture-validated | `FSX_STORAGE_PARTIAL` or `UNRESOLVED` |

The FSx catalog contains family- and deployment-specific products. A newly
encountered product combination stays unresolved until its live catalog
attributes are inspected and covered by a fixture. This is intentional: a
plausible but wrong FSx match is more damaging than an unavailable subtotal.

## EBS calculation

For a supported volume, the monthly provisioned amount is:

```text
storage GiB × storage USD/GB-month
+ max(IOPS - 3000, 0) × gp3 USD/IOPS-month
+ max(throughput MB/s - 125, 0) × gp3 USD/MBps-month
```

The interval amount uses decimal arithmetic:

```text
monthly provisioned amount × observed seconds / (30 × 86,400)
```

AWS's EBS pricing examples use this 30-day denominator and state that gp3
storage, IOPS and throughput are billed per second with a 60-second minimum.
CloudScope only produces intervals from stable polling observations, so it does
not attempt to infer a short-lived volume between polls.

## EFS calculation

EFS reports storage-class byte counts in `SizeInBytes`. CloudScope converts each
class using `1 GiB = 2^30 bytes` and matches these usage types independently:

| API field | Catalog usage-type suffix |
| --- | --- |
| `ValueInStandard` | `TimedStorage-ByteHrs` |
| `ValueInIA` | `IATimedStorage-ByteHrs` |
| `ValueInArchive` | `ArchiveTimedStorage-ByteHrs` |

AWS defines EFS GB-month usage as accumulated GB-hours divided by the number of
hours in that calendar month. CloudScope therefore splits a cross-month
interval at the UTC month boundary and uses the actual length of each month,
including leap-year February. It does not use a fixed 730-hour divisor.

The EFS result is partial even when all three storage-class prices match. The
API snapshot does not provide all throughput, access, tiering, deletion and
transfer quantities needed for a complete bill.

## FSx calculation

The collector retains file-system family, SSD/HDD type, provisioned capacity,
deployment type and available throughput fields. The current estimator matches
only the storage dimension by:

```text
Region + file-system family + storage type + deployment option + operation + GB-month unit
```

Lustre persistent products also include per-unit storage throughput and drive
cache type. Lustre scratch API deployment values map to the catalog's
`Single-AZ` deployment option. These mappings were checked against the
published Amazon FSx offer file; omitting them either returns no price or can
make a persistent lookup ambiguous.

The matched monthly storage amount is prorated per second using the 30-day
convention in the AWS FSx for Lustre pricing example. The result remains partial
because provisioned throughput and other family-specific charges are excluded.

## Operational validation

Deploying this version changes the normalized EFS/FSx metadata and stored price
evidence. The first collection after deployment can legitimately produce a
transition interval. Two subsequent stable collections within ten minutes are
needed before storage intervals appear.

The collection result includes counts by service and status, plus grouped
unresolved reasons. It intentionally omits resource IDs from these diagnostics.
After live validation, inspect both the collection result and interval ledger:

```sql
SELECT r.provider_resource_type,
       c.basis,
       c.reason,
       COUNT(*) AS intervals,
       SUM(c.amount_usd) AS subtotal_usd
FROM observed_costs AS c
JOIN resources AS r ON r.id = c.resource_id
WHERE c.usage_start >= now() - interval '20 minutes'
GROUP BY r.provider_resource_type, c.basis, c.reason
ORDER BY r.provider_resource_type, c.basis, c.reason;
```

Expected successful bases are `EBS_PROVISIONED`, `EFS_STORAGE_PARTIAL` and
`FSX_STORAGE_PARTIAL`. Any unmatched combination must remain `UNRESOLVED` until
the corresponding AWS catalog response is understood and regression-tested.

## AWS references

- [Amazon EBS pricing](https://aws.amazon.com/ebs/pricing/)
- [Amazon EFS pricing](https://aws.amazon.com/efs/pricing/)
- [EFS billing usage types and GB-month calculation](https://docs.aws.amazon.com/efs/latest/ug/billing-usage-reports-understand.html)
- [Amazon FSx for Lustre pricing](https://aws.amazon.com/fsx/lustre/pricing/)
- [Amazon FSx current US East (N. Virginia) offer file](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonFSx/current/us-east-1/index.json)
- [Finding prices in AWS service price list files](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/finding-prices-in-service-price-list-files.html)
