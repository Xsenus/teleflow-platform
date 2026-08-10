from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.enums import CampaignStatus, ConnectionKind, ConnectionStatus, PermissionStatus
from app.services import safety
from app.services.safety import (
    SafetyDecision,
    evaluate_delivery,
    hard_min_interval,
    validate_campaign,
)


def safety_settings() -> SimpleNamespace:
    """Создать минимальные hard limits для Safety Engine."""
    return SimpleNamespace(
        user_hard_min_interval_seconds=15,
        bot_hard_min_interval_seconds=1,
        global_hard_daily_cap=10,
    )


def test_safety_decision_and_campaign_validation_cover_all_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить serialization и полный набор campaign validation blockers."""
    deferred = datetime(2026, 8, 10, tzinfo=UTC)
    assert SafetyDecision(False, defer_until=deferred).to_dict()["defer_until"] == (
        deferred.isoformat()
    )
    user_connection = SimpleNamespace(
        kind=ConnectionKind.USER,
        status=ConnectionStatus.DRAFT,
        min_interval_seconds=1,
        daily_cap=20,
        destination_cooldown_minutes=0,
    )
    assert hard_min_interval(user_connection, safety_settings()) == 15  # type: ignore[arg-type]
    destination = SimpleNamespace(
        connection_id="other-connection",
        title="Target",
        enabled=False,
        permission_status=PermissionStatus.UNVERIFIED,
        validated=False,
        permission_note=None,
        rules_url=None,
        permission_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    link = SimpleNamespace(enabled=True, destination=destination)
    campaign = SimpleNamespace(
        connection=user_connection,
        connection_id="connection-id",
        template=SimpleNamespace(is_active=False),
        secondary_template_id="missing-template",
        secondary_template=None,
        secondary_template_weight=0,
        destinations=[link],
        spacing_seconds=1,
        manual_approval_required=False,
    )
    blockers, warnings = validate_campaign(campaign, safety_settings())  # type: ignore[arg-type]
    assert warnings == []
    assert len(blockers) >= 12
    assert any("другому подключению" in item for item in blockers)
    assert any("истёк срок" in item for item in blockers)
    assert any("ручного утверждения" in item for item in blockers)

    campaign.secondary_template = SimpleNamespace(is_active=False)
    campaign.secondary_template_weight = 100
    campaign.destinations = [SimpleNamespace(enabled=False, destination=destination)]
    blockers, _ = validate_campaign(campaign, safety_settings())  # type: ignore[arg-type]
    assert any("варианта B отключён" in item for item in blockers)
    assert any("Все назначения" in item for item in blockers)

    destination.enabled = True
    destination.permission_status = PermissionStatus.CONFIRMED
    destination.validated = True
    destination.permission_note = "Owner approval"
    destination.permission_expires_at = None
    campaign.destinations = [SimpleNamespace(enabled=True, destination=destination)]
    monkeypatch.setattr(safety, "validation_is_fresh", lambda *_args, **_kwargs: False)
    blockers, _ = validate_campaign(campaign, safety_settings())  # type: ignore[arg-type]
    assert any("устарела проверка" in item for item in blockers)

    user_connection.daily_cap = 1
    campaign.destinations = [
        SimpleNamespace(enabled=True, destination=destination),
        SimpleNamespace(enabled=True, destination=destination),
    ]
    blockers, _ = validate_campaign(campaign, safety_settings())  # type: ignore[arg-type]
    assert any("превышает дневной лимит" in item for item in blockers)

    campaign.destinations = []
    blockers, _ = validate_campaign(campaign, safety_settings())  # type: ignore[arg-type]
    assert any("Не выбрано" in item for item in blockers)


class SafetySession:
    """Предоставить organization и дневные counters для evaluate_delivery."""

    def __init__(self, organization: object | None, counts: list[int] | None = None) -> None:
        """Сохранить organization и последовательность scalar counters."""
        self.organization = organization
        self.counts = list(counts or [])

    def get(self, _model: object, _key: object) -> object | None:
        """Вернуть organization задания."""
        return self.organization

    def scalar(self, _statement: object) -> int:
        """Вернуть следующий дневной counter."""
        return self.counts.pop(0) if self.counts else 0


def delivery_objects(now: datetime) -> tuple[Any, Any, Any, Any, Any]:
    """Создать полностью разрешённое исходное состояние delivery."""
    organization = SimpleNamespace(
        publishing_paused=False,
        publishing_pause_reason=None,
        maintenance_mode=False,
        maintenance_reason=None,
    )
    job = SimpleNamespace(
        id="job-id",
        organization_id="organization-id",
        content_fingerprint="fingerprint",
    )
    connection = SimpleNamespace(
        id="connection-id",
        kind=ConnectionKind.BOT,
        status=ConnectionStatus.ACTIVE,
        flood_blocked_until=None,
        last_delivery_at=None,
        min_interval_seconds=1,
        daily_cap=5,
    )
    destination = SimpleNamespace(
        id="destination-id",
        enabled=True,
        permission_status=PermissionStatus.CONFIRMED,
        permission_note="Owner approval",
        rules_url=None,
        permission_expires_at=None,
        next_allowed_at=None,
    )
    campaign = SimpleNamespace(
        status=CampaignStatus.RUNNING,
        duplicate_guard_minutes=60,
        timezone_name="UTC",
    )
    return organization, job, connection, destination, campaign


@pytest.mark.parametrize(
    "case",
    [
        "organization_missing",
        "paused",
        "maintenance",
        "execution_lease",
        "execution_other",
        "slo",
        "capacity",
        "pilot",
        "approval",
        "connection",
        "flood",
        "campaign",
        "destination",
        "permission",
        "evidence",
        "permission_expired",
        "validation",
        "blackout",
        "duplicate",
        "window",
        "cooldown",
        "connection_interval",
        "daily_cap",
        "global_cap",
        "allowed",
    ],
)
def test_evaluate_delivery_returns_expected_decision_for_every_gate(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Проверить fail-closed порядок и код каждого Safety Engine dispatch gate."""
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    organization, job, connection, destination, campaign = delivery_objects(now)
    monkeypatch.setattr(
        safety,
        "execution_gate_decision",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, message=None, code=None),
    )
    monkeypatch.setattr(
        safety,
        "slo_gate_decision",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True, message=None),
    )
    monkeypatch.setattr(
        safety,
        "capacity_dispatch_decision",
        lambda *_args, **_kwargs: SimpleNamespace(
            allowed=True, message=None, code=None, defer_until=None
        ),
    )
    monkeypatch.setattr(safety, "campaign_stage_blocker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(safety, "approval_is_current", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(safety, "validation_is_fresh", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        safety,
        "evaluate_blackouts",
        lambda *_args, **_kwargs: SimpleNamespace(active=False, reason=None, defer_until=None),
    )
    monkeypatch.setattr(safety, "find_recent_duplicate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        safety,
        "evaluate_destination_window",
        lambda *_args, **_kwargs: SimpleNamespace(
            allowed=True, timezone_name="UTC", defer_until=None
        ),
    )
    counts = [0, 0]
    expected = "ALLOWED"

    if case == "organization_missing":
        organization = None
        expected = "ORGANIZATION_NOT_FOUND"
    elif case == "paused":
        organization.publishing_paused = True
        expected = "ORG_EMERGENCY_STOP"
    elif case == "maintenance":
        organization.maintenance_mode = True
        expected = "ORG_MAINTENANCE_MODE"
    elif case in {"execution_lease", "execution_other"}:
        code = "EXECUTION_LEASE_HELD" if case == "execution_lease" else "EXECUTION_BLOCKED"
        monkeypatch.setattr(
            safety,
            "execution_gate_decision",
            lambda *_args, **_kwargs: SimpleNamespace(allowed=False, message="blocked", code=code),
        )
        expected = code
    elif case == "slo":
        monkeypatch.setattr(
            safety,
            "slo_gate_decision",
            lambda *_args, **_kwargs: SimpleNamespace(allowed=False, message="slo blocked"),
        )
        expected = "SLO_GATE_BLOCKED"
    elif case == "capacity":
        monkeypatch.setattr(
            safety,
            "capacity_dispatch_decision",
            lambda *_args, **_kwargs: SimpleNamespace(
                allowed=False,
                message="capacity",
                code="CAPACITY_BLOCKED",
                defer_until=now + timedelta(minutes=1),
            ),
        )
        expected = "CAPACITY_BLOCKED"
    elif case == "pilot":
        monkeypatch.setattr(safety, "campaign_stage_blocker", lambda *_args: "pilot blocked")
        expected = "PILOT_STAGE_LIMIT"
    elif case == "approval":
        monkeypatch.setattr(safety, "approval_is_current", lambda *_args: False)
        expected = "CAMPAIGN_APPROVAL_STALE"
    elif case == "connection":
        connection.status = ConnectionStatus.PAUSED
        expected = "CONNECTION_NOT_ACTIVE"
    elif case == "flood":
        connection.flood_blocked_until = now + timedelta(minutes=1)
        expected = "CONNECTION_FLOOD_BLOCKED"
    elif case == "campaign":
        campaign.status = CampaignStatus.DRAFT
        expected = "CAMPAIGN_NOT_ACTIVE"
    elif case == "destination":
        destination.enabled = False
        expected = "DESTINATION_DISABLED"
    elif case == "permission":
        destination.permission_status = PermissionStatus.UNVERIFIED
        expected = "PERMISSION_NOT_CONFIRMED"
    elif case == "evidence":
        destination.permission_note = None
        expected = "PERMISSION_EVIDENCE_MISSING"
    elif case == "permission_expired":
        destination.permission_expires_at = now - timedelta(seconds=1)
        expected = "PERMISSION_EXPIRED"
    elif case == "validation":
        monkeypatch.setattr(safety, "validation_is_fresh", lambda *_args, **_kwargs: False)
        expected = "DESTINATION_VALIDATION_STALE"
    elif case == "blackout":
        monkeypatch.setattr(
            safety,
            "evaluate_blackouts",
            lambda *_args, **_kwargs: SimpleNamespace(
                active=True, reason="blackout", defer_until=now + timedelta(hours=1)
            ),
        )
        expected = "PUBLISHING_BLACKOUT"
    elif case == "duplicate":
        monkeypatch.setattr(safety, "find_recent_duplicate", lambda *_args, **_kwargs: object())
        expected = "DUPLICATE_CONTENT"
    elif case == "window":
        monkeypatch.setattr(
            safety,
            "evaluate_destination_window",
            lambda *_args, **_kwargs: SimpleNamespace(
                allowed=False,
                timezone_name="Asia/Novosibirsk",
                defer_until=now + timedelta(hours=1),
            ),
        )
        expected = "DESTINATION_TIME_WINDOW"
    elif case == "cooldown":
        destination.next_allowed_at = now + timedelta(minutes=1)
        expected = "DESTINATION_COOLDOWN"
    elif case == "connection_interval":
        connection.last_delivery_at = now
        expected = "CONNECTION_INTERVAL"
    elif case == "daily_cap":
        counts = [5]
        expected = "DAILY_CAP_REACHED"
    elif case == "global_cap":
        counts = [0, 10]
        expected = "GLOBAL_DAILY_CAP_REACHED"

    decision = evaluate_delivery(
        SafetySession(organization, counts),  # type: ignore[arg-type]
        job=job,  # type: ignore[arg-type]
        connection=connection,  # type: ignore[arg-type]
        destination=destination,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        settings=safety_settings(),  # type: ignore[arg-type]
        worker_id="worker-id",
        fence_epoch=1,
        now=now,
    )
    assert decision.code == expected
    assert decision.allowed is (case == "allowed")
    if case == "execution_lease":
        assert decision.requires_review is False
    if case == "execution_other":
        assert decision.requires_review is True
