from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import engine, initialize_database, ping_database, rows
from providers.aws.collector import (
    AwsAccountConfig,
    collect_resources,
    price_running_ec2,
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


app = FastAPI(title="CloudScope API", version="1.1.0", lifespan=lifespan)
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
                    connection_checked_at, last_collected_at, last_error, created_at
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
    discovered, failures = collect_resources(_config(account))
    prices = price_running_ec2(_config(account), discovered)
    complete_prices = sum(
        1 for item in prices.values() if item["status"] == "COMPLETE"
    )
    estimated_prices = sum(
        1 for item in prices.values() if item["status"] == "ESTIMATED"
    )
    with engine.begin() as connection:
        for item in discovered:
            metadata = {
                **item["metadata"],
                "pricing": prices.get(
                    item["provider_resource_id"], {"status": "NOT_APPLICABLE"}
                ),
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
            "unresolved": len(prices) - complete_prices - estimated_prices,
        },
        "alerts_evaluated": False,
        "alerts_reason": "complete usage cost intervals do not exist yet",
    }


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
