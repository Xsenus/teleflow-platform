from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.enums import (
    AIInteractionStatus,
    AIProviderKind,
    ApiKeyStatus,
    ApprovalDecision,
    ArtifactSignatureStatus,
    ArtifactSigningKeyStatus,
    BlackoutKind,
    BlackoutScope,
    BusinessConnectionStatus,
    CampaignApprovalStatus,
    CampaignStatus,
    CandidateStatus,
    CapacityAssessmentSource,
    ChallengeStatus,
    ChangeRequestStatus,
    ChangeRequestType,
    ConfigurationBundleKind,
    ConfigurationBundleStatus,
    ConnectionKind,
    ConnectionStatus,
    ConsentStatus,
    ContinuityDrillEventType,
    ContinuityDrillMode,
    ContinuityDrillStatus,
    ConversationStatus,
    DeliveryAttemptStatus,
    DeliveryReviewResolution,
    DependencyReportKind,
    DeploymentVerificationPhase,
    DestinationKind,
    DestinationValidationStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
    InboundUpdateStatus,
    IncidentEventType,
    IncidentSource,
    IncidentStatus,
    IntegrationKind,
    JobStatus,
    MessageAuthor,
    MessageDirection,
    NotificationStatus,
    OrganizationStatus,
    OutboxStatus,
    ParseMode,
    PermissionStatus,
    PilotCanaryStatus,
    PilotProgramStatus,
    PilotStage,
    PilotStageStatus,
    PreflightStatus,
    PrivacyRequestStatus,
    PrivacyRequestType,
    ReadinessStatus,
    RecoveryBackupStatus,
    RecoveryDrillMode,
    RecoveryDrillStatus,
    ReleaseTransparencyEventType,
    RolloutMode,
    RunStatus,
    SafetySeverity,
    ScheduleType,
    SLOAssessmentSource,
    SupportBundleStatus,
    UserRole,
)


def utcnow() -> datetime:
    """Выполнить операцию utcnow. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return datetime.now(UTC)


def new_id() -> str:
    """Выполнить операцию new id. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    status: Mapped[OrganizationStatus] = mapped_column(
        Enum(OrganizationStatus, native_enum=False, length=20),
        default=OrganizationStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    timezone_name: Mapped[str] = mapped_column(
        String(80), default="Europe/Helsinki", nullable=False
    )
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Organization-wide safety controls. The emergency stop is checked both by
    # the scheduler and immediately before every Telegram network request.
    publishing_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    publishing_pause_reason: Mapped[str | None] = mapped_column(Text)
    publishing_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publishing_paused_by_id: Mapped[str | None] = mapped_column(String(36))

    # Maintenance mode is separate from emergency stop. It blocks scheduled and
    # manual publishing while a controlled platform/configuration change is in progress.
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maintenance_reason: Mapped[str | None] = mapped_column(Text)
    maintenance_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_started_by_id: Mapped[str | None] = mapped_column(String(36))

    # Four-eyes controls are opt-in for compatibility with single-user local
    # installations. Production installations should enable distinct approval.
    require_distinct_campaign_approver: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    high_risk_destination_threshold: Mapped[int] = mapped_column(
        Integer, default=20, nullable=False
    )
    high_risk_required_approvals: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    approval_request_ttl_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    # Controlled live-pilot stage. New installations start in local/fake mode;
    # migrations preserve the previous effective capacity for existing tenants.
    pilot_stage: Mapped[PilotStage] = mapped_column(
        Enum(PilotStage, native_enum=False, length=20),
        default=PilotStage.LOCAL,
        nullable=False,
        index=True,
    )
    pilot_stage_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pilot_stage_updated_by_id: Mapped[str | None] = mapped_column(String(36))
    pilot_stage_note: Mapped[str | None] = mapped_column(Text)

    users: Mapped[list[User]] = relationship(back_populates="organization")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_user_org_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=20), default=UserRole.VIEWER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    pending_totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(back_populates="users")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class TelegramConnection(Base, TimestampMixin):
    __tablename__ = "telegram_connections"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_connection_org_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[ConnectionKind] = mapped_column(
        Enum(ConnectionKind, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, native_enum=False, length=30),
        default=ConnectionStatus.DRAFT,
        nullable=False,
        index=True,
    )
    credentials_enc: Mapped[str | None] = mapped_column(Text)
    telegram_account_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    telegram_display_name: Mapped[str | None] = mapped_column(String(180))

    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    daily_cap: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    destination_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=1440, nullable=False)
    require_manual_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stop_on_flood: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flood_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)

    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    destinations: Mapped[list[Destination]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )
    campaigns: Mapped[list[Campaign]] = relationship(back_populates="connection")
    business_connections: Mapped[list[TelegramBusinessConnection]] = relationship(
        back_populates="telegram_connection", cascade="all, delete-orphan"
    )


class Destination(Base, TimestampMixin):
    __tablename__ = "destinations"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "telegram_chat_id", "topic_id", name="uq_destination_chat_topic"
        ),
        Index(
            "ix_destination_org_enabled_permission",
            "organization_id",
            "enabled",
            "permission_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[DestinationKind] = mapped_column(
        Enum(DestinationKind, native_enum=False, length=30), nullable=False
    )
    topic_id: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    validation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    permission_status: Mapped[PermissionStatus] = mapped_column(
        Enum(PermissionStatus, native_enum=False, length=20),
        default=PermissionStatus.UNVERIFIED,
        nullable=False,
    )
    permission_note: Mapped[str | None] = mapped_column(Text)
    rules_url: Mapped[str | None] = mapped_column(String(1000))
    permission_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permission_confirmed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    permission_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permission_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    timezone_name: Mapped[str | None] = mapped_column(String(80))
    allowed_weekdays: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    allowed_start_time: Mapped[time | None] = mapped_column(Time())
    allowed_end_time: Mapped[time | None] = mapped_column(Time())
    cooldown_minutes_override: Mapped[int | None] = mapped_column(Integer)

    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)

    connection: Mapped[TelegramConnection] = relationship(back_populates="destinations")
    campaign_links: Mapped[list[CampaignDestination]] = relationship(
        back_populates="destination", cascade="all, delete-orphan"
    )


class DestinationValidationRecord(Base):
    __tablename__ = "destination_validation_records"
    __table_args__ = (
        Index(
            "ix_destination_validation_org_destination_checked",
            "organization_id",
            "destination_id",
            "checked_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[DestinationValidationStatus] = mapped_column(
        Enum(DestinationValidationStatus, native_enum=False, length=30),
        nullable=False,
        index=True,
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    checked_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class PublishingBlackout(Base, TimestampMixin):
    __tablename__ = "publishing_blackouts"
    __table_args__ = (
        Index(
            "ix_blackout_org_enabled_scope",
            "organization_id",
            "enabled",
            "scope",
        ),
        Index("ix_blackout_org_starts_ends", "organization_id", "starts_at", "ends_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope: Mapped[BlackoutScope] = mapped_column(
        Enum(BlackoutScope, native_enum=False, length=24), nullable=False, index=True
    )
    kind: Mapped[BlackoutKind] = mapped_column(
        Enum(BlackoutKind, native_enum=False, length=24), nullable=False
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True
    )
    destination_id: Mapped[str | None] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # ONE_TIME windows are stored in UTC. WEEKLY windows use local weekdays
    # and wall-clock times in ``timezone_name`` and may cross midnight.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone_name: Mapped[str | None] = mapped_column(String(80))
    weekdays: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time())
    end_time: Mapped[time | None] = mapped_column(Time())

    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("organization_id", "stored_name", name="uq_media_org_stored_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(1000))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class MessageTemplate(Base, TimestampMixin):
    __tablename__ = "message_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[ParseMode] = mapped_column(
        Enum(ParseMode, native_enum=False, length=20), default=ParseMode.PLAIN, nullable=False
    )
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))
    link_preview: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)

    media_asset: Mapped[MediaAsset | None] = relationship()
    campaigns: Mapped[list[Campaign]] = relationship(
        back_populates="template", foreign_keys="Campaign.template_id"
    )


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaign_org_due", "organization_id", "status", "next_run_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id"), index=True, nullable=False
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("message_templates.id"), index=True, nullable=False
    )
    secondary_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("message_templates.id"), index=True
    )
    secondary_template_weight: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, native_enum=False, length=30),
        default=CampaignStatus.DRAFT,
        nullable=False,
        index=True,
    )
    schedule_type: Mapped[ScheduleType] = mapped_column(
        Enum(ScheduleType, native_enum=False, length=20),
        default=ScheduleType.ONCE,
        nullable=False,
    )
    schedule_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone_name: Mapped[str] = mapped_column(
        String(80), default="Europe/Helsinki", nullable=False
    )
    weekdays: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    spacing_seconds: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    rollout_mode: Mapped[RolloutMode] = mapped_column(
        Enum(RolloutMode, native_enum=False, length=20),
        default=RolloutMode.STANDARD,
        nullable=False,
    )
    rollout_batch_size: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    rollout_pause_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    rollout_require_checkpoint: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rollout_failure_threshold_percent: Mapped[int] = mapped_column(
        Integer, default=20, nullable=False
    )
    duplicate_guard_minutes: Mapped[int] = mapped_column(Integer, default=1380, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    manual_approval_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    active_approval_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    connection: Mapped[TelegramConnection] = relationship(back_populates="campaigns")
    template: Mapped[MessageTemplate] = relationship(
        back_populates="campaigns", foreign_keys=[template_id]
    )
    secondary_template: Mapped[MessageTemplate | None] = relationship(
        foreign_keys=[secondary_template_id]
    )
    destinations: Mapped[list[CampaignDestination]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="CampaignDestination.position",
    )
    runs: Mapped[list[CampaignRun]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list[CampaignApprovalRequest]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    preflight_reports: Mapped[list[CampaignPreflightReport]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    readiness_reports: Mapped[list[PilotReadinessReport]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignApprovalRequest(Base):
    __tablename__ = "campaign_approval_requests"
    __table_args__ = (
        Index("ix_campaign_approval_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_campaign_approval_campaign_status", "campaign_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CampaignApprovalStatus] = mapped_column(
        Enum(CampaignApprovalStatus, native_enum=False, length=20),
        default=CampaignApprovalStatus.PENDING,
        nullable=False,
        index=True,
    )
    required_approvals: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    require_distinct_requester: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="approval_requests")
    decisions: Mapped[list[CampaignApprovalDecision]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="CampaignApprovalDecision.created_at",
    )


class CampaignApprovalDecision(Base):
    __tablename__ = "campaign_approval_decisions"
    __table_args__ = (
        UniqueConstraint("request_id", "user_id", name="uq_campaign_approval_user"),
        Index("ix_campaign_approval_decision_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    request_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_approval_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(ApprovalDecision, native_enum=False, length=16), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    request: Mapped[CampaignApprovalRequest] = relationship(back_populates="decisions")


class CampaignPreflightReport(Base):
    __tablename__ = "campaign_preflight_reports"
    __table_args__ = (
        Index("ix_preflight_org_campaign_created", "organization_id", "campaign_id", "created_at"),
        Index("ix_preflight_org_status_expires", "organization_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[PreflightStatus] = mapped_column(
        Enum(PreflightStatus, native_enum=False, length=20), nullable=False, index=True
    )
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="preflight_reports")


class PilotReadinessReport(Base):
    __tablename__ = "pilot_readiness_reports"
    __table_args__ = (
        Index(
            "ix_readiness_org_campaign_created",
            "organization_id",
            "campaign_id",
            "created_at",
        ),
        Index("ix_readiness_org_status_expires", "organization_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20), nullable=False, index=True
    )
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    preflight_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_preflight_reports.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="readiness_reports")


class PilotStageAssessment(Base):
    __tablename__ = "pilot_stage_assessments"
    __table_args__ = (
        Index(
            "ix_pilot_stage_assessment_org_created",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_pilot_stage_assessment_org_status_expires",
            "organization_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    current_stage: Mapped[PilotStage] = mapped_column(
        Enum(PilotStage, native_enum=False, length=20), nullable=False
    )
    requested_stage: Mapped[PilotStage] = mapped_column(
        Enum(PilotStage, native_enum=False, length=20), nullable=False, index=True
    )
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20), nullable=False, index=True
    )
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PilotCanaryAttempt(Base):
    __tablename__ = "pilot_canary_attempts"
    __table_args__ = (
        Index(
            "ix_pilot_canary_org_destination_created",
            "organization_id",
            "destination_id",
            "created_at",
        ),
        Index("ix_pilot_canary_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[PilotCanaryStatus] = mapped_column(
        Enum(PilotCanaryStatus, native_enum=False, length=20),
        default=PilotCanaryStatus.PENDING,
        nullable=False,
        index=True,
    )
    marker: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_message_id: Mapped[str | None] = mapped_column(String(100))
    is_fake: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SupportBundle(Base):
    __tablename__ = "support_bundles"
    __table_args__ = (
        Index("ix_support_bundle_org_created", "organization_id", "created_at"),
        Index("ix_support_bundle_org_status_expires", "organization_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[SupportBundleStatus] = mapped_column(
        Enum(SupportBundleStatus, native_enum=False, length=20),
        default=SupportBundleStatus.READY,
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str | None] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sections: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CommissioningCheckRun(Base):
    __tablename__ = "commissioning_check_runs"
    __table_args__ = (
        Index("ix_commissioning_org_created", "organization_id", "created_at"),
        Index("ix_commissioning_org_status_expires", "organization_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PilotProgram(Base, TimestampMixin):
    __tablename__ = "pilot_programs"
    __table_args__ = (
        Index("ix_pilot_program_org_status", "organization_id", "status"),
        Index("ix_pilot_program_org_campaign", "organization_id", "campaign_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[PilotProgramStatus] = mapped_column(
        Enum(PilotProgramStatus, native_enum=False, length=20),
        default=PilotProgramStatus.DRAFT,
        nullable=False,
        index=True,
    )
    stage_sizes: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    current_stage_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    require_distinct_signoff: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign] = relationship()
    stages: Mapped[list[PilotStageExecution]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
        order_by="PilotStageExecution.stage_order",
    )


class PilotStageExecution(Base, TimestampMixin):
    __tablename__ = "pilot_stage_executions"
    __table_args__ = (
        UniqueConstraint("program_id", "stage_order", name="uq_pilot_program_stage_order"),
        Index("ix_pilot_stage_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    program_id: Mapped[str] = mapped_column(
        ForeignKey("pilot_programs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    target_destination_count: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_live_telegram: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[PilotStageStatus] = mapped_column(
        Enum(PilotStageStatus, native_enum=False, length=30),
        default=PilotStageStatus.PENDING,
        nullable=False,
        index=True,
    )
    commissioning_check_id: Mapped[str | None] = mapped_column(
        ForeignKey("commissioning_check_runs.id", ondelete="SET NULL"), index=True
    )
    readiness_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("pilot_readiness_reports.id", ondelete="SET NULL"), index=True
    )
    preflight_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_preflight_reports.id", ondelete="SET NULL"), index=True
    )
    campaign_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_runs.id", ondelete="SET NULL"), index=True
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    started_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_off_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signoff_note: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    program: Mapped[PilotProgram] = relationship(back_populates="stages")
    campaign_run: Mapped[CampaignRun | None] = relationship()


class ConfigurationBundle(Base):
    __tablename__ = "configuration_bundles"
    __table_args__ = (
        Index("ix_configuration_bundle_org_created", "organization_id", "created_at"),
        Index("ix_configuration_bundle_org_kind_status", "organization_id", "kind", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[ConfigurationBundleKind] = mapped_column(
        Enum(ConfigurationBundleKind, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ConfigurationBundleStatus] = mapped_column(
        Enum(ConfigurationBundleStatus, native_enum=False, length=20), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_product_version: Mapped[str | None] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    include_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conflict_mode: Mapped[str | None] = mapped_column(String(20))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    applied_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ArtifactSigningKey(Base, TimestampMixin):
    __tablename__ = "artifact_signing_keys"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "fingerprint", name="uq_artifact_signing_key_org_fingerprint"
        ),
        UniqueConstraint("organization_id", "key_id", name="uq_artifact_signing_key_org_key_id"),
        Index("ix_artifact_signing_key_org_status", "organization_id", "status"),
        Index("ix_artifact_signing_key_org_default", "organization_id", "is_default"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    key_id: Mapped[str] = mapped_column(String(80), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), default="Ed25519", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    public_key_b64: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ArtifactSigningKeyStatus] = mapped_column(
        Enum(ArtifactSigningKeyStatus, native_enum=False, length=20),
        default=ArtifactSigningKeyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    trusted_for_import: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    revoked_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    @property
    def has_private_key(self) -> bool:
        """Выполнить операцию has private key класса ArtifactSigningKey. Аргументы интерпретируются
        в контексте модуля, результат возвращается вызывающему коду.
        """
        return bool(self.private_key_enc)


class ReleaseAttestation(Base):
    __tablename__ = "release_attestations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "payload_sha256", name="uq_release_attestation_org_payload"
        ),
        Index(
            "ix_release_attestation_org_version_created", "organization_id", "version", "created_at"
        ),
        Index("ix_release_attestation_org_signature", "organization_id", "signature_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_commit: Mapped[str | None] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ReleaseTransparencyEvent(Base):
    __tablename__ = "release_transparency_events"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "sequence", name="uq_release_transparency_org_sequence"
        ),
        Index(
            "ix_release_transparency_org_created",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_release_transparency_attestation_event",
            "release_attestation_id",
            "event_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    release_attestation_id: Mapped[str] = mapped_column(
        ForeignKey("release_attestations.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[ReleaseTransparencyEventType] = mapped_column(
        Enum(ReleaseTransparencyEventType, native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DependencyPolicy(Base, TimestampMixin):
    __tablename__ = "dependency_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_dependency_policy_organization"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    require_exact_pins: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_prerelease: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_vulnerability_scan: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_trusted_report: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_critical: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_high: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_medium: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    report_ttl_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    denied_packages: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ReleaseDependencyAssessment(Base):
    __tablename__ = "release_dependency_assessments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "release_attestation_id",
            "report_sha256",
            "policy_sha256",
            name="uq_dependency_assessment_org_release_report_policy",
        ),
        Index(
            "ix_dependency_assessment_org_release_created",
            "organization_id",
            "release_attestation_id",
            "created_at",
        ),
        Index(
            "ix_dependency_assessment_org_status_expiry",
            "organization_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    release_attestation_id: Mapped[str] = mapped_column(
        ForeignKey("release_attestations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    report_kind: Mapped[DependencyReportKind] = mapped_column(
        Enum(DependencyReportKind, native_enum=False, length=30),
        nullable=False,
        index=True,
    )
    scanner_name: Mapped[str] = mapped_column(String(120), nullable=False)
    scanner_version: Mapped[str | None] = mapped_column(String(80))
    report_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    sbom_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sbom_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attestation_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    critical_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    high_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    medium_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unpinned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prerelease_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    denied_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20),
        nullable=False,
        index=True,
    )
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SLOPolicy(Base, TimestampMixin):
    __tablename__ = "slo_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_slo_policy_organization"),
        CheckConstraint(
            "evaluation_window_hours >= 1 AND evaluation_window_hours <= 720",
            name="ck_slo_policy_window_hours",
        ),
        CheckConstraint(
            "delivery_success_target_bps >= 5000 AND delivery_success_target_bps <= 10000",
            name="ck_slo_policy_delivery_target",
        ),
        CheckConstraint(
            "minimum_delivery_sample_size >= 1",
            name="ck_slo_policy_minimum_sample",
        ),
        CheckConstraint(
            "max_queue_age_seconds >= 1 AND max_worker_heartbeat_age_seconds >= 5",
            name="ck_slo_policy_runtime_limits",
        ),
        CheckConstraint(
            "max_unresolved_delivery_reviews >= 0 AND max_open_critical_incidents >= 0",
            name="ck_slo_policy_non_negative_limits",
        ),
        CheckConstraint(
            "error_budget_warning_percent >= 1 AND error_budget_critical_percent > error_budget_warning_percent",
            name="ck_slo_policy_error_budget_thresholds",
        ),
        CheckConstraint(
            "assessment_ttl_minutes >= 1 AND assessment_ttl_minutes <= 1440",
            name="ck_slo_policy_assessment_ttl",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    evaluation_window_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    delivery_success_target_bps: Mapped[int] = mapped_column(Integer, default=9900, nullable=False)
    minimum_delivery_sample_size: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_queue_age_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    max_worker_heartbeat_age_seconds: Mapped[int] = mapped_column(
        Integer, default=120, nullable=False
    )
    max_unresolved_delivery_reviews: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_open_critical_incidents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_budget_warning_percent: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    error_budget_critical_percent: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    assessment_ttl_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    gate_publishing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gate_changes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_create_incidents: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_resolve_incidents: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    suppress_incidents_during_maintenance: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class SLOAssessment(Base):
    __tablename__ = "slo_assessments"
    __table_args__ = (
        Index("ix_slo_assessment_org_created", "organization_id", "created_at"),
        Index("ix_slo_assessment_org_status_expiry", "organization_id", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source: Mapped[SLOAssessmentSource] = mapped_column(
        Enum(SLOAssessmentSource, native_enum=False, length=20),
        default=SLOAssessmentSource.MANUAL,
        nullable=False,
        index=True,
    )
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    eligible_deliveries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_deliveries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_deliveries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uncertain_deliveries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivery_success_rate_bps: Mapped[int | None] = mapped_column(Integer)
    error_budget_consumed_bps: Mapped[int | None] = mapped_column(Integer)
    oldest_queue_age_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    worker_heartbeat_age_seconds: Mapped[int | None] = mapped_column(Integer)
    open_critical_incidents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unresolved_delivery_reviews: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CapacityPolicy(Base, TimestampMixin):
    """Tenant limits that bound queue growth and network dispatch pressure."""

    __tablename__ = "capacity_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_capacity_policy_organization"),
        CheckConstraint(
            "max_active_jobs >= 1 AND max_ready_jobs >= 1 AND max_processing_jobs >= 1",
            name="ck_capacity_policy_job_limits_positive",
        ),
        CheckConstraint(
            "max_ready_jobs <= max_active_jobs AND max_processing_jobs <= max_ready_jobs",
            name="ck_capacity_policy_job_limit_order",
        ),
        CheckConstraint(
            "max_active_runs >= 1 AND max_jobs_per_run >= 1 AND max_jobs_per_run <= max_active_jobs",
            name="ck_capacity_policy_run_limits",
        ),
        CheckConstraint(
            "max_network_starts_per_minute >= 1 AND max_network_starts_per_hour >= max_network_starts_per_minute",
            name="ck_capacity_policy_network_limits",
        ),
        CheckConstraint(
            "max_estimated_drain_seconds >= 60",
            name="ck_capacity_policy_drain_limit",
        ),
        CheckConstraint(
            "warning_utilization_percent >= 1 AND warning_utilization_percent < admission_block_utilization_percent AND admission_block_utilization_percent <= 100",
            name="ck_capacity_policy_utilization_thresholds",
        ),
        CheckConstraint(
            "assessment_ttl_minutes >= 1 AND assessment_ttl_minutes <= 1440",
            name="ck_capacity_policy_assessment_ttl",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_active_jobs: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    max_ready_jobs: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_processing_jobs: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_active_runs: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    max_jobs_per_run: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_network_starts_per_minute: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    max_network_starts_per_hour: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    max_estimated_drain_seconds: Mapped[int] = mapped_column(Integer, default=21600, nullable=False)
    warning_utilization_percent: Mapped[int] = mapped_column(Integer, default=70, nullable=False)
    admission_block_utilization_percent: Mapped[int] = mapped_column(
        Integer, default=90, nullable=False
    )
    assessment_ttl_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    gate_admission: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gate_dispatch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class CapacityAssessment(Base):
    """Immutable queue-pressure snapshot used by admission and operator review."""

    __tablename__ = "capacity_assessments"
    __table_args__ = (
        Index("ix_capacity_assessment_org_created", "organization_id", "created_at"),
        Index(
            "ix_capacity_assessment_org_status_expiry",
            "organization_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source: Mapped[CapacityAssessmentSource] = mapped_column(
        Enum(CapacityAssessmentSource, native_enum=False, length=20),
        default=CapacityAssessmentSource.MANUAL,
        nullable=False,
        index=True,
    )
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20), nullable=False, index=True
    )
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    active_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ready_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    held_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processing_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    waiting_review_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    network_starts_last_minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    network_starts_last_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_drain_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queue_utilization_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    projected_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    projected_ready_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    projected_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Incident(Base, TimestampMixin):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incident_org_status_severity", "organization_id", "status", "severity"),
        Index("ix_incident_org_dedup", "organization_id", "dedup_key"),
        Index("ix_incident_org_detected", "organization_id", "detected_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, native_enum=False, length=24),
        default=IncidentStatus.OPEN,
        nullable=False,
        index=True,
    )
    severity: Mapped[SafetySeverity] = mapped_column(
        Enum(SafetySeverity, native_enum=False, length=20),
        default=SafetySeverity.WARNING,
        nullable=False,
        index=True,
    )
    source: Mapped[IncidentSource] = mapped_column(
        Enum(IncidentSource, native_enum=False, length=20),
        default=IncidentSource.MANUAL,
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str | None] = mapped_column(String(240))
    linked_slo_assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("slo_assessments.id", ondelete="SET NULL"), index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    mitigating_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitigating_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolution_summary: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    postmortem_url: Mapped[str | None] = mapped_column(String(1000))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        Index("ix_incident_event_incident_created", "incident_id", "created_at"),
        Index("ix_incident_event_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[IncidentEventType] = mapped_column(
        Enum(IncidentEventType, native_enum=False, length=32), nullable=False, index=True
    )
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ChangeRequest(Base, TimestampMixin):
    __tablename__ = "change_requests"
    __table_args__ = (
        Index("ix_change_request_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_change_request_org_type", "organization_id", "change_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    change_type: Mapped[ChangeRequestType] = mapped_column(
        Enum(ChangeRequestType, native_enum=False, length=32), nullable=False, index=True
    )
    status: Mapped[ChangeRequestStatus] = mapped_column(
        Enum(ChangeRequestStatus, native_enum=False, length=24),
        default=ChangeRequestStatus.DRAFT,
        nullable=False,
        index=True,
    )
    current_version: Mapped[str | None] = mapped_column(String(40))
    target_version: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_summary: Mapped[str] = mapped_column(Text, nullable=False)
    rollback_plan: Mapped[str] = mapped_column(Text, nullable=False)
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    release_attestation_id: Mapped[str | None] = mapped_column(
        ForeignKey("release_attestations.id", ondelete="SET NULL"), index=True
    )
    release_dependency_assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("release_dependency_assessments.id", ondelete="SET NULL"), index=True
    )


class DeploymentVerificationReport(Base):
    __tablename__ = "deployment_verification_reports"
    __table_args__ = (
        Index("ix_deploy_verify_org_phase_created", "organization_id", "phase", "created_at"),
        Index("ix_deploy_verify_change_created", "change_request_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    change_request_id: Mapped[str] = mapped_column(
        ForeignKey("change_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    phase: Mapped[DeploymentVerificationPhase] = mapped_column(
        Enum(DeploymentVerificationPhase, native_enum=False, length=20), nullable=False, index=True
    )
    status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus, native_enum=False, length=20), nullable=False, index=True
    )
    expected_version: Mapped[str | None] = mapped_column(String(40))
    observed_version: Mapped[str] = mapped_column(String(40), nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RecoveryPolicy(Base, TimestampMixin):
    __tablename__ = "recovery_policies"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_recovery_policy_organization"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rpo_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    rto_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    restore_drill_max_age_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    minimum_retained_backups: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    require_encrypted_backup: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_trusted_signature: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_restore_drill: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class RecoveryBackupEvidence(Base):
    __tablename__ = "recovery_backup_evidence"
    __table_args__ = (
        UniqueConstraint("organization_id", "backup_id", name="uq_recovery_backup_org_backup"),
        Index(
            "ix_recovery_backup_org_status_created",
            "organization_id",
            "status",
            "backup_created_at",
        ),
        Index("ix_recovery_backup_org_signature", "organization_id", "signature_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    backup_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[RecoveryBackupStatus] = mapped_column(
        Enum(RecoveryBackupStatus, native_enum=False, length=20),
        default=RecoveryBackupStatus.REGISTERED,
        nullable=False,
        index=True,
    )
    product_version: Mapped[str] = mapped_column(String(40), nullable=False)
    database_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(24), nullable=False)
    artifact_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    artifact_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encryption_algorithm: Mapped[str | None] = mapped_column(String(40))
    backup_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    imported_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RecoveryRestoreDrill(Base):
    __tablename__ = "recovery_restore_drills"
    __table_args__ = (
        UniqueConstraint("organization_id", "drill_id", name="uq_recovery_drill_org_drill"),
        Index(
            "ix_recovery_drill_org_status_completed", "organization_id", "status", "completed_at"
        ),
        Index("ix_recovery_drill_org_backup", "organization_id", "backup_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    backup_evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("recovery_backup_evidence.id", ondelete="SET NULL"), index=True
    )
    drill_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    backup_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    backup_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    product_version: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[RecoveryDrillMode] = mapped_column(
        Enum(RecoveryDrillMode, native_enum=False, length=24), nullable=False
    )
    status: Mapped[RecoveryDrillStatus] = mapped_column(
        Enum(RecoveryDrillStatus, native_enum=False, length=20), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    target_rto_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    rto_met: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature_status: Mapped[ArtifactSignatureStatus] = mapped_column(
        Enum(ArtifactSignatureStatus, native_enum=False, length=30),
        default=ArtifactSignatureStatus.UNSIGNED,
        nullable=False,
        index=True,
    )
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    signer_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    executed_host_hash: Mapped[str | None] = mapped_column(String(64))
    imported_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CampaignDestination(Base):
    __tablename__ = "campaign_destinations"
    __table_args__ = (
        UniqueConstraint("campaign_id", "destination_id", name="uq_campaign_destination"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    custom_body: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="destinations")
    destination: Mapped[Destination] = relationship(back_populates="campaign_links")


class CampaignRun(Base):
    __tablename__ = "campaign_runs"
    __table_args__ = (
        UniqueConstraint("campaign_id", "scheduled_for", name="uq_campaign_scheduled_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=20), default=RunStatus.QUEUED, nullable=False
    )
    total_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rollout_mode: Mapped[RolloutMode] = mapped_column(
        Enum(RolloutMode, native_enum=False, length=20),
        default=RolloutMode.STANDARD,
        nullable=False,
    )
    batch_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_batches: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_batch: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rollout_pause_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_threshold_percent: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    checkpoint_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    checkpoint_reason: Mapped[str | None] = mapped_column(Text)
    checkpoint_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    checkpoint_note: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="runs")
    jobs: Mapped[list[DeliveryJob]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ExecutionSite(Base, TimestampMixin):
    __tablename__ = "execution_sites"
    __table_args__ = (
        UniqueConstraint("organization_id", "site_key", name="uq_execution_site_org_key"),
        Index("ix_execution_site_org_seen", "organization_id", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    site_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_worker_id: Mapped[str | None] = mapped_column(String(120))
    hostname: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(40))
    environment: Mapped[str | None] = mapped_column(String(30))
    current_revision: Mapped[str | None] = mapped_column(String(64))
    expected_revision: Mapped[str | None] = mapped_column(String(64))
    schema_current: Mapped[bool | None] = mapped_column(Boolean)
    critical_config_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    release_payload_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ExecutionLease(Base, TimestampMixin):
    __tablename__ = "execution_leases"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_execution_lease_org"),
        Index("ix_execution_lease_site_status", "active_site_key", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    active_site_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    holder_worker_id: Mapped[str | None] = mapped_column(String(120), index=True)
    epoch: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    status: Mapped[ExecutionLeaseStatus] = mapped_column(
        Enum(ExecutionLeaseStatus, native_enum=False, length=20),
        default=ExecutionLeaseStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drain_reason: Mapped[str | None] = mapped_column(Text)
    drain_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FailoverRequest(Base):
    __tablename__ = "failover_requests"
    __table_args__ = (
        Index("ix_failover_org_status_created", "organization_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_site_key: Mapped[str] = mapped_column(String(80), nullable=False)
    target_site_key: Mapped[str] = mapped_column(String(80), nullable=False)
    source_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_epoch: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[FailoverRequestStatus] = mapped_column(
        Enum(FailoverRequestStatus, native_enum=False, length=20),
        default=FailoverRequestStatus.REQUESTED,
        nullable=False,
        index=True,
    )
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    blockers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ContinuityPolicy(Base, TimestampMixin):
    """Tenant policy defining acceptable continuity evidence and RTO limits."""

    __tablename__ = "continuity_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_continuity_policy_organization"),
        CheckConstraint(
            "max_rto_seconds >= 30 AND max_rto_seconds <= 86400",
            name="ck_continuity_policy_rto",
        ),
        CheckConstraint(
            "evidence_valid_days >= 1 AND evidence_valid_days <= 365",
            name="ck_continuity_policy_evidence_days",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_live_drill: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_rto_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    evidence_valid_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    require_distinct_signoff: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ContinuityDrill(Base, TimestampMixin):
    """Persisted simulation or live failover/failback exercise."""

    __tablename__ = "continuity_drills"
    __table_args__ = (
        Index("ix_continuity_drill_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_continuity_drill_org_expires", "organization_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mode: Mapped[ContinuityDrillMode] = mapped_column(
        Enum(ContinuityDrillMode, native_enum=False, length=20), nullable=False, index=True
    )
    status: Mapped[ContinuityDrillStatus] = mapped_column(
        Enum(ContinuityDrillStatus, native_enum=False, length=30),
        default=ContinuityDrillStatus.DRAFT,
        nullable=False,
        index=True,
    )
    source_site_key: Mapped[str] = mapped_column(String(80), nullable=False)
    target_site_key: Mapped[str] = mapped_column(String(80), nullable=False)
    source_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_epoch: Mapped[int | None] = mapped_column(BigInteger)
    return_epoch: Mapped[int | None] = mapped_column(BigInteger)
    failover_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("failover_requests.id"), index=True
    )
    failback_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("failover_requests.id"), index=True
    )
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    runtime_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    rto_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failback_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_off_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    signoff_note: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    events: Mapped[list[ContinuityDrillEvent]] = relationship(
        back_populates="drill", cascade="all, delete-orphan"
    )


class ContinuityDrillEvent(Base):
    """Append-only, hash-linked event in a continuity drill history."""

    __tablename__ = "continuity_drill_events"
    __table_args__ = (
        UniqueConstraint("drill_id", "sequence", name="uq_continuity_drill_event_sequence"),
        Index("ix_continuity_event_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    drill_id: Mapped[str] = mapped_column(
        ForeignKey("continuity_drills.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[ContinuityDrillEventType] = mapped_column(
        Enum(ContinuityDrillEventType, native_enum=False, length=40), nullable=False, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    drill: Mapped[ContinuityDrill] = relationship(back_populates="events")


class DeliveryJob(Base):
    __tablename__ = "delivery_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_delivery_idempotency"),
        Index("ix_delivery_org_due_status", "organization_id", "status", "due_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("destinations.id"), index=True, nullable=False
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("message_templates.id"), index=True, nullable=False
    )
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))

    body_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[ParseMode] = mapped_column(
        Enum(ParseMode, native_enum=False, length=20), nullable=False
    )
    link_preview: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=30),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    execution_site_key: Mapped[str | None] = mapped_column(String(80), index=True)
    execution_epoch: Mapped[int | None] = mapped_column(BigInteger, index=True)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    telegram_message_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    safety_decision: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review_resolution: Mapped[DeliveryReviewResolution | None] = mapped_column(
        Enum(DeliveryReviewResolution, native_enum=False, length=30)
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[CampaignRun] = relationship(back_populates="jobs")
    campaign: Mapped[Campaign] = relationship()
    connection: Mapped[TelegramConnection] = relationship()
    destination: Mapped[Destination] = relationship()
    template: Mapped[MessageTemplate] = relationship()
    media_asset: Mapped[MediaAsset | None] = relationship()
    attempts: Mapped[list[DeliveryAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_delivery_attempt_job_number"),
        Index("ix_delivery_attempt_org_status", "organization_id", "status"),
        Index("ix_delivery_attempt_job_created", "job_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False)
    site_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    fence_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[DeliveryAttemptStatus] = mapped_column(
        Enum(DeliveryAttemptStatus, native_enum=False, length=32),
        default=DeliveryAttemptStatus.PREPARED,
        nullable=False,
        index=True,
    )
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    network_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    job: Mapped[DeliveryJob] = relationship(back_populates="attempts")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_org_created_action", "organization_id", "created_at", "action"),
        Index("ix_audit_org_sequence", "organization_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80), index=True)
    severity: Mapped[SafetySeverity] = mapped_column(
        Enum(SafetySeverity, native_enum=False, length=20),
        default=SafetySeverity.INFO,
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sequence: Mapped[int | None] = mapped_column(BigInteger)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    entry_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    chain_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class AuditChainState(Base):
    __tablename__ = "audit_chain_states"

    chain_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(String(36), index=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(64), default="0" * 64, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_notification_org_dedup", "organization_id", "dedup_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    severity: Mapped[SafetySeverity] = mapped_column(
        Enum(SafetySeverity, native_enum=False, length=20),
        default=SafetySeverity.INFO,
        nullable=False,
    )
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, native_enum=False, length=20),
        default=NotificationStatus.UNREAD,
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80), index=True)
    dedup_key: Mapped[str | None] = mapped_column(String(240))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class TelegramAuthChallenge(Base):
    __tablename__ = "telegram_auth_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    payload_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ChallengeStatus] = mapped_column(
        Enum(ChallengeStatus, native_enum=False, length=20),
        default=ChallengeStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TelegramBusinessConnection(Base, TimestampMixin):
    __tablename__ = "telegram_business_connections"
    __table_args__ = (
        UniqueConstraint(
            "telegram_connection_id", "business_connection_id", name="uq_bot_business_connection"
        ),
        Index("ix_business_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    telegram_connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    business_connection_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    status: Mapped[BusinessConnectionStatus] = mapped_column(
        Enum(BusinessConnectionStatus, native_enum=False, length=24),
        default=BusinessConnectionStatus.ACTIVE,
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    user_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(80))
    last_name: Mapped[str | None] = mapped_column(String(80))
    rights: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    telegram_connection: Mapped[TelegramConnection] = relationship(
        back_populates="business_connections"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="business_connection", cascade="all, delete-orphan"
    )


class InboundTelegramUpdate(Base):
    __tablename__ = "inbound_telegram_updates"
    __table_args__ = (
        UniqueConstraint(
            "telegram_connection_id", "telegram_update_id", name="uq_connection_update_id"
        ),
        Index("ix_inbound_org_status_received", "organization_id", "status", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    telegram_connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    telegram_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[InboundUpdateStatus] = mapped_column(
        Enum(InboundUpdateStatus, native_enum=False, length=20),
        default=InboundUpdateStatus.RECEIVED,
        nullable=False,
    )
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    payload_enc: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutomationFlow(Base, TimestampMixin):
    __tablename__ = "automation_flows"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_flow_org_name"),
        Index("ix_flow_org_active", "organization_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class AutomationPolicy(Base, TimestampMixin):
    __tablename__ = "automation_policies"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_policy_org_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    telegram_connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ai_provider_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_provider_configs.id", ondelete="SET NULL")
    )
    automation_flow_id: Mapped[str | None] = mapped_column(
        ForeignKey("automation_flows.id", ondelete="SET NULL"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timezone_name: Mapped[str] = mapped_column(
        String(80), default="Europe/Helsinki", nullable=False
    )
    active_hours: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    allowed_chat_types: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["private"], nullable=False
    )
    consent_notice: Mapped[str] = mapped_column(
        Text,
        default=(
            "Здравствуйте! В этом чате используется автоматизированный помощник. "
            "Продолжая диалог, вы соглашаетесь на обработку предоставленных данных для связи по вакансии. "
            "Напишите «оператор», чтобы перейти к человеку, или «стоп», чтобы прекратить автоматизацию."
        ),
        nullable=False,
    )
    fallback_message: Mapped[str] = mapped_column(
        Text,
        default="Передаю ваш вопрос специалисту. Ответим вручную.",
        nullable=False,
    )
    max_auto_replies_per_day: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    require_consent_before_ai: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    handoff_keywords: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["оператор", "человек", "менеджер"], nullable=False
    )
    stop_words: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["стоп", "отмена", "не писать"], nullable=False
    )
    vacancy_detection_rules: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)

    ai_provider: Mapped[AIProviderConfig | None] = relationship()
    automation_flow: Mapped[AutomationFlow | None] = relationship()


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "business_connection_id", "telegram_chat_id", name="uq_business_conversation_chat"
        ),
        Index("ix_conversation_org_status_last", "organization_id", "status", "last_message_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    business_connection_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_business_connections.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    automation_policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("automation_policies.id", ondelete="SET NULL")
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(80))
    last_name: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, native_enum=False, length=24),
        default=ConversationStatus.NEW,
        nullable=False,
        index=True,
    )
    consent_status: Mapped[ConsentStatus] = mapped_column(
        Enum(ConsentStatus, native_enum=False, length=20),
        default=ConsentStatus.UNKNOWN,
        nullable=False,
    )
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_notice_version: Mapped[str | None] = mapped_column(String(40))
    source_campaign_id: Mapped[str | None] = mapped_column(ForeignKey("campaigns.id"))
    vacancy_key: Mapped[str | None] = mapped_column(String(120), index=True)
    assigned_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    flow_node_id: Mapped[str | None] = mapped_column(String(64))
    flow_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    flow_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    business_connection: Mapped[TelegramBusinessConnection] = relationship(
        back_populates="conversations"
    )
    policy: Mapped[AutomationPolicy | None] = relationship()
    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )
    candidate: Mapped[CandidateProfile | None] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "telegram_message_id",
            "direction",
            name="uq_conversation_message_direction",
        ),
        Index(
            "ix_message_org_conversation_created",
            "organization_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, native_enum=False, length=16), nullable=False
    )
    author: Mapped[MessageAuthor] = mapped_column(
        Enum(MessageAuthor, native_enum=False, length=20), nullable=False
    )
    body_enc: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), default="text", nullable=False)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class CandidateProfile(Base, TimestampMixin):
    __tablename__ = "candidate_profiles"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_candidate_conversation"),
        Index("ix_candidate_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str | None] = mapped_column(String(180))
    city: Mapped[str | None] = mapped_column(String(120))
    age: Mapped[int | None] = mapped_column(Integer)
    experience: Mapped[str | None] = mapped_column(Text)
    schedule: Mapped[str | None] = mapped_column(String(250))
    phone_enc: Mapped[str | None] = mapped_column(Text)
    email_enc: Mapped[str | None] = mapped_column(Text)
    vacancy_key: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus, native_enum=False, length=24),
        default=CandidateStatus.NEW,
        nullable=False,
    )
    summary: Mapped[str | None] = mapped_column(Text)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    consent_to_storage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="candidate")


class KnowledgeBaseArticle(Base, TimestampMixin):
    __tablename__ = "knowledge_base_articles"
    __table_args__ = (
        Index("ix_kb_org_active_vacancy", "organization_id", "is_active", "vacancy_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    vacancy_key: Mapped[str | None] = mapped_column(String(120), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class AIProviderConfig(Base, TimestampMixin):
    __tablename__ = "ai_provider_configs"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_ai_provider_org_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    kind: Mapped[AIProviderKind] = mapped_column(
        Enum(AIProviderKind, native_enum=False, length=24),
        default=AIProviderKind.RULE_BASED,
        nullable=False,
    )
    base_url: Mapped[str | None] = mapped_column(String(1000))
    model_name: Mapped[str | None] = mapped_column(String(160))
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    temperature_milli: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    data_region: Mapped[str | None] = mapped_column(String(80))
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    allowed_models: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class AIInteraction(Base):
    __tablename__ = "ai_interactions"
    __table_args__ = (Index("ix_ai_org_created_status", "organization_id", "created_at", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL")
    )
    provider_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_provider_configs.id", ondelete="SET NULL")
    )
    provider_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    prompt_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    response_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[AIInteractionStatus] = mapped_column(
        Enum(AIInteractionStatus, native_enum=False, length=20), nullable=False
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    safety_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class IntegrationEndpoint(Base, TimestampMixin):
    __tablename__ = "integration_endpoints"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_integration_org_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[IntegrationKind] = mapped_column(
        Enum(IntegrationKind, native_enum=False, length=24), nullable=False
    )
    config_enc: Mapped[str | None] = mapped_column(Text)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_due_status", "status", "due_at"),
        Index("ix_outbox_org_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    target_endpoint_ids: Mapped[list[str] | None] = mapped_column(JSON)
    delivery_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, native_enum=False, length=20),
        default=OutboxStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("secret_hash", name="uq_api_key_secret_hash"),
        Index("ix_api_key_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[ApiKeyStatus] = mapped_column(
        Enum(ApiKeyStatus, native_enum=False, length=20),
        default=ApiKeyStatus.ACTIVE,
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PrivacyRequest(Base):
    __tablename__ = "privacy_requests"
    __table_args__ = (
        Index("ix_privacy_org_status_created", "organization_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    request_type: Mapped[PrivacyRequestType] = mapped_column(
        Enum(PrivacyRequestType, native_enum=False, length=20), nullable=False
    )
    status: Mapped[PrivacyRequestStatus] = mapped_column(
        Enum(PrivacyRequestStatus, native_enum=False, length=20),
        default=PrivacyRequestStatus.PENDING,
        nullable=False,
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    requested_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    output_relative_path: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
