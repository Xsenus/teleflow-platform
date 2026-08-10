"""v2.1 release transparency and dependency assurance

Revision ID: 7f1b3d5e9a2c
Revises: 6e0a2c4f8b1d
Create Date: 2026-08-07 06:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f1b3d5e9a2c"
down_revision: str | Sequence[str] | None = "6e0a2c4f8b1d"
branch_labels = None
depends_on = None

transparency_event_enum = sa.Enum(
    "PUBLISHED",
    "WITHDRAWN",
    name="releasetransparencyeventtype",
    native_enum=False,
    length=20,
)
dependency_report_kind_enum = sa.Enum(
    "INVENTORY_ONLY",
    "VULNERABILITY_SCAN",
    name="dependencyreportkind",
    native_enum=False,
    length=30,
)
signature_status_enum = sa.Enum(
    "UNSIGNED",
    "VALID_UNTRUSTED",
    "VALID_TRUSTED",
    "INVALID",
    "REVOKED",
    name="artifactsignaturestatus",
    native_enum=False,
    length=30,
)
readiness_status_enum = sa.Enum(
    "PASSED",
    "WARNING",
    "BLOCKED",
    name="readinessstatus",
    native_enum=False,
    length=20,
)


def upgrade() -> None:
    """Применить this Alembic revision in dependency-safe schema order."""
    op.create_table(
        "release_transparency_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "release_attestation_id",
            sa.String(36),
            sa.ForeignKey("release_attestations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", transparency_event_enum, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("signature_status", signature_status_enum, nullable=False),
        sa.Column("signature_info", sa.JSON(), nullable=False),
        sa.Column("signer_fingerprint", sa.String(64)),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "sequence",
            name="uq_release_transparency_org_sequence",
        ),
    )
    op.create_index(
        "ix_release_transparency_org_created",
        "release_transparency_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_release_transparency_attestation_event",
        "release_transparency_events",
        ["release_attestation_id", "event_type"],
    )
    for column in (
        "organization_id",
        "release_attestation_id",
        "event_type",
        "entry_hash",
        "signature_status",
        "signer_fingerprint",
    ):
        op.create_index(
            f"ix_release_transparency_events_{column}",
            "release_transparency_events",
            [column],
        )

    op.create_table(
        "dependency_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("require_exact_pins", sa.Boolean(), nullable=False),
        sa.Column("allow_prerelease", sa.Boolean(), nullable=False),
        sa.Column("require_vulnerability_scan", sa.Boolean(), nullable=False),
        sa.Column("require_trusted_report", sa.Boolean(), nullable=False),
        sa.Column("max_critical", sa.Integer(), nullable=False),
        sa.Column("max_high", sa.Integer(), nullable=False),
        sa.Column("max_medium", sa.Integer(), nullable=False),
        sa.Column("report_ttl_hours", sa.Integer(), nullable=False),
        sa.Column("denied_packages", sa.JSON(), nullable=False),
        sa.Column("updated_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_dependency_policy_organization"),
    )
    op.create_index(
        "ix_dependency_policies_organization_id",
        "dependency_policies",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "release_dependency_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "release_attestation_id",
            sa.String(36),
            sa.ForeignKey("release_attestations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_kind", dependency_report_kind_enum, nullable=False),
        sa.Column("scanner_name", sa.String(120), nullable=False),
        sa.Column("scanner_version", sa.String(80)),
        sa.Column("report_payload", sa.JSON(), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("signature_status", signature_status_enum, nullable=False),
        sa.Column("signature_info", sa.JSON(), nullable=False),
        sa.Column("signer_fingerprint", sa.String(64)),
        sa.Column("sbom_payload", sa.JSON(), nullable=False),
        sa.Column("sbom_sha256", sa.String(64), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("attestation_payload_sha256", sa.String(64), nullable=False),
        sa.Column("critical_count", sa.Integer(), nullable=False),
        sa.Column("high_count", sa.Integer(), nullable=False),
        sa.Column("medium_count", sa.Integer(), nullable=False),
        sa.Column("low_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("unpinned_count", sa.Integer(), nullable=False),
        sa.Column("prerelease_count", sa.Integer(), nullable=False),
        sa.Column("denied_count", sa.Integer(), nullable=False),
        sa.Column("status", readiness_status_enum, nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "release_attestation_id",
            "report_sha256",
            "policy_sha256",
            name="uq_dependency_assessment_org_release_report_policy",
        ),
    )
    op.create_index(
        "ix_dependency_assessment_org_release_created",
        "release_dependency_assessments",
        ["organization_id", "release_attestation_id", "created_at"],
    )
    op.create_index(
        "ix_dependency_assessment_org_status_expiry",
        "release_dependency_assessments",
        ["organization_id", "status", "expires_at"],
    )
    for column in (
        "organization_id",
        "release_attestation_id",
        "report_kind",
        "report_sha256",
        "signature_status",
        "signer_fingerprint",
        "sbom_sha256",
        "policy_sha256",
        "status",
        "expires_at",
    ):
        op.create_index(
            f"ix_release_dependency_assessments_{column}",
            "release_dependency_assessments",
            [column],
        )

    with op.batch_alter_table("change_requests") as batch:
        batch.add_column(
            sa.Column("release_dependency_assessment_id", sa.String(36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_change_requests_release_dependency_assessment",
            "release_dependency_assessments",
            ["release_dependency_assessment_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_change_requests_release_dependency_assessment_id",
            ["release_dependency_assessment_id"],
        )


def downgrade() -> None:
    """Откатить this Alembic revision in dependency-safe schema order."""
    with op.batch_alter_table("change_requests") as batch:
        batch.drop_index("ix_change_requests_release_dependency_assessment_id")
        batch.drop_constraint(
            "fk_change_requests_release_dependency_assessment", type_="foreignkey"
        )
        batch.drop_column("release_dependency_assessment_id")
    op.drop_table("release_dependency_assessments")
    op.drop_table("dependency_policies")
    op.drop_table("release_transparency_events")
