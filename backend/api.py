from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI

app = FastAPI(title="CloudScope API", version="1.0.0")


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/quality")
def quality() -> dict[str, object]:
    return {
        "currency": "USD",
        "calculation_label": "Estimated Cost",
        "pricing_coverage": Decimal("1"),
        "alerting_eligible": True,
        "guardrails": [
            "complete_pricing",
            "fresh_inputs",
            "two_consecutive_confirmations",
            "idempotent_threshold_event",
        ],
    }
