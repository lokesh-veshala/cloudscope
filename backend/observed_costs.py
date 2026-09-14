"""Conservative polling estimates. Never reconstruct usage before first observation."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext


def estimate_observed_interval(previous, current, current_price):
    start, end = previous["last_seen"], current["observed_at"]
    seconds = Decimal((end-start).days * 86400 + (end-start).seconds) + Decimal((end-start).microseconds) / Decimal(1000000)
    result = {"amount": None, "basis": "UNRESOLVED", "reason": "unsupported_dimension"}
    if seconds <= 0:
        raise ValueError("observations must be ordered")
    if seconds > 600:
        return {**result, "reason": "observation_gap_exceeds_10_minutes"}
    service = current["provider_resource_type"]
    if service in ("ebs", "efs", "fsx"):
        return _storage_interval(previous, current, current_price, seconds)
    if service != "ec2":
        return result
    old, new = previous["metadata"], current["metadata"]
    keys = ("instance_type", "purchase_model", "os", "tenancy", "tags", "launch_time")
    if previous["state"] != current["state"] or any(old.get(k) != new.get(k) for k in keys):
        return {**result, "reason": "state_or_ownership_transition_time_unknown"}
    if current["state"] != "running":
        return {**result, "reason": "compute_not_observed_running"}
    prior_price = old.get("pricing", {})
    if current_price.get("status") not in ("COMPLETE", "ESTIMATED") or prior_price.get("status") != current_price.get("status"):
        return {**result, "reason": "missing_price"}
    try:
        rate = Decimal(current_price["usd_per_hour"])
        old_rate = Decimal(prior_price["usd_per_hour"])
        fetched = datetime.fromisoformat(prior_price["fetched_at"])
        effective = datetime.fromisoformat(prior_price["effective_at"])
        if not rate.is_finite() or rate <= 0 or old_rate != rate:
            return {**result, "reason": "invalid_or_changed_rate"}
        if effective > start or fetched > end or (end-fetched).total_seconds() > 86400:
            return {**result, "reason": "price_outside_valid_window"}
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return {**result, "reason": "invalid_price_evidence"}
    with localcontext() as ctx:
        ctx.prec = 38
        amount = seconds / Decimal(3600) * rate
    return {"amount": amount, "basis": "ASSUMED_SPOT_DISCOUNT" if current_price["status"] == "ESTIMATED" else "OBSERVED_EC2",
            "reason": "continuous_running_between_polls_assumed"}


def _storage_interval(previous, current, current_price, seconds):
    unresolved = {"amount": None, "basis": "UNRESOLVED"}
    service = current["provider_resource_type"]
    fields = {
        "ebs": ("volume_type", "size_gib", "iops", "throughput", "tags"),
        "efs": ("size_standard_bytes", "size_ia_bytes", "size_archive_bytes",
                "availability_zone_name", "throughput_mode",
                "provisioned_throughput_mibps", "tags"),
        "fsx": ("filesystem_type", "storage_capacity_gib", "storage_type",
                "deployment_type", "throughput_capacity",
                "per_unit_storage_throughput", "tags"),
    }[service]
    old, new = previous["metadata"], current["metadata"]
    if any(old.get(field) != new.get(field) for field in fields):
        return {**unresolved, "reason": "storage_or_ownership_transition_time_unknown"}
    if service in ("efs", "fsx") and (
            previous["state"] != current["state"] or current["state"].lower() != "available"):
        return {**unresolved, "reason": "filesystem_not_continuously_available"}
    prior_price = old.get("pricing", {})
    if (current_price.get("status") not in ("COMPLETE", "PARTIAL")
            or prior_price.get("status") != current_price.get("status")
            or prior_price.get("source") != current_price.get("source")
            or prior_price.get("proration") != current_price.get("proration")):
        return {**unresolved, "reason": "missing_storage_price"}
    try:
        monthly = Decimal(current_price["monthly_usd"])
        old_monthly = Decimal(prior_price["monthly_usd"])
        fetched = datetime.fromisoformat(prior_price["fetched_at"])
        effective = datetime.fromisoformat(prior_price["effective_at"])
        if not monthly.is_finite() or monthly <= 0 or old_monthly != monthly:
            return {**unresolved, "reason": "invalid_or_changed_storage_rate"}
        if effective > previous["last_seen"] or fetched > current["observed_at"] \
                or (current["observed_at"] - fetched).total_seconds() > 86400:
            return {**unresolved, "reason": "storage_price_outside_valid_window"}
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return {**unresolved, "reason": "invalid_storage_price_evidence"}
    with localcontext() as ctx:
        ctx.prec = 38
        if current_price["proration"] == "FIXED_30_DAY_MONTH":
            amount = monthly * seconds / Decimal(30 * 86400)
        elif current_price["proration"] == "UTC_CALENDAR_MONTH":
            amount = monthly * _calendar_month_fraction(
                previous["last_seen"], current["observed_at"])
        else:
            return {**unresolved, "reason": "invalid_storage_proration"}
    return {"amount": amount, "basis": current_price["source"],
            "reason": "continuous_provisioning_between_polls_assumed"}


def _calendar_month_fraction(start, end):
    """Return the exact fraction of UTC calendar months covered by an interval."""
    fraction = Decimal(0)
    cursor = start
    while cursor < end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        boundary = min(end, next_month)
        month_start = cursor.replace(day=1, hour=0, minute=0, second=0,
                                     microsecond=0)
        month_seconds = Decimal((next_month - month_start).total_seconds())
        fraction += Decimal((boundary - cursor).total_seconds()) / month_seconds
        cursor = boundary
    return fraction
