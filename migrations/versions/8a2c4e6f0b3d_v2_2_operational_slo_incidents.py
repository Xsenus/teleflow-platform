"""v2.2 operational SLO and incident assurance

Revision ID: 8a2c4e6f0b3d
Revises: 7f1b3d5e9a2c
Create Date: 2026-08-07 08:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a2c4e6f0b3d"
down_revision: str | Sequence[str] | None = "7f1b3d5e9a2c"
branch_labels = None
depends_on = None

readiness_status_enum = sa.Enum(
    "PASSED", "WARNING", "BLOCKED", name="readinessstatus", native_enum=False, length=20
)
slo_source_enum = sa.Enum(
    "MANUAL", "WORKER", name="sloassessmentsource", native_enum=False, length=20
)
incident_status_enum = sa.Enum(
    "OPEN",
    "ACKNOWLEDGED",
    "MITIGATING",
    "RESOLVED",
    "CLOSED",
    name="incidentstatus",
    native_enum=False,
    length=24,
)
incident_source_enum = sa.Enum(
    "MANUAL",
    "SLO",
    "DELIVERY",
    "WORKER",
    "CHANGE",
    "SECURITY",
    name="incidentsource",
    native_enum=False,
    length=20,
)
incident_event_enum = sa.Enum(
    "CREATED",
    "ACKNOWLEDGED",
    "MITIGATION_STARTED",
    "COMMENTED",
    "RESOLVED",
    "CLOSED",
    "REOPENED",
    "OWNER_ASSIGNED",
    "SEVERITY_CHANGED",
    name="incidenteventtype",
    native_enum=False,
    length=32,
)
safety_severity_enum = sa.Enum(
    "INFO", "WARNING", "CRITICAL", name="safetyseverity", native_enum=False, length=20
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "slo_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("evaluation_window_hours", sa.Integer(), nullable=False),
        sa.Column("delivery_success_target_bps", sa.Integer(), nullable=False),
        sa.Column("minimum_delivery_sample_size", sa.Integer(), nullable=False),
        sa.Column("max_queue_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_worker_heartbeat_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_unresolved_delivery_reviews", sa.Integer(), nullable=False),
        sa.Column("max_open_critical_incidents", sa.Integer(), nullable=False),
        sa.Column("error_budget_warning_percent", sa.Integer(), nullable=False),
        sa.Column("error_budget_critical_percent", sa.Integer(), nullable=False),
        sa.Column("assessment_ttl_minutes", sa.Integer(), nullable=False),
        sa.Column("gate_publishing", sa.Boolean(), nullable=False),
        sa.Column("gate_changes", sa.Boolean(), nullable=False),
        sa.Column("auto_create_incidents", sa.Boolean(), nullable=False),
        sa.Column("auto_resolve_incidents", sa.Boolean(), nullable=False),
        sa.Column("suppress_incidents_during_maintenance", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("updated_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_slo_policy_organization"),
        sa.CheckConstraint(
            "evaluation_window_hours >= 1 AND evaluation_window_hours <= 720",
            name="ck_slo_policy_window_hours",
        ),
        sa.CheckConstraint(
            "delivery_success_target_bps >= 5000 AND delivery_success_target_bps <= 10000",
            name="ck_slo_policy_delivery_target",
        ),
        sa.CheckConstraint(
            "minimum_delivery_sample_size >= 1",
            name="ck_slo_policy_minimum_sample",
        ),
        sa.CheckConstraint(
            "max_queue_age_seconds >= 1 AND max_worker_heartbeat_age_seconds >= 5",
            name="ck_slo_policy_runtime_limits",
        ),
        sa.CheckConstraint(
            "max_unresolved_delivery_reviews >= 0 AND max_open_critical_incidents >= 0",
            name="ck_slo_policy_non_negative_limits",
        ),
        sa.CheckConstraint(
            "error_budget_warning_percent >= 1 AND error_budget_critical_percent > error_budget_warning_percent",
            name="ck_slo_policy_error_budget_thresholds",
        ),
        sa.CheckConstraint(
            "assessment_ttl_minutes >= 1 AND assessment_ttl_minutes <= 1440",
            name="ck_slo_policy_assessment_ttl",
        ),
    )
    op.create_index(
        "ix_slo_policies_organization_id",
        "slo_policies",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "slo_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", slo_source_enum, nullable=False),
        sa.Column("status", readiness_status_enum, nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("eligible_deliveries", sa.Integer(), nullable=False),
        sa.Column("successful_deliveries", sa.Integer(), nullable=False),
        sa.Column("failed_deliveries", sa.Integer(), nullable=False),
        sa.Column("uncertain_deliveries", sa.Integer(), nullable=False),
        sa.Column("delivery_success_rate_bps", sa.Integer()),
        sa.Column("error_budget_consumed_bps", sa.Integer()),
        sa.Column("oldest_queue_age_seconds", sa.Integer(), nullable=False),
        sa.Column("worker_heartbeat_age_seconds", sa.Integer()),
        sa.Column("open_critical_incidents", sa.Integer(), nullable=False),
        sa.Column("unresolved_delivery_reviews", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_slo_assessment_org_created",
        "slo_assessments",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_slo_assessment_org_status_expiry",
        "slo_assessments",
        ["organization_id", "status", "expires_at"],
    )
    for column in (
        "organization_id",
        "source",
        "status",
        "policy_sha256",
        "fingerprint",
        "expires_at",
    ):
        op.create_index(f"ix_slo_assessments_{column}", "slo_assessments", [column])

    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", incident_status_enum, nullable=False),
        sa.Column("severity", safety_severity_enum, nullable=False),
        sa.Column("source", incident_source_enum, nullable=False),
        sa.Column("title", sa.String(220), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text()),
        sa.Column("dedup_key", sa.String(240)),
        sa.Column(
            "linked_slo_assessment_id",
            sa.String(36),
            sa.ForeignKey("slo_assessments.id", ondelete="SET NULL"),
        ),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("mitigating_at", sa.DateTime(timezone=True)),
        sa.Column("mitigating_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("resolution_summary", sa.Text()),
        sa.Column("root_cause", sa.Text()),
        sa.Column("postmortem_url", sa.String(1000)),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_incident_org_status_severity",
        "incidents",
        ["organization_id", "status", "severity"],
    )
    op.create_index("ix_incident_org_dedup", "incidents", ["organization_id", "dedup_key"])
    op.create_index("ix_incident_org_detected", "incidents", ["organization_id", "detected_at"])
    for column in (
        "organization_id",
        "status",
        "severity",
        "source",
        "linked_slo_assessment_id",
        "owner_user_id",
    ):
        op.create_index(f"ix_incidents_{column}", "incidents", [column])

    op.create_table(
        "incident_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", incident_event_enum, nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_incident_event_incident_created",
        "incident_events",
        ["incident_id", "created_at"],
    )
    op.create_index(
        "ix_incident_event_org_created",
        "incident_events",
        ["organization_id", "created_at"],
    )
    for column in ("organization_id", "incident_id", "event_type"):
        op.create_index(f"ix_incident_events_{column}", "incident_events", [column])


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    op.drop_table("incident_events")
    op.drop_table("incidents")
    op.drop_table("slo_assessments")
    op.drop_table("slo_policies")
