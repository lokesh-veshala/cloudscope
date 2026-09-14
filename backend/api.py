from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID
from decimal import Decimal
from observed_costs import estimate_observed_interval

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import engine, initialize_database, ping_database, rows
from providers.aws.collector import (
    AwsAccountConfig,
    collect_resources,
    price_running_ec2,
    price_storage_resources,
    validate_connection,
)


ARN = re.compile(r"^arn:aws(?:-[a-z]+)?:[a-z0-9-]*:[a-z0-9-]*:\d{12}:.+$")


class AccountCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=100)
    provider_account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(pattern=r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
    credential_profile: str = Field(min_length=1, max_length=128)
    role_arn: str
    roles_anywhere_profile_arn: str
    trust_anchor_arn: str
    sns_topic_arn: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="CloudScope API", version="1.2.0", lifespan=lifespan)
allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CLOUDSCOPE_ALLOWED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/healthz")
def health():
    ping_database()
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/accounts")
def list_accounts():
    with engine.connect() as connection:
        return rows(
            connection.execute(
                text(
                    """SELECT id, provider_account_id, display_name, region,
                    credential_profile, role_arn, roles_anywhere_profile_arn,
                    trust_anchor_arn, sns_topic_arn, connection_status,
                    connection_checked_at, last_collected_at, last_error,
                    collection_enabled, collection_interval_seconds,
                    next_collection_at, created_at
                    FROM cloud_accounts ORDER BY created_at"""
                )
            )
        )


@app.post("/api/v1/accounts", status_code=201)
def create_account(payload: AccountCreate):
    required_arns = (
        payload.role_arn,
        payload.roles_anywhere_profile_arn,
        payload.trust_anchor_arn,
    )
    for value in required_arns:
        if not ARN.match(value):
            raise HTTPException(422, "one or more AWS ARNs are invalid")
    if payload.sns_topic_arn and not ARN.match(payload.sns_topic_arn):
        raise HTTPException(422, "sns_topic_arn is not a valid AWS ARN")
    for value in (*required_arns, payload.sns_topic_arn):
        if value and f":{payload.provider_account_id}:" not in value:
            raise HTTPException(422, "every ARN must belong to the entered AWS account")
    try:
        with engine.begin() as connection:
            row = connection.execute(
                text(
                    """INSERT INTO cloud_accounts
                    (provider, provider_account_id, display_name, region,
                     credential_profile, role_arn, roles_anywhere_profile_arn,
                     trust_anchor_arn, sns_topic_arn)
                    VALUES ('aws', :account, :name, :region, :profile, :role,
                            :roles_profile, :anchor, :sns)
                    RETURNING id, provider_account_id, display_name, region,
                              connection_status, created_at"""
                ),
                {
                    "account": payload.provider_account_id,
                    "name": payload.display_name.strip(),
                    "region": payload.region,
                    "profile": payload.credential_profile,
                    "role": payload.role_arn,
                    "roles_profile": payload.roles_anywhere_profile_arn,
                    "anchor": payload.trust_anchor_arn,
                    "sns": payload.sns_topic_arn,
                },
            ).mappings().one()
        return dict(row)
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, "this AWS account is already registered") from exc
        raise


def _account(account_id: UUID):
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM cloud_accounts WHERE id=:id AND enabled=true"),
            {"id": account_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "account not found")
    return row


def _config(account) -> AwsAccountConfig:
    return AwsAccountConfig(
        account["provider_account_id"],
        account["region"],
        account["credential_profile"],
    )


class CollectionScheduleUpdate(BaseModel):
    enabled: bool
    interval_seconds: int = Field(ge=120, le=600)


@app.post("/api/v1/accounts/{account_id}/collection-schedule")
def update_collection_schedule(account_id: UUID, payload: CollectionScheduleUpdate):
    account = _account(account_id)
    if payload.enabled and account["connection_status"] != "CONNECTED":
        raise HTTPException(409, "run a successful connection test before scheduling")
    with engine.begin() as connection:
        row = connection.execute(text("""UPDATE cloud_accounts
            SET collection_enabled=:enabled,
                collection_interval_seconds=:interval,
                next_collection_at=CASE WHEN :enabled THEN now() ELSE NULL END
            WHERE id=:id RETURNING collection_enabled,
                collection_interval_seconds, next_collection_at"""),
            {"enabled": payload.enabled, "interval": payload.interval_seconds,
             "id": account_id}).mappings().one()
        if not payload.enabled:
            connection.execute(text("""DELETE FROM collection_jobs
                WHERE cloud_account_id=:id AND status='QUEUED'"""), {"id": account_id})
    return dict(row)


@app.get("/api/v1/accounts/{account_id}/collection-jobs")
def account_collection_jobs(account_id: UUID):
    _account(account_id)
    with engine.connect() as connection:
        return rows(connection.execute(text("""SELECT id, collector, scheduled_for,
            started_at, finished_at, status, attempt_count, error_code, details
            FROM collection_jobs WHERE cloud_account_id=:id
            ORDER BY scheduled_for DESC LIMIT 20"""), {"id": account_id}))


@app.post("/api/v1/accounts/{account_id}/test-connection")
def test_account_connection(account_id: UUID):
    account = _account(account_id)
    result = validate_connection(_config(account))
    status = "CONNECTED" if result["connected"] else "FAILED"
    error = None
    if not result["connected"]:
        error = "; ".join(
            f'{check["name"]}: {check.get("error", "failed")}'
            for check in result["checks"]
            if check["status"] == "FAIL"
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE cloud_accounts SET connection_status=:status,
                connection_checked_at=now(), last_error=:error WHERE id=:id"""
            ),
            {"status": status, "error": error, "id": account_id},
        )
    return {**result, "status": status}


@app.post("/api/v1/accounts/{account_id}/collect")
def collect_account(account_id: UUID):
    account = _account(account_id)
    if account["connection_status"] != "CONNECTED":
        raise HTTPException(409, "run a successful connection test before collection")
    with engine.connect() as lock_connection:
        locked = lock_connection.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
            {"key": str(account_id)},
        ).scalar_one()
        if not locked:
            raise HTTPException(409, "collection already running for this account")
        try:
            return _collect_account(account_id, account)
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": str(account_id)},
            )


def _collect_account(account_id: UUID, account):
    discovered, failures = collect_resources(_config(account))
    ec2_prices = price_running_ec2(_config(account), discovered)
    storage_prices = price_storage_resources(_config(account), discovered)
    complete_prices = sum(
        1 for item in ec2_prices.values() if item["status"] == "COMPLETE"
    )
    estimated_prices = sum(
        1 for item in ec2_prices.values() if item["status"] == "ESTIMATED"
    )
    with engine.begin() as connection:
        connection.execute(text("SELECT id FROM cloud_accounts WHERE id=:id FOR UPDATE"), {"id": account_id})
        for item in discovered:
            previous = connection.execute(text("""SELECT id, last_seen, state, metadata FROM resources
                WHERE cloud_account_id=:account AND provider_resource_type=:service AND provider_resource_id=:resource
                FOR UPDATE"""), {"account": account_id, "service": item["provider_resource_type"], "resource": item["provider_resource_id"]}).mappings().first()
            if previous and previous["last_seen"] >= item["observed_at"]:
                continue
            metadata = {
                **item["metadata"],
                "pricing": (ec2_prices.get(item["provider_resource_id"])
                    if item["provider_resource_type"] == "ec2"
                    else storage_prices.get((item["provider_resource_type"],
                                              item["provider_resource_id"])))
                    or {"status": "NOT_APPLICABLE"},
            }
            resource_id = connection.execute(
                text(
                    """INSERT INTO resources
                    (cloud_account_id, provider, region, availability_zone,
                     resource_type, provider_resource_type, provider_resource_id,
                     resource_arn, name, state, first_seen, last_seen, metadata)
                    VALUES (:account, 'aws', :region, :az, :resource_type,
                     :provider_type, :provider_id, :arn, :name, :state,
                     :observed, :observed, CAST(:metadata AS jsonb))
                    ON CONFLICT (cloud_account_id, provider_resource_type,
                                 provider_resource_id) DO UPDATE SET
                      region=excluded.region,
                      availability_zone=excluded.availability_zone,
                      resource_arn=excluded.resource_arn,
                      name=excluded.name,
                      state=excluded.state,
                      last_seen=excluded.last_seen,
                      deleted_at=NULL,
                      metadata=excluded.metadata
                    RETURNING id"""
                ),
                {
                    "account": account_id,
                    "region": item["region"],
                    "az": item["availability_zone"],
                    "resource_type": item["resource_type"],
                    "provider_type": item["provider_resource_type"],
                    "provider_id": item["provider_resource_id"],
                    "arn": item["resource_arn"],
                    "name": item["name"],
                    "state": item["state"],
                    "observed": item["observed_at"],
                    "metadata": json.dumps(metadata),
                },
            ).scalar_one()
            if previous:
                estimate = estimate_observed_interval(previous, item, metadata["pricing"])
                connection.execute(text("""INSERT INTO observed_costs
                    (resource_id,usage_start,usage_end,amount_usd,basis,reason,tags)
                    VALUES (:id,:start,:end,:amount,:basis,:reason,CAST(:tags AS jsonb))
                    ON CONFLICT DO NOTHING"""), {"id": resource_id, "start": previous["last_seen"],
                    "end": item["observed_at"], "amount": estimate["amount"], "basis": estimate["basis"],
                    "reason": estimate["reason"], "tags": json.dumps(previous["metadata"].get("tags", {}))})
            _sync_state(connection, resource_id, item["state"], item["observed_at"])
            _sync_tags(
                connection,
                resource_id,
                item["metadata"].get("tags", {}),
                item["observed_at"],
            )
        connection.execute(
            text(
                """UPDATE cloud_accounts SET last_collected_at=now(),
                last_error=:error WHERE id=:id"""
            ),
            {
                "error": json.dumps(failures) if failures else None,
                "id": account_id,
            },
        )
    return {
        "status": "PARTIAL" if failures else "SUCCEEDED",
        "resource_count": len(discovered),
        "service_failures": failures,
        "ec2_pricing": {
            "complete": complete_prices,
            "estimated_spot_fallback": estimated_prices,
            "unresolved": len(ec2_prices) - complete_prices - estimated_prices,
        },
        "storage_pricing": _storage_pricing_summary(discovered, storage_prices),
        "alerts_evaluated": False,
        "alerts_reason": "complete usage cost intervals do not exist yet",
    }


def _storage_pricing_summary(discovered, prices):
    result = {}
    for service in ("ebs", "efs", "fsx"):
        quotes = [prices.get((service, item["provider_resource_id"]), {})
                  for item in discovered if item["provider_resource_type"] == service]
        reasons = {}
        for quote in quotes:
            if quote.get("status") == "UNRESOLVED":
                reason = quote.get("reason", "unknown")
                reasons[reason] = reasons.get(reason, 0) + 1
        result[service] = {
            status.lower(): sum(1 for quote in quotes if quote.get("status") == status)
            for status in ("COMPLETE", "PARTIAL", "UNRESOLVED")}
        result[service]["unresolved_reasons"] = reasons
    return result


def _sync_state(connection, resource_id, state, observed_at):
    current = connection.execute(
        text(
            """SELECT id, state FROM resource_state_history
            WHERE resource_id=:id AND valid_to IS NULL FOR UPDATE"""
        ),
        {"id": resource_id},
    ).mappings().first()
    if current and current["state"] == state:
        return
    if current:
        connection.execute(
            text("UPDATE resource_state_history SET valid_to=:at WHERE id=:id"),
            {"at": observed_at, "id": current["id"]},
        )
    connection.execute(
        text(
            """INSERT INTO resource_state_history(resource_id, state, valid_from)
            VALUES (:id, :state, :at)"""
        ),
        {"id": resource_id, "state": state, "at": observed_at},
    )


def _sync_tags(connection, resource_id, tags, observed_at):
    current = {
        row["tag_key"]: row
        for row in connection.execute(
            text(
                """SELECT id, tag_key, tag_value FROM resource_tag_history
                WHERE resource_id=:id AND valid_to IS NULL FOR UPDATE"""
            ),
            {"id": resource_id},
        ).mappings()
    }
    for key, row in current.items():
        if tags.get(key) != row["tag_value"]:
            connection.execute(
                text("UPDATE resource_tag_history SET valid_to=:at WHERE id=:id"),
                {"at": observed_at, "id": row["id"]},
            )
    for key, value in tags.items():
        if key not in current or current[key]["tag_value"] != value:
            connection.execute(
                text(
                    """INSERT INTO resource_tag_history
                    (resource_id, tag_key, tag_value, valid_from)
                    VALUES (:id, :key, :value, :at)"""
                ),
                {"id": resource_id, "key": key, "value": value, "at": observed_at},
            )


@app.get("/api/v1/accounts/{account_id}/resources")
def account_resources(account_id: UUID):
    _account(account_id)
    with engine.connect() as connection:
        return rows(
            connection.execute(
                text(
                    """SELECT id, provider_resource_id, provider_resource_type,
                    resource_type, name, region, availability_zone, state,
                    last_seen, metadata FROM resources
                    WHERE cloud_account_id=:id AND deleted_at IS NULL
                    ORDER BY provider_resource_type, name"""
                ),
                {"id": account_id},
            )
        )


@app.get("/api/v1/quality")
def quality():
    return {
        "currency": "USD",
        "calculation_label": "Estimated Cost",
        "alerting_eligible": False,
        "guardrails": [
            "exact_price_match",
            "complete_usage_intervals",
            "fresh_inputs",
            "idempotent_threshold_event",
        ],
        "reason": "alerting remains disabled until complete cost intervals exist",
    }


class PilotLimitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tag_key: str = Field(min_length=1, max_length=128)
    tag_value: str = Field(min_length=1, max_length=256)
    amount_usd: Decimal = Field(gt=0, max_digits=20, decimal_places=6, allow_inf_nan=False)


@app.post("/api/v1/accounts/{account_id}/limits", status_code=201)
def create_pilot_limit(account_id: UUID, payload: PilotLimitCreate):
    _account(account_id)
    with engine.begin() as connection:
        result = connection.execute(text("""INSERT INTO pilot_team_limits
            (cloud_account_id,name,tag_key,tag_value,amount_usd)
            VALUES (:account,:name,:tag_key,:tag_value,:amount) RETURNING id"""),
            {"account": account_id, "name": payload.name, "tag_key": payload.tag_key,
             "tag_value": payload.tag_value, "amount": payload.amount_usd}).scalar_one()
    return {"id": result, "notification_enabled": False}


@app.get("/api/v1/accounts/{account_id}/overview")
def live_overview(account_id: UUID):
    account = _account(account_id)
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Clip intervals crossing the UTC month boundary; decimal math stays in PostgreSQL.
    subtotal = "SUM(c.amount_usd * EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) / EXTRACT(EPOCH FROM (c.usage_end-c.usage_start)))"
    scope = "FROM observed_costs c JOIN resources r ON r.id=c.resource_id WHERE r.cloud_account_id=:id AND c.usage_end>:start AND c.usage_start<:end"
    args = {"id": account_id, "start": start, "end": now}
    with engine.connect() as connection:
        services = rows(connection.execute(text("""SELECT provider_resource_type AS service, count(*) AS resources,
            min(first_seen) AS first_observed, max(last_seen) AS last_observed
            FROM resources WHERE cloud_account_id=:id GROUP BY provider_resource_type ORDER BY provider_resource_type"""), {"id": account_id}))
        costs = rows(connection.execute(text(f"SELECT c.basis, count(*) AS intervals, {subtotal} AS amount_usd {scope} GROUP BY c.basis"), args))
        limits = rows(connection.execute(text("SELECT * FROM pilot_team_limits WHERE cloud_account_id=:id ORDER BY created_at"), {"id": account_id}))
        for limit in limits:
            amount = connection.execute(text(f"SELECT {subtotal} {scope} AND (c.tags ->> :key) = :value"),
                {**args, "key": limit["tag_key"], "value": limit["tag_value"]}).scalar_one()
            limit["observed_subtotal_usd"] = str(amount) if amount is not None else None
            limit["amount_usd"] = str(limit["amount_usd"])
            limit["evaluation_status"] = "WITHHELD_INCOMPLETE_COST_COVERAGE"
    for cost in costs:
        cost["amount_usd"] = str(cost["amount_usd"]) if cost["amount_usd"] is not None else None
    return {"account_name": account["display_name"], "period_start": start, "period_end": now,
            "last_collected_at": account["last_collected_at"], "last_error": account["last_error"],
            "services": services, "observed_costs": costs, "limits": limits,
            "complete_account_cost_usd": None, "alerts_evaluated": False,
            "limitations": ["Supported EC2 compute and provisioned EBS dimensions are priced",
                "EFS and FSx values include matched storage dimensions only",
                "Continuous provisioning between polls is assumed",
                "No usage before the first observation is reconstructed",
                "EFS/FSx throughput, access, backup and transfer dimensions remain excluded",
                "Spot fallback assumes a 42% discount", "Limit alarms are withheld for incomplete costs"]}
