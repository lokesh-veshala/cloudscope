"""Database-backed, opt-in collection scheduler for the on-premises pilot."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from database import engine, initialize_database


API_URL = os.environ.get("CLOUDSCOPE_INTERNAL_API_URL", "http://api:8000").rstrip("/")
POLL_SECONDS = max(2, int(os.environ.get("CLOUDSCOPE_SCHEDULER_POLL_SECONDS", "10")))
LEASE_MINUTES = 15
MAX_ATTEMPTS = 3


def enqueue_due_jobs(now: datetime, limit: int = 25) -> int:
    """Schedule at most one active job per due account."""
    with engine.begin() as connection:
        accounts = connection.execute(text("""
            SELECT id, collection_interval_seconds
            FROM cloud_accounts a
            WHERE enabled=true AND connection_status='CONNECTED'
              AND collection_enabled=true
              AND (next_collection_at IS NULL OR next_collection_at <= :now)
              AND NOT EXISTS (
                SELECT 1 FROM collection_jobs j
                WHERE j.cloud_account_id=a.id AND j.status IN ('QUEUED','RUNNING'))
            ORDER BY next_collection_at NULLS FIRST
            FOR UPDATE OF a SKIP LOCKED LIMIT :limit
        """), {"now": now, "limit": limit}).mappings().all()
        for account in accounts:
            connection.execute(text("""INSERT INTO collection_jobs
                (cloud_account_id,collector,scheduled_for,status)
                VALUES (:account,'aws_inventory',:now,'QUEUED')"""),
                {"account": account["id"], "now": now})
            connection.execute(text("""UPDATE cloud_accounts
                SET next_collection_at=:next WHERE id=:id"""),
                {"next": now + timedelta(seconds=account["collection_interval_seconds"]),
                 "id": account["id"]})
        return len(accounts)


def claim_job(now: datetime):
    with engine.begin() as connection:
        connection.execute(text("""UPDATE collection_jobs
            SET status='QUEUED', scheduled_for=:now, lease_expires_at=NULL,
                error_code='LEASE_EXPIRED'
            WHERE status='RUNNING' AND lease_expires_at < :now
              AND attempt_count < :max_attempts"""),
            {"now": now, "max_attempts": MAX_ATTEMPTS})
        connection.execute(text("""UPDATE collection_jobs
            SET status='FAILED', finished_at=:now, lease_expires_at=NULL,
                error_code='LEASE_EXPIRED_MAX_ATTEMPTS'
            WHERE status='RUNNING' AND lease_expires_at < :now
              AND attempt_count >= :max_attempts"""),
            {"now": now, "max_attempts": MAX_ATTEMPTS})
        connection.execute(text("""UPDATE cloud_accounts a
            SET next_collection_at=:now + make_interval(secs => a.collection_interval_seconds)
            WHERE EXISTS (SELECT 1 FROM collection_jobs j
                WHERE j.cloud_account_id=a.id AND j.status='FAILED'
                  AND j.error_code='LEASE_EXPIRED_MAX_ATTEMPTS'
                  AND j.finished_at=:now)"""), {"now": now})
        job = connection.execute(text("""SELECT id, cloud_account_id, attempt_count
            FROM collection_jobs WHERE status='QUEUED' AND scheduled_for <= :now
            ORDER BY scheduled_for FOR UPDATE SKIP LOCKED LIMIT 1"""),
            {"now": now}).mappings().first()
        if not job:
            return None
        return dict(connection.execute(text("""UPDATE collection_jobs
            SET status='RUNNING', started_at=COALESCE(started_at,:now),
                attempt_count=attempt_count+1, lease_expires_at=:lease
            WHERE id=:id RETURNING id, cloud_account_id, attempt_count"""),
            {"id": job["id"], "now": now,
             "lease": now + timedelta(minutes=LEASE_MINUTES)}).mappings().one())


def collect_via_api(account_id) -> dict:
    request = urllib.request.Request(
        f"{API_URL}/api/v1/accounts/{account_id}/collect", method="POST",
        headers={"Content-Type": "application/json"}, data=b"{}")
    try:
        with urllib.request.urlopen(request, timeout=840) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"collection API returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError("collection API unavailable") from error


def complete_job(job, result: dict | None = None, error: Exception | None = None) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        if error is None:
            connection.execute(text("""UPDATE collection_jobs SET status='SUCCEEDED',
                finished_at=:now, lease_expires_at=NULL, error_code=NULL,
                details=CAST(:details AS jsonb) WHERE id=:id"""),
                {"now": now, "details": json.dumps(result or {}), "id": job["id"]})
            return
        if job["attempt_count"] < MAX_ATTEMPTS:
            delay = retry_delay_seconds(job["attempt_count"])
            connection.execute(text("""UPDATE collection_jobs SET status='QUEUED',
                scheduled_for=:retry, lease_expires_at=NULL, error_code='COLLECTION_FAILED',
                details=CAST(:details AS jsonb) WHERE id=:id"""),
                {"retry": now + timedelta(seconds=delay),
                 "details": json.dumps({"message": str(error)}), "id": job["id"]})
        else:
            connection.execute(text("""UPDATE collection_jobs SET status='FAILED',
                finished_at=:now, lease_expires_at=NULL, error_code='MAX_ATTEMPTS',
                details=CAST(:details AS jsonb) WHERE id=:id"""),
                {"now": now, "details": json.dumps({"message": str(error)}),
                 "id": job["id"]})
            connection.execute(text("""UPDATE cloud_accounts
                SET next_collection_at=:now + make_interval(secs => collection_interval_seconds)
                WHERE id=:id"""), {"now": now, "id": job["cloud_account_id"]})


def retry_delay_seconds(attempt_count: int) -> int:
    return min(300, 30 * (2 ** max(0, attempt_count - 1)))


def run_once() -> bool:
    now = datetime.now(timezone.utc)
    enqueue_due_jobs(now)
    job = claim_job(now)
    if not job:
        return False
    try:
        complete_job(job, result=collect_via_api(job["cloud_account_id"]))
    except Exception as error:
        complete_job(job, error=error)
    return True


def main() -> None:
    initialize_database()
    while True:
        processed = run_once()
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
