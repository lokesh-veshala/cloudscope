from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum


class AlarmDecision(str, Enum):
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    READY = "READY"
    DUPLICATE = "DUPLICATE"
    BLOCKED_INCOMPLETE_PRICING = "BLOCKED_INCOMPLETE_PRICING"
    BLOCKED_STALE_DATA = "BLOCKED_STALE_DATA"
    BLOCKED_INVALID_INPUT = "BLOCKED_INVALID_INPUT"


@dataclass(frozen=True)
class AlarmPolicy:
    dashboard_id: str
    limit_version: int
    limit_amount: Decimal
    threshold_percent: Decimal
    period_start: datetime
    period_end: datetime
    max_data_age: timedelta = timedelta(minutes=10)
    required_confirmations: int = 2

    def __post_init__(self) -> None:
        if self.limit_amount <= 0:
            raise ValueError("limit_amount must be greater than zero")
        if not Decimal("0") < self.threshold_percent:
            raise ValueError("threshold_percent must be positive")
        if self.period_end <= self.period_start:
            raise ValueError("billing period must be non-empty")
        if self.required_confirmations < 1:
            raise ValueError("required_confirmations must be at least one")

    @property
    def key(self) -> str:
        return "|".join((
            self.dashboard_id,
            self.period_start.isoformat(),
            str(self.limit_version),
            str(self.threshold_percent.normalize()),
        ))


class AlarmState:
    """Port boundary; production implementations persist this transactionally."""

    def was_triggered(self, key: str) -> bool:
        raise NotImplementedError

    def confirmation_count(self, key: str) -> int:
        raise NotImplementedError

    def set_confirmation_count(self, key: str, value: int) -> None:
        raise NotImplementedError

    def last_observation(self, key: str) -> datetime | None:
        raise NotImplementedError

    def set_last_observation(self, key: str, value: datetime | None) -> None:
        raise NotImplementedError

    def create_pending_once(self, key: str) -> bool:
        raise NotImplementedError


class MemoryAlarmState(AlarmState):
    def __init__(self) -> None:
        self.confirmations: dict[str, int] = {}
        self.observations: dict[str, datetime] = {}
        self.triggered: set[str] = set()

    def was_triggered(self, key: str) -> bool:
        return key in self.triggered

    def confirmation_count(self, key: str) -> int:
        return self.confirmations.get(key, 0)

    def set_confirmation_count(self, key: str, value: int) -> None:
        self.confirmations[key] = value

    def last_observation(self, key: str) -> datetime | None:
        return self.observations.get(key)

    def set_last_observation(self, key: str, value: datetime | None) -> None:
        if value is None:
            self.observations.pop(key, None)
        else:
            self.observations[key] = value

    def create_pending_once(self, key: str) -> bool:
        if key in self.triggered:
            return False
        self.triggered.add(key)
        return True


class AlarmEvaluator:
    """Fail-closed evaluator that prevents stale, partial, or duplicate alarms."""

    def __init__(self, state: AlarmState) -> None:
        self.state = state

    def evaluate(
        self,
        policy: AlarmPolicy,
        *,
        estimated_cost: Decimal,
        pricing_coverage: Decimal,
        data_as_of: datetime,
        evaluated_at: datetime,
    ) -> AlarmDecision:
        if estimated_cost < 0 or not Decimal("0") <= pricing_coverage <= Decimal("1"):
            return AlarmDecision.BLOCKED_INVALID_INPUT
        if pricing_coverage != Decimal("1"):
            self.state.set_confirmation_count(policy.key, 0)
            self.state.set_last_observation(policy.key, None)
            return AlarmDecision.BLOCKED_INCOMPLETE_PRICING
        if data_as_of > evaluated_at or evaluated_at - data_as_of > policy.max_data_age:
            self.state.set_confirmation_count(policy.key, 0)
            self.state.set_last_observation(policy.key, None)
            return AlarmDecision.BLOCKED_STALE_DATA
        if not policy.period_start <= evaluated_at < policy.period_end:
            return AlarmDecision.BLOCKED_INVALID_INPUT
        if self.state.was_triggered(policy.key):
            return AlarmDecision.DUPLICATE

        usage_percent = estimated_cost / policy.limit_amount * Decimal("100")
        if usage_percent < policy.threshold_percent:
            self.state.set_confirmation_count(policy.key, 0)
            self.state.set_last_observation(policy.key, None)
            return AlarmDecision.BELOW_THRESHOLD

        last_observation = self.state.last_observation(policy.key)
        if last_observation is not None and data_as_of <= last_observation:
            return AlarmDecision.AWAITING_CONFIRMATION
        self.state.set_last_observation(policy.key, data_as_of)
        confirmations = self.state.confirmation_count(policy.key) + 1
        self.state.set_confirmation_count(policy.key, confirmations)
        if confirmations < policy.required_confirmations:
            return AlarmDecision.AWAITING_CONFIRMATION
        return (
            AlarmDecision.READY
            if self.state.create_pending_once(policy.key)
            else AlarmDecision.DUPLICATE
        )
