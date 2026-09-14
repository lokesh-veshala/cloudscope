from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from decimal import Decimal
from observed_costs import estimate_observed_interval
from cost_engine.automatic import next_decision

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from database import engine, initialize_database, ping_database, rows
from providers.aws.collector import (
    AwsAccountConfig,
    collect_resources,
    price_running_ec2,
    price_storage_resources,
    publish_sns_test,
    publish_threshold_alert,
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


app = FastAPI(title="CloudScope API", version="1.9.0", lifespan=lifespan)
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
    allow_methods=["GET", "POST", "DELETE"],
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


@app.post("/api/v1/accounts/{account_id}/notifications/test")
def test_account_notification(account_id: UUID):
    account = _account(account_id)
    if account["connection_status"] != "CONNECTED":
        raise HTTPException(409, "run a successful connection test before publishing")
    topic = account["sns_topic_arn"]
    parts = topic.split(":", 5) if topic else []
    if (len(parts) != 6 or parts[0] != "arn" or parts[2] != "sns"
            or parts[4] != account["provider_account_id"] or not parts[3]
            or not parts[5]):
        raise HTTPException(409, "this account has no valid approved SNS topic")
    sent_at = datetime.now(timezone.utc)
    with engine.begin() as connection:
        delivery_id = connection.execute(text("""INSERT INTO notification_deliveries
            (cloud_account_id,event_type,status,topic_arn)
            VALUES (:account,'CLOUDSCOPE_NOTIFICATION_TEST','PENDING',:topic)
            RETURNING id"""), {"account": account_id, "topic": topic}).scalar_one()
    result = publish_sns_test(_config(account), topic, account["display_name"],
                              sent_at, str(delivery_id))
    status = "PUBLISHED" if result["published"] else "FAILED"
    with engine.begin() as connection:
        connection.execute(text("""UPDATE notification_deliveries
            SET status=:status, sns_message_id=:message, error_message=:error
            WHERE id=:id"""), {"status": status, "message": result.get("message_id"),
                                "error": result.get("error"), "id": delivery_id})
    if not result["published"]:
        raise HTTPException(502, result["error"])
    return {**result, "delivery_id": delivery_id, "is_test": True,
            "automatic_threshold_alerts_enabled": True,
            "subscriber_note": "SNS invokes subscribed Lambda functions; CloudScope does not invoke Lambda directly"}


@app.get("/api/v1/accounts/{account_id}/notifications")
def account_notifications(account_id: UUID):
    _account(account_id)
    with engine.connect() as connection:
        return rows(connection.execute(text("""SELECT * FROM (
            SELECT id,event_type,status,sns_message_id,error_message,created_at,
              NULL::uuid AS team_id,NULL::numeric AS threshold_percent,
              NULL::numeric AS estimated_cost_usd,NULL::numeric AS limit_usd,
              true AS is_test
            FROM notification_deliveries WHERE cloud_account_id=:account
            UNION ALL
            SELECT id,event_type,status,sns_message_id,error_message,created_at,
              team_id,threshold_percent,estimated_cost_usd,limit_usd,false AS is_test
            FROM pilot_threshold_events WHERE cloud_account_id=:account
          ) history ORDER BY created_at DESC LIMIT 100"""), {"account": account_id}))


@app.get("/api/v1/alerts")
def alert_history():
    with engine.connect() as connection:
        return rows(connection.execute(text("""SELECT e.id,e.event_type,e.status,
            e.threshold_percent,e.estimated_cost_usd,e.limit_usd,e.usage_percent,
            e.baseline_amount_usd,e.observed_cost_usd,e.monitoring_started_at,
            e.period_basis,e.calculation_coverage,
            e.sns_message_id,e.error_message,e.created_at,e.published_at,
            t.name AS team_name,a.display_name AS account_name
          FROM pilot_threshold_events e
          JOIN pilot_team_limits t ON t.id=e.team_id
          JOIN cloud_accounts a ON a.id=e.cloud_account_id
          ORDER BY e.created_at DESC LIMIT 200""")))


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
    alert_result = _evaluate_and_deliver_alerts(account_id, account)
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
        "alerts_evaluated": True,
        "alerts": alert_result,
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
        "automatic_alerting_enabled": True,
        "alerting_eligible": False,
        "guardrails": [
            "exact_price_match",
            "complete_usage_intervals",
            "fresh_inputs",
            "idempotent_threshold_event",
        ],
        "reason": "eligibility is evaluated per team after each collection; this global endpoint has no cost snapshot",
    }


class PilotLimitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    amount_usd: Decimal = Field(gt=0, max_digits=20, decimal_places=6, allow_inf_nan=False)
    baseline_amount_usd: Decimal = Field(default=Decimal("0"), ge=0,
                                         max_digits=20, decimal_places=6,
                                         allow_inf_nan=False)
    allow_partial_alerts: bool = False
    filter_expression: dict

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("team name cannot be blank")
        return value

    @field_validator("filter_expression")
    @classmethod
    def valid_filter_expression(cls, value):
        return _validate_filter_expression(value)


def _validate_filter_expression(expression, depth=0, counter=None):
    """Validate and normalize the bounded tag-filter language accepted by the API."""
    if counter is None:
        counter = [0]
    if not isinstance(expression, dict) or depth > 4:
        raise ValueError("filter expression must be an object with at most 4 nested levels")
    kind = expression.get("kind")
    if kind == "tag":
        counter[0] += 1
        if counter[0] > 20:
            raise ValueError("filter expression cannot contain more than 20 tag conditions")
        key = expression.get("key")
        operator = expression.get("operator")
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > 128:
            raise ValueError("each tag condition requires a tag key of 1 to 128 characters")
        if operator not in {"EQUALS", "NOT_EQUALS", "EXISTS", "NOT_EXISTS"}:
            raise ValueError("unsupported tag comparison operator")
        normalized = {"kind": "tag", "key": key.strip(), "operator": operator}
        if operator in {"EQUALS", "NOT_EQUALS"}:
            value = expression.get("value")
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
                raise ValueError("equals comparisons require a tag value of 1 to 256 characters")
            normalized["value"] = value.strip()
        return normalized
    if kind != "group" or expression.get("operator") not in {"AND", "OR"}:
        raise ValueError("filter groups require an AND or OR operator")
    conditions = expression.get("conditions")
    if not isinstance(conditions, list) or not conditions or len(conditions) > 20:
        raise ValueError("filter groups require 1 to 20 conditions")
    return {"kind": "group", "operator": expression["operator"],
            "conditions": [_validate_filter_expression(item, depth + 1, counter)
                           for item in conditions]}


def _compile_filter(expression, json_expression, prefix="filter"):
    """Compile a validated filter using only fixed SQL and bound user values."""
    arguments = {}
    sequence = [0]

    def compile_node(node):
        if node["kind"] == "group":
            parts = [compile_node(child) for child in node["conditions"]]
            return "(" + f' {node["operator"]} '.join(parts) + ")"
        index = sequence[0]
        sequence[0] += 1
        key_name = f"{prefix}_key_{index}"
        arguments[key_name] = node["key"]
        operator = node["operator"]
        if operator == "EXISTS":
            return f"({json_expression} ? :{key_name})"
        if operator == "NOT_EXISTS":
            return f"(NOT ({json_expression} ? :{key_name}))"
        value_name = f"{prefix}_value_{index}"
        arguments[value_name] = node["value"]
        if operator == "EQUALS":
            return f"(({json_expression} ->> :{key_name}) = :{value_name})"
        return (f"(({json_expression} ? :{key_name}) AND "
                f"({json_expression} ->> :{key_name}) <> :{value_name})")

    return compile_node(expression), arguments


def _stored_expression(limit):
    expression = limit.get("filter_expression")
    if isinstance(expression, str):
        expression = json.loads(expression)
    if expression:
        return _validate_filter_expression(expression)
    return _validate_filter_expression({"kind": "group", "operator": "AND",
        "conditions": [{"kind": "tag", "key": limit["tag_key"],
                        "operator": "EQUALS", "value": limit["tag_value"]}]})


def _valid_account_topic(account) -> bool:
    topic = account.get("sns_topic_arn")
    parts = topic.split(":", 5) if topic else []
    return (len(parts) == 6 and parts[0] == "arn" and parts[2] == "sns"
            and parts[4] == account["provider_account_id"]
            and bool(parts[3]) and bool(parts[5]))


def _month_bounds(at: datetime) -> tuple[datetime, datetime]:
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


def _team_alert_snapshot(connection, account_id, team, period_start,
                         data_as_of):
    """Return a fail-closed MTD snapshot; missing evidence is never treated as zero."""
    expression = _stored_expression(team)
    token = f"alarm_{str(team['id']).replace('-', '')}"
    current_sql, current_args = _compile_filter(
        expression, "COALESCE(r.metadata->'tags','{}'::jsonb)", token + "_current")
    cost_sql, cost_args = _compile_filter(expression, "c.tags", token + "_cost")
    monitoring_start = max(period_start, team["created_at"])
    baseline = (Decimal(team["baseline_amount_usd"])
                if team["created_at"].year == period_start.year
                and team["created_at"].month == period_start.month
                else Decimal("0"))
    args = {"account": account_id, "start": monitoring_start, "end": data_as_of,
            **current_args, **cost_args}
    snapshot = dict(connection.execute(text(f"""WITH current_members AS (
          SELECT r.id, r.first_seen, r.provider_resource_type,
                 r.metadata #>> '{{pricing,status}}' AS pricing_status
          FROM resources r WHERE r.cloud_account_id=:account
            AND r.deleted_at IS NULL AND {current_sql}
        ), matching_costs AS (
          SELECT c.* FROM observed_costs c JOIN resources r ON r.id=c.resource_id
          WHERE r.cloud_account_id=:account AND c.usage_end>:start
            AND c.usage_start<:end AND {cost_sql}
        )
        SELECT
          (SELECT count(*) FROM current_members) AS current_resources,
          (SELECT count(*) FROM current_members
             WHERE pricing_status IS DISTINCT FROM 'COMPLETE') AS ineligible_rates,
          (SELECT count(*) FROM current_members
             WHERE provider_resource_type NOT IN ('ec2','ebs')) AS unsupported_resources,
          (SELECT count(*) FROM current_members
             WHERE first_seen > :start) AS resources_first_seen_after_period_start,
          count(*) AS intervals,
          count(*) FILTER (WHERE amount_usd IS NULL) AS unresolved_intervals,
          count(DISTINCT resource_id) AS resources_with_intervals,
          min(usage_start) AS first_interval,
          max(usage_end) AS last_interval,
          SUM(amount_usd * EXTRACT(EPOCH FROM
            (LEAST(usage_end,:end)-GREATEST(usage_start,:start))) /
            EXTRACT(EPOCH FROM (usage_end-usage_start))) AS cost_usd
        FROM matching_costs"""), args).mappings().one())
    reason = None
    snapshot["monitoring_started_at"] = monitoring_start
    snapshot["baseline_amount_usd"] = baseline
    snapshot["observed_cost_usd"] = snapshot["cost_usd"]
    snapshot["cost_usd"] = (baseline + Decimal(snapshot["cost_usd"])
                            if snapshot["cost_usd"] is not None else None)
    if snapshot["current_resources"] == 0:
        reason = "no_matching_resources_to_verify"
    elif snapshot["unsupported_resources"]:
        reason = "team_contains_services_without_complete_cost_models"
    elif snapshot["ineligible_rates"]:
        reason = "team_contains_partial_assumed_or_unresolved_rates"
    elif snapshot["resources_first_seen_after_period_start"]:
        reason = "inventory_history_incomplete_after_monitoring_start"
    elif snapshot["intervals"] == 0:
        reason = "no_month_to_date_usage_intervals"
    elif snapshot["unresolved_intervals"]:
        reason = "month_to_date_usage_contains_unresolved_intervals"
    elif snapshot["resources_with_intervals"] < snapshot["current_resources"]:
        reason = "one_or_more_resources_have_no_cost_intervals"
    elif snapshot["first_interval"] is None or snapshot["first_interval"] > monitoring_start:
        reason = "cost_history_incomplete_after_monitoring_start"
    elif snapshot["last_interval"] is None or data_as_of - snapshot["last_interval"] > timedelta(minutes=10):
        reason = "cost_data_is_stale"
    snapshot["calculation_coverage"] = "COMPLETE"
    if team["allow_partial_alerts"] and reason:
        observed = (Decimal(snapshot["observed_cost_usd"])
                    if snapshot["observed_cost_usd"] is not None else Decimal("0"))
        snapshot["observed_cost_usd"] = observed
        snapshot["cost_usd"] = baseline + observed
        snapshot["calculation_coverage"] = "PARTIAL_OBSERVED"
        reason = None
    return snapshot, reason


def _record_alert_decision(connection, team, period_start, threshold,
                           data_as_of, cost, blocked_reason):
    key = {"team": team["id"], "start": period_start,
           "version": team["configuration_version"], "threshold": threshold}
    existing = connection.execute(text("""SELECT confirmation_count,
        last_data_as_of FROM pilot_alert_evaluations WHERE team_id=:team
        AND period_start=:start AND configuration_version=:version
        AND threshold_percent=:threshold FOR UPDATE"""), key).mappings().first()
    event_exists = bool(connection.execute(text("""SELECT 1 FROM pilot_threshold_events
        WHERE team_id=:team AND period_start=:start
          AND configuration_version=:version
          AND threshold_percent=:threshold"""), key).first())
    decision, confirmations = next_decision(
        existing_count=existing["confirmation_count"] if existing else 0,
        last_data_as_of=existing["last_data_as_of"] if existing else None,
        event_exists=event_exists, data_as_of=data_as_of,
        blocked_reason=blocked_reason, cost=cost,
        limit=Decimal(team["amount_usd"]), threshold=Decimal(threshold))
    connection.execute(text("""INSERT INTO pilot_alert_evaluations
        (team_id,period_start,configuration_version,threshold_percent,
         confirmation_count,last_data_as_of,last_decision,last_reason)
        VALUES (:team,:start,:version,:threshold,:confirmations,:as_of,
                :decision,:reason)
        ON CONFLICT (team_id,period_start,configuration_version,threshold_percent)
        DO UPDATE SET confirmation_count=excluded.confirmation_count,
          last_data_as_of=excluded.last_data_as_of,
          last_decision=excluded.last_decision,last_reason=excluded.last_reason,
          updated_at=now()"""), {**key, "confirmations": confirmations,
            "as_of": data_as_of, "decision": decision, "reason": blocked_reason})
    return decision


def _evaluate_and_deliver_alerts(account_id: UUID, account) -> dict:
    data_as_of = datetime.now(timezone.utc)
    period_start, period_end = _month_bounds(data_as_of)
    decisions = []
    with engine.begin() as connection:
        stored_account = dict(connection.execute(text("""SELECT * FROM cloud_accounts
            WHERE id=:id FOR UPDATE"""), {"id": account_id}).mappings().one())
        teams = rows(connection.execute(text("""SELECT * FROM pilot_team_limits
            WHERE cloud_account_id=:account AND deleted_at IS NULL
            ORDER BY created_at FOR UPDATE"""), {"account": account_id}))
        for team in teams:
            snapshot, reason = _team_alert_snapshot(
                connection, account_id, team, period_start, data_as_of)
            if not _valid_account_topic(stored_account):
                reason = reason or "approved_sns_topic_not_configured"
            cost = snapshot["cost_usd"]
            if cost is None:
                reason = reason or "complete_month_to_date_cost_is_unavailable"
                cost = Decimal("0")
            for threshold in (Decimal("80"), Decimal("100")):
                decision = _record_alert_decision(
                    connection, team, period_start, threshold, data_as_of,
                    Decimal(cost), reason)
                if decision == "READY":
                    usage = Decimal(cost) / Decimal(team["amount_usd"]) * Decimal("100")
                    connection.execute(text("""INSERT INTO pilot_threshold_events
                        (cloud_account_id,team_id,configuration_version,period_start,
                         period_end,threshold_percent,estimated_cost_usd,limit_usd,
                         usage_percent,pricing_coverage,calculation_coverage,
                         baseline_amount_usd,observed_cost_usd,monitoring_started_at,
                         period_basis,status,topic_arn)
                        VALUES (:account,:team,:version,:start,:end,:threshold,
                          :cost,:limit,:usage,1,:coverage,:baseline,:observed,
                          :monitoring,:basis,'PENDING',:topic)
                        ON CONFLICT DO NOTHING"""), {"account": account_id,
                        "team": team["id"], "version": team["configuration_version"],
                        "start": period_start, "end": period_end,
                        "threshold": threshold, "cost": cost,
                        "limit": team["amount_usd"], "usage": usage,
                        "coverage": snapshot["calculation_coverage"],
                        "baseline": snapshot["baseline_amount_usd"],
                        "observed": snapshot["observed_cost_usd"],
                        "monitoring": snapshot["monitoring_started_at"],
                        "basis": ("DECLARED_BASELINE_PLUS_MONITORING"
                                  if (snapshot["monitoring_started_at"] > period_start
                                      or snapshot["baseline_amount_usd"] > 0)
                                  else "CALENDAR_MONTH"),
                        "topic": stored_account["sns_topic_arn"]})
                decisions.append({"team_id": str(team["id"]),
                                  "threshold_percent": str(threshold),
                                  "decision": decision, "reason": reason})
    deliveries = _deliver_pending_alerts(account_id, account)
    return {"active": True, "evaluations": decisions, "deliveries": deliveries,
            "safety_policy": "per_team_complete_or_partial_observed_mode"}


def _deliver_pending_alerts(account_id: UUID, account) -> list[dict]:
    delivered = []
    with engine.connect() as connection:
        events = rows(connection.execute(text("""SELECT e.*, t.name AS team_name
            FROM pilot_threshold_events e JOIN pilot_team_limits t ON t.id=e.team_id
            WHERE e.cloud_account_id=:account
              AND e.status IN ('PENDING','RETRY') AND e.next_attempt_at<=now()
            ORDER BY e.created_at LIMIT 20"""), {"account": account_id}))
    for event in events:
        payload = {"event_type": "COST_THRESHOLD_EXCEEDED",
            "event_id": str(event["id"]),
            "account_id": account["provider_account_id"],
            "account_name": account["display_name"],
            "team_id": str(event["team_id"]), "team_name": event["team_name"],
            "period": event["period_start"].strftime("%Y-%m"),
            "threshold_percent": str(event["threshold_percent"]),
            "limit_usd": str(event["limit_usd"]),
            "estimated_cost_usd": str(event["estimated_cost_usd"]),
            "declared_baseline_usd": str(event["baseline_amount_usd"]),
            "observed_cost_after_monitoring_start_usd": str(event["observed_cost_usd"]),
            "monitoring_started_at": event["monitoring_started_at"].isoformat(),
            "period_basis": event["period_basis"],
            "usage_percent": str(event["usage_percent"]),
            "baseline_source": (
                "USER_DECLARED"
                if event["period_basis"] == "DECLARED_BASELINE_PLUS_MONITORING"
                else "NOT_APPLICABLE"),
            "calculation_coverage": event["calculation_coverage"],
            "is_test": False,
            "automatic_threshold_alert": True,
            "triggered_at": event["created_at"].isoformat()}
        result = publish_threshold_alert(_config(account), event["topic_arn"], payload)
        with engine.begin() as connection:
            locked = connection.execute(text("""SELECT status,attempt_count
                FROM pilot_threshold_events WHERE id=:id FOR UPDATE"""),
                {"id": event["id"]}).mappings().one()
            if locked["status"] not in ("PENDING", "RETRY"):
                continue
            attempts = locked["attempt_count"] + 1
            if result["published"]:
                status, next_at = "PUBLISHED", datetime.now(timezone.utc)
            else:
                status = "FAILED" if attempts >= 5 else "RETRY"
                delay = min(300, 30 * (2 ** (attempts - 1)))
                next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            connection.execute(text("""UPDATE pilot_threshold_events
                SET status=:status,attempt_count=:attempts,next_attempt_at=:next,
                  sns_message_id=:message,error_message=:error,
                  published_at=CASE WHEN :status='PUBLISHED' THEN now() ELSE NULL END
                WHERE id=:id"""), {"status": status, "attempts": attempts,
                  "next": next_at, "message": result.get("message_id"),
                  "error": result.get("error"), "id": event["id"]})
        delivered.append({"event_id": str(event["id"]), "status": status,
                          "message_id": result.get("message_id")})
    return delivered


def _team_alert_status(connection, team_id, period_start):
    event = connection.execute(text("""SELECT status,threshold_percent,
        created_at,published_at FROM pilot_threshold_events
        WHERE team_id=:team AND period_start=:start
        ORDER BY threshold_percent DESC LIMIT 1"""),
        {"team": team_id, "start": period_start}).mappings().first()
    evaluations = rows(connection.execute(text("""SELECT threshold_percent,
        confirmation_count,last_decision,last_reason,updated_at
        FROM pilot_alert_evaluations WHERE team_id=:team AND period_start=:start
        ORDER BY threshold_percent"""), {"team": team_id, "start": period_start}))
    if event:
        return {"status": event["status"], "threshold_percent": str(event["threshold_percent"]),
                "published_at": event["published_at"], "evaluations": evaluations}
    if not evaluations:
        return {"status": "NOT_YET_EVALUATED", "evaluations": []}
    priority = next((item for item in evaluations if item["last_decision"] == "BLOCKED"),
                    evaluations[0])
    return {"status": priority["last_decision"],
            "reason": priority["last_reason"], "evaluations": evaluations}


def _team_baseline_for_window(team, start: datetime, end: datetime) -> Decimal:
    created = team["created_at"]
    month_start = created.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start <= month_start < end and start <= created < end:
        return Decimal(team["baseline_amount_usd"])
    return Decimal("0")


@app.post("/api/v1/accounts/{account_id}/teams", status_code=201)
@app.post("/api/v1/accounts/{account_id}/limits", status_code=201, include_in_schema=False)
def create_pilot_limit(account_id: UUID, payload: PilotLimitCreate):
    account = _account(account_id)
    with engine.begin() as connection:
        result = connection.execute(text("""INSERT INTO pilot_team_limits
            (cloud_account_id,name,tag_key,tag_value,amount_usd,
             baseline_amount_usd,allow_partial_alerts,filter_expression)
            VALUES (:account,:name,'','',:amount,:baseline,:allow_partial,
                   CAST(:filter AS jsonb))
            RETURNING id"""),
            {"account": account_id, "name": payload.name,
             "amount": payload.amount_usd,
             "baseline": payload.baseline_amount_usd,
             "allow_partial": payload.allow_partial_alerts,
             "filter": json.dumps(payload.filter_expression)}).scalar_one()
    return {"id": result, "notification_enabled": _valid_account_topic(account),
            "automatic_alerts_enabled": True,
            "evaluation_status": "NOT_YET_EVALUATED"}


class TeamPreview(BaseModel):
    filter_expression: dict

    @field_validator("filter_expression")
    @classmethod
    def valid_filter_expression(cls, value):
        return _validate_filter_expression(value)


@app.post("/api/v1/accounts/{account_id}/teams/preview")
def preview_team(account_id: UUID, payload: TeamPreview):
    _account(account_id)
    predicate, filter_args = _compile_filter(
        payload.filter_expression, "COALESCE(metadata->'tags','{}'::jsonb)", "preview")
    with engine.connect() as connection:
        count = connection.execute(text(f"""SELECT count(*) FROM resources
            WHERE cloud_account_id=:id AND deleted_at IS NULL AND {predicate}"""),
            {"id": account_id, **filter_args}).scalar_one()
        matches = rows(connection.execute(text(f"""SELECT id, name,
            provider_resource_id, provider_resource_type, region, state,
            metadata #>> '{{pricing,status}}' AS pricing_status
            FROM resources WHERE cloud_account_id=:id AND deleted_at IS NULL
            AND {predicate} ORDER BY provider_resource_type, name LIMIT 25"""),
            {"id": account_id, **filter_args}))
    return {"matching_resources": count, "sample": matches, "sample_limit": 25}


def _team(account_id, team_id, connection):
    team = connection.execute(text("""SELECT * FROM pilot_team_limits
        WHERE id=:team AND cloud_account_id=:account AND deleted_at IS NULL"""),
        {"team": team_id, "account": account_id}).mappings().first()
    if not team:
        raise HTTPException(404, "team not found")
    return dict(team)


@app.get("/api/v1/accounts/{account_id}/teams")
def list_teams(account_id: UUID, start_date: date | None = None,
               end_date: date | None = None):
    _account(account_id)
    start, end = _reporting_window(start_date, end_date, datetime.now(timezone.utc))
    subtotal = "SUM(c.amount_usd * EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) / EXTRACT(EPOCH FROM (c.usage_end-c.usage_start)))"
    with engine.connect() as connection:
        teams = rows(connection.execute(text("""SELECT * FROM pilot_team_limits
            WHERE cloud_account_id=:id AND deleted_at IS NULL
            ORDER BY created_at"""), {"id": account_id}))
        for team in teams:
            expression = _stored_expression(team)
            token = str(team["id"]).replace("-", "")
            current_sql, current_args = _compile_filter(
                expression, "COALESCE(r.metadata->'tags','{}'::jsonb)", f"current_{token}")
            cost_sql, cost_args = _compile_filter(expression, "c.tags", f"cost_{token}")
            inventory = connection.execute(text(f"""SELECT count(*) AS resource_count,
                count(*) FILTER (WHERE r.metadata #>> '{{pricing,status}}' IN ('COMPLETE','PARTIAL','ESTIMATED')) AS resources_with_rates
                FROM resources r WHERE r.cloud_account_id=:id AND r.deleted_at IS NULL
                AND {current_sql}"""), {"id": account_id, **current_args}).mappings().one()
            observation_start = max(start, team["created_at"])
            costs = connection.execute(text(f"""SELECT count(*) AS intervals,
                count(*) FILTER (WHERE c.amount_usd IS NOT NULL) AS priced_intervals,
                count(*) FILTER (WHERE c.amount_usd IS NULL) AS unresolved_intervals,
                {subtotal} AS observed_subtotal_usd
                FROM observed_costs c JOIN resources r ON r.id=c.resource_id
                WHERE r.cloud_account_id=:id AND c.usage_end>:start AND c.usage_start<:end
                AND {cost_sql}"""), {"id": account_id, "start": observation_start,
                                      "end": end, **cost_args}).mappings().one()
            team["filter_expression"] = expression
            team.update(dict(inventory)); team.update(dict(costs))
            team["amount_usd"] = str(team["amount_usd"])
            value = team["observed_subtotal_usd"]
            team["observed_subtotal_usd"] = str(value) if value is not None else None
            baseline = _team_baseline_for_window(team, start, end)
            team["baseline_amount_usd"] = str(baseline)
            team["effective_total_usd"] = (str(Decimal(value) + baseline)
                                            if value is not None else None)
            alert = _team_alert_status(connection, team["id"],
                                       start.replace(day=1, hour=0, minute=0,
                                                     second=0, microsecond=0))
            team["alert_status"] = alert
            team["evaluation_status"] = alert["status"]
    return {"period_start": start, "period_end": end, "teams": teams,
            "alerts_evaluated": True,
            "automatic_alerts_enabled": True}


@app.get("/api/v1/accounts/{account_id}/teams/{team_id}/resources")
def team_resources(account_id: UUID, team_id: UUID,
                   start_date: date | None = None, end_date: date | None = None):
    _account(account_id)
    start, end = _reporting_window(start_date, end_date, datetime.now(timezone.utc))
    with engine.connect() as connection:
        team = _team(account_id, team_id, connection)
        expression = _stored_expression(team)
        predicate, filter_args = _compile_filter(
            expression, "COALESCE(r.metadata->'tags','{}'::jsonb)", "resource")
        historic, historic_args = _compile_filter(expression, "c.tags", "history")
        membership, membership_args = _compile_filter(
            expression, "hc.tags", "membership_history")
        matches = rows(connection.execute(text(f"""SELECT r.id, r.name,
            r.provider_resource_id, r.provider_resource_type, r.region,
            r.availability_zone, r.state, r.last_seen, r.metadata,
            ({predicate}) AND r.deleted_at IS NULL AS current_member,
            count(c.resource_id) AS intervals,
            count(c.resource_id) FILTER (WHERE c.amount_usd IS NULL) AS unresolved_intervals,
            COALESCE(SUM(EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start)))
                ) FILTER (WHERE c.amount_usd IS NOT NULL),0) AS priced_seconds,
            SUM(c.amount_usd * EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) /
                EXTRACT(EPOCH FROM (c.usage_end-c.usage_start))) AS observed_subtotal_usd
            FROM resources r LEFT JOIN observed_costs c ON c.resource_id=r.id
              AND c.usage_end>:start AND c.usage_start<:end AND {historic}
            WHERE r.cloud_account_id=:account AND (
              (r.deleted_at IS NULL AND {predicate}) OR EXISTS (
                SELECT 1 FROM observed_costs hc WHERE hc.resource_id=r.id
                  AND hc.usage_end>:start AND hc.usage_start<:end AND {membership}
              )
            )
            GROUP BY r.id, r.name, r.provider_resource_id, r.provider_resource_type,
              r.region, r.availability_zone, r.state, r.last_seen, r.metadata,
              r.deleted_at
            ORDER BY r.provider_resource_type, r.name"""),
            {"account": account_id, "start": start, "end": end,
             **filter_args, **historic_args, **membership_args}))
        for item in matches:
            item["priced_seconds"] = str(item["priced_seconds"])
            value = item["observed_subtotal_usd"]
            item["observed_subtotal_usd"] = str(value) if value is not None else None
        bucket = "hour" if end - start <= timedelta(days=2) else "day"
        step = f"1 {bucket}"
        trend = rows(connection.execute(text(f"""WITH buckets AS (
            SELECT bucket_start,
              LEAST(bucket_start + interval '{step}', :end) AS bucket_end
            FROM generate_series(date_trunc('{bucket}', :start),
              :end - interval '1 microsecond', interval '{step}') AS series(bucket_start)
          ), matching AS (
            SELECT c.* FROM observed_costs c
            JOIN resources r ON r.id=c.resource_id
            WHERE r.cloud_account_id=:account AND c.usage_end>:start
              AND c.usage_start<:end AND {historic}
          )
          SELECT b.bucket_start,
            SUM(m.amount_usd * EXTRACT(EPOCH FROM (
              LEAST(m.usage_end,b.bucket_end)-GREATEST(m.usage_start,b.bucket_start))) /
              EXTRACT(EPOCH FROM (m.usage_end-m.usage_start)))
              FILTER (WHERE m.amount_usd IS NOT NULL) AS amount_usd,
            count(m.resource_id) FILTER (WHERE m.amount_usd IS NOT NULL) AS priced_intervals,
            count(m.resource_id) FILTER (WHERE m.amount_usd IS NULL) AS unresolved_intervals
          FROM buckets b LEFT JOIN matching m ON m.usage_end>b.bucket_start
            AND m.usage_start<b.bucket_end
          GROUP BY b.bucket_start ORDER BY b.bucket_start"""),
            {"account": account_id, "start": start, "end": end,
             **historic_args}))
        for item in trend:
            value = item["amount_usd"]
            item["amount_usd"] = str(value) if value is not None else None
            if item["priced_intervals"] and item["unresolved_intervals"]:
                item["coverage_status"] = "PARTIAL"
            elif item["priced_intervals"]:
                item["coverage_status"] = "PRICED"
            elif item["unresolved_intervals"]:
                item["coverage_status"] = "UNRESOLVED"
            else:
                item["coverage_status"] = "NO_OBSERVATION"
    return {"team_id": team_id, "team_name": team["name"],
            "filter_expression": expression, "period_start": start,
            "period_end": end, "trend_bucket": bucket,
            "cost_trend": trend, "resources": matches}


@app.get("/api/v1/accounts/{account_id}/teams/{team_id}/resources/{resource_id}/cost-intervals")
def team_resource_cost_intervals(account_id: UUID, team_id: UUID, resource_id: UUID,
                                 start_date: date | None = None,
                                 end_date: date | None = None,
                                 limit: int = Query(default=200, ge=1, le=500)):
    _account(account_id)
    start, end = _reporting_window(start_date, end_date, datetime.now(timezone.utc))
    with engine.connect() as connection:
        team = _team(account_id, team_id, connection)
        expression = _stored_expression(team)
        current, current_args = _compile_filter(
            expression, "COALESCE(r.metadata->'tags','{}'::jsonb)", "detail_current")
        membership, membership_args = _compile_filter(
            expression, "hc.tags", "detail_membership")
        resource = connection.execute(text(f"""SELECT r.id, r.name,
            r.provider_resource_id, r.provider_resource_type, r.region, r.state,
            (({current}) AND r.deleted_at IS NULL) AS current_member,
            EXISTS (SELECT 1 FROM observed_costs hc WHERE hc.resource_id=r.id
              AND hc.usage_end>:start AND hc.usage_start<:end AND {membership})
              AS historical_member
            FROM resources r WHERE r.id=:resource AND r.cloud_account_id=:account
              AND ((r.deleted_at IS NULL AND {current}) OR EXISTS (
                SELECT 1 FROM observed_costs hc WHERE hc.resource_id=r.id
                  AND hc.usage_end>:start AND hc.usage_start<:end AND {membership}
              ))"""),
            {"resource": resource_id, "account": account_id,
             "start": start, "end": end, **current_args,
             **membership_args}).mappings().first()
        if not resource:
            raise HTTPException(404, "resource is not a member of this team in the selected period")
        historic, historic_args = _compile_filter(expression, "c.tags", "detail_history")
        args = {"resource": resource_id, "start": start, "end": end,
                "limit": limit, **historic_args}
        summary = dict(connection.execute(text(f"""SELECT count(*) AS intervals,
            count(*) FILTER (WHERE c.amount_usd IS NOT NULL) AS priced_intervals,
            count(*) FILTER (WHERE c.amount_usd IS NULL) AS unresolved_intervals,
            COALESCE(SUM(EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start)))
                ) FILTER (WHERE c.amount_usd IS NOT NULL),0) AS priced_seconds,
            SUM(c.amount_usd * EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) /
                EXTRACT(EPOCH FROM (c.usage_end-c.usage_start))) AS observed_subtotal_usd
            FROM observed_costs c WHERE c.resource_id=:resource
              AND c.usage_end>:start AND c.usage_start<:end AND {historic}"""), args).mappings().one())
        intervals = rows(connection.execute(text(f"""SELECT
            GREATEST(c.usage_start,:start) AS usage_start,
            LEAST(c.usage_end,:end) AS usage_end,
            EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) AS duration_seconds,
            CASE WHEN c.amount_usd IS NULL THEN NULL ELSE
              c.amount_usd * EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) /
              EXTRACT(EPOCH FROM (c.usage_end-c.usage_start)) END AS amount_usd,
            CASE WHEN c.amount_usd IS NULL THEN NULL ELSE
              c.amount_usd * 3600 / EXTRACT(EPOCH FROM (c.usage_end-c.usage_start))
              END AS effective_hourly_usd,
            c.basis, c.reason, c.tags
            FROM observed_costs c WHERE c.resource_id=:resource
              AND c.usage_end>:start AND c.usage_start<:end AND {historic}
            ORDER BY c.usage_start DESC LIMIT :limit"""), args))
    summary["priced_seconds"] = str(summary["priced_seconds"])
    value = summary["observed_subtotal_usd"]
    summary["observed_subtotal_usd"] = str(value) if value is not None else None
    for interval in intervals:
        interval["duration_seconds"] = str(interval["duration_seconds"])
        value = interval["amount_usd"]
        interval["amount_usd"] = str(value) if value is not None else None
        value = interval["effective_hourly_usd"]
        interval["effective_hourly_usd"] = str(value) if value is not None else None
    return {"team_id": team_id, "team_name": team["name"],
            "resource": dict(resource), "period_start": start, "period_end": end,
            "summary": summary, "intervals": intervals, "returned_limit": limit,
            "cost_status": "PARTIAL_OBSERVED_ONLY"}


@app.delete("/api/v1/accounts/{account_id}/teams/{team_id}", status_code=204)
def delete_team(account_id: UUID, team_id: UUID):
    _account(account_id)
    with engine.begin() as connection:
        result = connection.execute(text("""UPDATE pilot_team_limits
            SET deleted_at=now() WHERE id=:team AND cloud_account_id=:account
              AND deleted_at IS NULL"""),
            {"team": team_id, "account": account_id})
        if result.rowcount != 1:
            raise HTTPException(404, "team not found")
    return Response(status_code=204)


def _reporting_window(start_date: date | None, end_date: date | None,
                      now: datetime) -> tuple[datetime, datetime]:
    if (start_date is None) != (end_date is None):
        raise HTTPException(422, "start_date and end_date must be provided together")
    if start_date is None:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
    days = (end_date - start_date).days + 1
    if days < 1 or days > 90:
        raise HTTPException(422, "date range must contain 1 to 90 ordered UTC days")
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = min(datetime.combine(end_date + timedelta(days=1), time.min,
                               tzinfo=timezone.utc), now)
    if start >= end:
        raise HTTPException(422, "date range must begin before the current UTC time")
    return start, end


def _five_minute_window(now: datetime, window_minutes: int) -> tuple[datetime, datetime]:
    if window_minutes < 60 or window_minutes > 10_080:
        raise HTTPException(422, "trend window must be between 1 hour and 7 days")
    return now - timedelta(minutes=window_minutes), now


@app.get("/api/v1/accounts/{account_id}/cost-trend")
def five_minute_cost_trend(
    account_id: UUID,
    window_minutes: int = Query(default=1_440, ge=60, le=10_080),
):
    """Return a bounded five-minute observed-cost series without zero-filling gaps."""
    _account(account_id)
    start, end = _five_minute_window(datetime.now(timezone.utc), window_minutes)
    with engine.connect() as connection:
        trend = rows(connection.execute(text("""WITH buckets AS (
            SELECT bucket_start,
              LEAST(bucket_start + interval '5 minutes', :end) AS bucket_end
            FROM generate_series(
              :start,
              :end - interval '1 microsecond',
              interval '5 minutes') AS series(bucket_start)
          ), matching AS (
            SELECT c.* FROM observed_costs c
            JOIN resources r ON r.id=c.resource_id
            WHERE r.cloud_account_id=:account AND c.usage_end>:start
              AND c.usage_start<:end
          )
          SELECT b.bucket_start,
            SUM(m.amount_usd * EXTRACT(EPOCH FROM (
              LEAST(m.usage_end,b.bucket_end)-GREATEST(m.usage_start,b.bucket_start))) /
              EXTRACT(EPOCH FROM (m.usage_end-m.usage_start)))
              FILTER (WHERE m.amount_usd IS NOT NULL) AS amount_usd,
            count(m.resource_id) FILTER (WHERE m.amount_usd IS NOT NULL)
              AS priced_intervals,
            count(m.resource_id) FILTER (WHERE m.amount_usd IS NULL)
              AS unresolved_intervals
          FROM buckets b LEFT JOIN matching m ON m.usage_end>b.bucket_start
            AND m.usage_start<b.bucket_end
          GROUP BY b.bucket_start ORDER BY b.bucket_start"""),
            {"account": account_id, "start": start, "end": end}))
    for item in trend:
        value = item["amount_usd"]
        item["amount_usd"] = str(value) if value is not None else None
        if item["priced_intervals"] and item["unresolved_intervals"]:
            item["coverage_status"] = "PARTIAL"
        elif item["priced_intervals"]:
            item["coverage_status"] = "PRICED"
        elif item["unresolved_intervals"]:
            item["coverage_status"] = "UNRESOLVED"
        else:
            item["coverage_status"] = "NO_OBSERVATION"
    return {"period_start": start, "period_end": end,
            "bucket_minutes": 5, "window_minutes": window_minutes,
            "points": trend, "complete_account_cost": False}


@app.get("/api/v1/accounts/{account_id}/overview")
def live_overview(account_id: UUID, start_date: date | None = None,
                  end_date: date | None = None):
    account = _account(account_id)
    now = datetime.now(timezone.utc)
    start, end = _reporting_window(start_date, end_date, now)
    # Clip intervals at the selected UTC range; decimal math stays in PostgreSQL.
    subtotal = "SUM(c.amount_usd * EXTRACT(EPOCH FROM (LEAST(c.usage_end,:end)-GREATEST(c.usage_start,:start))) / EXTRACT(EPOCH FROM (c.usage_end-c.usage_start)))"
    scope = "FROM observed_costs c JOIN resources r ON r.id=c.resource_id WHERE r.cloud_account_id=:id AND c.usage_end>:start AND c.usage_start<:end"
    args = {"id": account_id, "start": start, "end": end}
    with engine.connect() as connection:
        services = rows(connection.execute(text("""SELECT provider_resource_type AS service,
            count(*) AS resources, min(first_seen) AS first_observed,
            max(last_seen) AS last_observed,
            count(*) FILTER (WHERE metadata #>> '{pricing,status}' = 'COMPLETE') AS complete_rates,
            count(*) FILTER (WHERE metadata #>> '{pricing,status}' = 'PARTIAL') AS partial_rates,
            count(*) FILTER (WHERE metadata #>> '{pricing,status}' = 'ESTIMATED') AS estimated_rates,
            count(*) FILTER (WHERE metadata #>> '{pricing,status}' IN ('UNRESOLVED','NOT_APPLICABLE')
                OR metadata #>> '{pricing,status}' IS NULL) AS unresolved_rates
            FROM resources WHERE cloud_account_id=:id AND deleted_at IS NULL
            GROUP BY provider_resource_type ORDER BY provider_resource_type"""), {"id": account_id}))
        costs = rows(connection.execute(text(f"SELECT c.basis, count(*) AS intervals, {subtotal} AS amount_usd {scope} GROUP BY c.basis"), args))
        summary = dict(connection.execute(text(f"""SELECT count(*) AS intervals,
            count(*) FILTER (WHERE c.amount_usd IS NOT NULL) AS priced_intervals,
            count(*) FILTER (WHERE c.amount_usd IS NULL) AS unresolved_intervals,
            {subtotal} AS observed_subtotal_usd {scope}"""), args).mappings().one())
        service_costs = rows(connection.execute(text(f"""SELECT r.provider_resource_type AS service,
            count(*) AS intervals,
            count(*) FILTER (WHERE c.amount_usd IS NOT NULL) AS priced_intervals,
            count(*) FILTER (WHERE c.amount_usd IS NULL) AS unresolved_intervals,
            {subtotal} AS amount_usd {scope}
            GROUP BY r.provider_resource_type ORDER BY amount_usd DESC NULLS LAST"""), args))
        top_resources = rows(connection.execute(text(f"""SELECT r.id, r.name,
            r.provider_resource_id, r.provider_resource_type AS service,
            r.region, r.state, r.metadata #>> '{{pricing,status}}' AS pricing_status,
            {subtotal} AS amount_usd
            {scope} AND c.amount_usd IS NOT NULL
            GROUP BY r.id, r.name, r.provider_resource_id,
              r.provider_resource_type, r.region, r.state, r.metadata
            ORDER BY amount_usd DESC LIMIT 10"""), args))
        inventory = dict(connection.execute(text("""SELECT count(*) AS resources,
            count(*) FILTER (WHERE COALESCE(metadata->'tags','{}'::jsonb) = '{}'::jsonb) AS untagged_resources
            FROM resources WHERE cloud_account_id=:id AND deleted_at IS NULL"""), {"id": account_id}).mappings().one())
        limits = rows(connection.execute(text("SELECT * FROM pilot_team_limits WHERE cloud_account_id=:id AND deleted_at IS NULL ORDER BY created_at"), {"id": account_id}))
        for limit in limits:
            expression = _stored_expression(limit)
            condition, condition_args = _compile_filter(
                expression, "c.tags", f"overview_{str(limit['id']).replace('-', '')}")
            observation_args = {**args, **condition_args,
                                "start": max(start, limit["created_at"])}
            amount = connection.execute(text(f"SELECT {subtotal} {scope} AND {condition}"),
                observation_args).scalar_one()
            limit["filter_expression"] = expression
            limit["observed_subtotal_usd"] = str(amount) if amount is not None else None
            limit["amount_usd"] = str(limit["amount_usd"])
            baseline = _team_baseline_for_window(limit, start, end)
            limit["baseline_amount_usd"] = str(baseline)
            limit["effective_total_usd"] = (str(Decimal(amount) + baseline)
                                             if amount is not None else None)
            alert = _team_alert_status(connection, limit["id"],
                                       now.replace(day=1, hour=0, minute=0,
                                                   second=0, microsecond=0))
            limit["alert_status"] = alert
            limit["evaluation_status"] = alert["status"]
    for cost in costs:
        cost["amount_usd"] = str(cost["amount_usd"]) if cost["amount_usd"] is not None else None
    summary["observed_subtotal_usd"] = str(summary["observed_subtotal_usd"]) if summary["observed_subtotal_usd"] is not None else None
    for collection in (service_costs, top_resources):
        for item in collection:
            for field in ("amount_usd",):
                if field in item and item[field] is not None:
                    item[field] = str(item[field])
    return {"account_name": account["display_name"], "period_start": start, "period_end": end,
            "last_collected_at": account["last_collected_at"], "last_error": account["last_error"],
            "services": services, "observed_costs": costs, "cost_summary": summary,
            "service_costs": service_costs,
            "top_resources": top_resources, "inventory": inventory, "limits": limits,
            "complete_account_cost_usd": None, "alerts_evaluated": True,
            "limitations": ["Supported EC2 compute and provisioned EBS dimensions are priced",
                "EFS and FSx values include matched storage dimensions only",
                "Continuous provisioning between polls is assumed",
                "No usage before the first observation is reconstructed",
                "EFS/FSx throughput, access, backup and transfer dimensions remain excluded",
                "Spot fallback assumes a 52% discount and is never alert eligible",
                "Automatic 80%/100% evaluation runs after every collection; incomplete post-monitoring evidence is blocked"]}
