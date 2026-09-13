"""Conservative polling estimates. Never reconstruct usage before first observation."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext


def estimate_observed_interval(previous, current, current_price):
    start, end = previous["last_seen"], current["observed_at"]
    seconds = Decimal((end-start).days * 86400 + (end-start).seconds) + Decimal((end-start).microseconds) / Decimal(1000000)
    result = {"amount": None, "basis": "UNRESOLVED", "reason": "unsupported_dimension"}
    if seconds <= 0:
        raise ValueError("observations must be ordered")
    if seconds > 600:
        return {**result, "reason": "observation_gap_exceeds_10_minutes"}
    if current["provider_resource_type"] != "ec2":
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
