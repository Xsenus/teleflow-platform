from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

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


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class MessageResponse(BaseModel):
    message: str


class OrganizationRead(ORMModel):
    id: str
    name: str
    slug: str
    status: OrganizationStatus
    timezone_name: str
    retention_days: int
    ai_enabled: bool
    settings: dict[str, Any]
    publishing_paused: bool
    publishing_pause_reason: str | None
    publishing_paused_at: datetime | None
    publishing_paused_by_id: str | None
    maintenance_mode: bool
    maintenance_reason: str | None
    maintenance_started_at: datetime | None
    maintenance_started_by_id: str | None
    require_distinct_campaign_approver: bool
    high_risk_destination_threshold: int
    high_risk_required_approvals: int
    approval_request_ttl_hours: int
    pilot_stage: PilotStage
    pilot_stage_updated_at: datetime | None
    pilot_stage_updated_by_id: str | None
    pilot_stage_note: str | None
    created_at: datetime
    updated_at: datetime


class OrganizationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    timezone_name: str | None = Field(default=None, min_length=1, max_length=80)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    ai_enabled: bool | None = None
    settings: dict[str, Any] | None = None
    require_distinct_campaign_approver: bool | None = None
    high_risk_destination_threshold: int | None = Field(default=None, ge=1, le=500)
    high_risk_required_approvals: int | None = Field(default=None, ge=1, le=5)
    approval_request_ttl_hours: int | None = Field(default=None, ge=1, le=168)


class PublishingPauseRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class PublishingResumeRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class MaintenanceStartRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class MaintenanceStopRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ReleaseAttestationImport(BaseModel):
    payload: dict[str, Any]
    signature: dict[str, Any]


class ReleaseAttestationRead(ORMModel):
    id: str
    organization_id: str
    version: str
    source_commit: str | None
    payload: dict[str, Any]
    payload_sha256: str
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    verified_at: datetime | None
    created_by_id: str
    created_at: datetime


class ReleaseTransparencyRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ReleaseTransparencyEventRead(ORMModel):
    id: str
    organization_id: str
    release_attestation_id: str
    sequence: int
    event_type: ReleaseTransparencyEventType
    payload: dict[str, Any]
    previous_hash: str
    entry_hash: str
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    created_by_id: str
    created_at: datetime


class ReleaseTransparencyVerification(BaseModel):
    valid: bool
    entry_count: int
    last_sequence: int
    last_hash: str
    active_release_count: int
    errors: list[str]


class DependencyPolicyRead(ORMModel):
    id: str
    organization_id: str
    require_exact_pins: bool
    allow_prerelease: bool
    require_vulnerability_scan: bool
    require_trusted_report: bool
    max_critical: int
    max_high: int
    max_medium: int
    report_ttl_hours: int
    denied_packages: list[str]
    updated_by_id: str | None
    created_at: datetime
    updated_at: datetime


class DependencyPolicyPatch(BaseModel):
    require_exact_pins: bool | None = None
    allow_prerelease: bool | None = None
    require_vulnerability_scan: bool | None = None
    require_trusted_report: bool | None = None
    max_critical: int | None = Field(default=None, ge=0, le=10000)
    max_high: int | None = Field(default=None, ge=0, le=10000)
    max_medium: int | None = Field(default=None, ge=0, le=10000)
    report_ttl_hours: int | None = Field(default=None, ge=1, le=720)
    denied_packages: list[str] | None = Field(default=None, max_length=500)


class DependencyAssessmentCreate(BaseModel):
    report: dict[str, Any]
    signature: dict[str, Any] | None = None
    sign_with_default_key: bool = True


class DependencyInventoryCreate(BaseModel):
    scanner_name: str = Field(default="teleflow-inventory", min_length=2, max_length=120)
    scanner_version: str | None = Field(default=None, max_length=80)


class ReleaseDependencyAssessmentRead(ORMModel):
    id: str
    organization_id: str
    release_attestation_id: str
    report_kind: DependencyReportKind
    scanner_name: str
    scanner_version: str | None
    report_payload: dict[str, Any]
    report_sha256: str
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    sbom_payload: dict[str, Any]
    sbom_sha256: str
    policy_snapshot: dict[str, Any]
    policy_sha256: str
    attestation_payload_sha256: str
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    unknown_count: int
    unpinned_count: int
    prerelease_count: int
    denied_count: int
    status: ReadinessStatus
    blockers: list[str]
    warnings: list[str]
    verified_at: datetime | None
    expires_at: datetime
    created_by_id: str
    created_at: datetime


class SLOPolicyRead(ORMModel):
    id: str
    organization_id: str
    enabled: bool
    evaluation_window_hours: int
    delivery_success_target_bps: int
    minimum_delivery_sample_size: int
    max_queue_age_seconds: int
    max_worker_heartbeat_age_seconds: int
    max_unresolved_delivery_reviews: int
    max_open_critical_incidents: int
    error_budget_warning_percent: int
    error_budget_critical_percent: int
    assessment_ttl_minutes: int
    gate_publishing: bool
    gate_changes: bool
    auto_create_incidents: bool
    auto_resolve_incidents: bool
    suppress_incidents_during_maintenance: bool
    created_by_id: str | None
    updated_by_id: str | None
    created_at: datetime
    updated_at: datetime


class SLOPolicyPatch(BaseModel):
    enabled: bool | None = None
    evaluation_window_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    delivery_success_target_bps: int | None = Field(default=None, ge=5000, le=10000)
    minimum_delivery_sample_size: int | None = Field(default=None, ge=1, le=1_000_000)
    max_queue_age_seconds: int | None = Field(default=None, ge=1, le=30 * 24 * 3600)
    max_worker_heartbeat_age_seconds: int | None = Field(default=None, ge=5, le=24 * 3600)
    max_unresolved_delivery_reviews: int | None = Field(default=None, ge=0, le=100_000)
    max_open_critical_incidents: int | None = Field(default=None, ge=0, le=10_000)
    error_budget_warning_percent: int | None = Field(default=None, ge=1, le=10_000)
    error_budget_critical_percent: int | None = Field(default=None, ge=1, le=10_000)
    assessment_ttl_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    gate_publishing: bool | None = None
    gate_changes: bool | None = None
    auto_create_incidents: bool | None = None
    auto_resolve_incidents: bool | None = None
    suppress_incidents_during_maintenance: bool | None = None

    @model_validator(mode="after")
    def validate_budget_thresholds(self):
        """Проверить budget thresholds класса SLOPolicyPatch. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if (
            self.error_budget_warning_percent is not None
            and self.error_budget_critical_percent is not None
            and self.error_budget_warning_percent >= self.error_budget_critical_percent
        ):
            raise ValueError("Порог предупреждения error budget должен быть меньше критического")
        return self


class SLOAssessmentRead(ORMModel):
    id: str
    organization_id: str
    source: SLOAssessmentSource
    status: ReadinessStatus
    window_start: datetime
    window_end: datetime
    policy_snapshot: dict[str, Any]
    policy_sha256: str
    metrics: dict[str, Any]
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    eligible_deliveries: int
    successful_deliveries: int
    failed_deliveries: int
    uncertain_deliveries: int
    delivery_success_rate_bps: int | None
    error_budget_consumed_bps: int | None
    oldest_queue_age_seconds: int
    worker_heartbeat_age_seconds: int | None
    open_critical_incidents: int
    unresolved_delivery_reviews: int
    fingerprint: str
    created_by_id: str | None
    expires_at: datetime
    created_at: datetime


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=220)
    summary: str = Field(min_length=5, max_length=8000)
    severity: SafetySeverity = SafetySeverity.WARNING
    impact: str | None = Field(default=None, max_length=8000)
    owner_user_id: str | None = Field(default=None, min_length=36, max_length=36)
    started_at: datetime | None = None


class IncidentPatch(BaseModel):
    severity: SafetySeverity | None = None
    owner_user_id: str | None = Field(default=None, min_length=36, max_length=36)
    impact: str | None = Field(default=None, max_length=8000)
    root_cause: str | None = Field(default=None, max_length=12000)
    postmortem_url: str | None = Field(default=None, max_length=1000)


class IncidentActionRequest(BaseModel):
    note: str = Field(min_length=3, max_length=8000)
    owner_user_id: str | None = Field(default=None, min_length=36, max_length=36)
    root_cause: str | None = Field(default=None, max_length=12000)
    postmortem_url: str | None = Field(default=None, max_length=1000)


class IncidentRead(ORMModel):
    id: str
    organization_id: str
    status: IncidentStatus
    severity: SafetySeverity
    source: IncidentSource
    title: str
    summary: str
    impact: str | None
    dedup_key: str | None
    linked_slo_assessment_id: str | None
    owner_user_id: str | None
    metadata_payload: dict[str, Any]
    detected_at: datetime
    started_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by_id: str | None
    mitigating_at: datetime | None
    mitigating_by_id: str | None
    resolved_at: datetime | None
    resolved_by_id: str | None
    closed_at: datetime | None
    closed_by_id: str | None
    resolution_summary: str | None
    root_cause: str | None
    postmortem_url: str | None
    created_by_id: str | None
    created_at: datetime
    updated_at: datetime


class IncidentEventRead(ORMModel):
    id: str
    organization_id: str
    incident_id: str
    event_type: IncidentEventType
    message: str | None
    payload: dict[str, Any]
    actor_user_id: str | None
    created_at: datetime


class OperationsOverviewRead(BaseModel):
    policy: SLOPolicyRead
    latest_assessment: SLOAssessmentRead | None
    assessment_current: bool
    publishing_gate_allowed: bool
    publishing_gate_message: str
    changes_gate_allowed: bool
    changes_gate_message: str
    open_incidents: int
    open_critical_incidents: int
    incidents_requiring_attention: int


class ChangeRequestCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    change_type: ChangeRequestType
    current_version: str | None = Field(default=None, max_length=40)
    target_version: str | None = Field(default=None, max_length=40)
    release_attestation_id: str | None = Field(default=None, max_length=36)
    release_dependency_assessment_id: str | None = Field(default=None, max_length=36)
    reason: str = Field(min_length=5, max_length=4000)
    risk_summary: str = Field(min_length=5, max_length=4000)
    rollback_plan: str = Field(min_length=5, max_length=6000)
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_change_window(self):
        """Проверить change window класса ChangeRequestCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if self.change_type == ChangeRequestType.UPGRADE and not self.target_version:
            raise ValueError("Для обновления укажите целевую версию")
        if (
            self.planned_start_at
            and self.planned_end_at
            and self.planned_end_at <= self.planned_start_at
        ):
            raise ValueError("Окончание окна изменения должно быть позже начала")
        return self


class ChangeRequestRead(ORMModel):
    id: str
    organization_id: str
    title: str
    change_type: ChangeRequestType
    status: ChangeRequestStatus
    current_version: str | None
    target_version: str | None
    release_attestation_id: str | None
    release_dependency_assessment_id: str | None
    reason: str
    risk_summary: str
    rollback_plan: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    fingerprint: str
    created_by_id: str
    approved_by_id: str | None
    approved_at: datetime | None
    started_by_id: str | None
    started_at: datetime | None
    completed_by_id: str | None
    completed_at: datetime | None
    result_summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ChangeActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


class DeploymentVerificationRead(ORMModel):
    id: str
    organization_id: str
    change_request_id: str
    phase: DeploymentVerificationPhase
    status: ReadinessStatus
    expected_version: str | None
    observed_version: str
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    fingerprint: str
    created_by_id: str
    expires_at: datetime
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=500)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class UserRead(ORMModel):
    id: str
    organization_id: str
    email: EmailStr
    display_name: str
    role: UserRole
    is_active: bool
    must_change_password: bool
    totp_enabled: bool
    created_at: datetime
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    user: UserRead
    organization: OrganizationRead | None = None
    access_expires_at: datetime


class RefreshSessionRead(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    created_ip: str | None = None
    user_agent: str | None = None
    current: bool = False


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=500)
    role: UserRole = UserRole.VIEWER


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=12, max_length=500)


class TotpStartResponse(BaseModel):
    provisioning_uri: str
    qr_data_uri: str
    secret_hint: str


class TotpConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class TotpDisableRequest(BaseModel):
    password: str
    code: str = Field(pattern=r"^\d{6}$")


class ConnectionSafetyInput(BaseModel):
    min_interval_seconds: int | None = Field(default=None, ge=1, le=86400)
    daily_cap: int | None = Field(default=None, ge=1, le=500)
    destination_cooldown_minutes: int | None = Field(default=None, ge=1, le=525600)
    require_manual_approval: bool = True
    stop_on_flood: bool = True


class BotConnectionCreate(ConnectionSafetyInput):
    name: str = Field(min_length=2, max_length=120)
    bot_token: str = Field(min_length=20, max_length=500)


class UserConnectionStart(ConnectionSafetyInput):
    name: str = Field(min_length=2, max_length=120)
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=20, max_length=200)
    phone: str = Field(min_length=5, max_length=40)


class UserConnectionComplete(BaseModel):
    challenge_id: str
    code: str = Field(min_length=3, max_length=20)
    password: str | None = Field(default=None, max_length=500)


class UserConnectionStartResponse(BaseModel):
    connection_id: str
    challenge_id: str
    expires_at: datetime
    code_hint: str | None = None


class ConnectionRead(ORMModel):
    id: str
    organization_id: str
    name: str
    kind: ConnectionKind
    status: ConnectionStatus
    telegram_account_id: int | None
    telegram_username: str | None
    telegram_display_name: str | None
    min_interval_seconds: int
    daily_cap: int
    destination_cooldown_minutes: int
    require_manual_approval: bool
    stop_on_flood: bool
    flood_blocked_until: datetime | None
    last_checked_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionPatch(ConnectionSafetyInput):
    name: str | None = Field(default=None, min_length=2, max_length=120)


class ConnectionHealthResponse(BaseModel):
    ok: bool
    status: ConnectionStatus
    identity: dict[str, Any] | None = None
    error: str | None = None


class DiscoveredDestination(BaseModel):
    telegram_chat_id: int
    username: str | None
    title: str
    kind: DestinationKind


class DestinationCreate(BaseModel):
    connection_id: str
    telegram_chat_id: int | None = None
    username: str | None = Field(default=None, min_length=2, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    kind: DestinationKind = DestinationKind.SUPERGROUP
    topic_id: int | None = Field(default=None, ge=1)
    permission_confirmed: bool = False
    permission_note: str | None = Field(default=None, max_length=4000)
    rules_url: HttpUrl | None = None
    permission_expires_at: datetime | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_weekdays: list[int] = Field(default_factory=list, max_length=7)
    allowed_start_time: time | None = None
    allowed_end_time: time | None = None
    cooldown_minutes_override: int | None = Field(default=None, ge=1, le=525600)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        """Проверить timezone name класса DestinationCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Неизвестный часовой пояс") from exc
        return value

    @field_validator("allowed_weekdays")
    @classmethod
    def validate_allowed_weekdays(cls, value: list[int]) -> list[int]:
        """Проверить allowed weekdays класса DestinationCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("Дни недели должны быть числами от 0 до 6")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_target_and_permission(self) -> DestinationCreate:
        """Проверить target and permission класса DestinationCreate. Некорректные данные или
        состояние отклоняются до побочного эффекта.
        """
        if self.telegram_chat_id is None and not self.username:
            raise ValueError("Нужно указать telegram_chat_id или username")
        if self.kind == DestinationKind.FORUM_TOPIC and not self.topic_id:
            raise ValueError("Для темы форума требуется topic_id")
        if self.permission_confirmed and not (self.permission_note or self.rules_url):
            raise ValueError("Зафиксируйте основание разрешения: примечание или ссылка на правила")
        if self.permission_expires_at and not self.permission_confirmed:
            raise ValueError("Срок разрешения задаётся только для подтверждённого разрешения")
        if self.permission_expires_at:
            expiry = self.permission_expires_at
            expiry_utc = expiry.astimezone(UTC) if expiry.tzinfo else expiry.replace(tzinfo=UTC)
            if expiry_utc <= datetime.now(UTC):
                raise ValueError("Срок разрешения должен быть в будущем")
        if (self.allowed_start_time is None) != (self.allowed_end_time is None):
            raise ValueError("Начало и окончание временного окна задаются вместе")
        if self.allowed_start_time and self.allowed_start_time == self.allowed_end_time:
            raise ValueError("Начало и окончание временного окна не должны совпадать")
        return self


class DestinationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    permission_status: PermissionStatus | None = None
    permission_note: str | None = Field(default=None, max_length=4000)
    rules_url: HttpUrl | None = None
    permission_expires_at: datetime | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_weekdays: list[int] | None = Field(default=None, max_length=7)
    allowed_start_time: time | None = None
    allowed_end_time: time | None = None
    cooldown_minutes_override: int | None = Field(default=None, ge=1, le=525600)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        """Проверить timezone name класса DestinationPatch. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        return DestinationCreate.validate_timezone_name(value)

    @field_validator("allowed_weekdays")
    @classmethod
    def validate_allowed_weekdays(cls, value: list[int] | None) -> list[int] | None:
        """Проверить allowed weekdays класса DestinationPatch. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if value is None:
            return value
        return DestinationCreate.validate_allowed_weekdays(value)


class DestinationRead(ORMModel):
    id: str
    organization_id: str
    connection_id: str
    telegram_chat_id: int | None
    username: str | None
    title: str
    kind: DestinationKind
    topic_id: int | None
    enabled: bool
    validated: bool
    validated_at: datetime | None
    validation_expires_at: datetime | None
    permission_status: PermissionStatus
    permission_note: str | None
    rules_url: str | None
    permission_confirmed_at: datetime | None
    permission_reviewed_at: datetime | None
    permission_expires_at: datetime | None
    timezone_name: str | None
    allowed_weekdays: list[int]
    allowed_start_time: time | None
    allowed_end_time: time | None
    cooldown_minutes_override: int | None
    last_sent_at: datetime | None
    next_allowed_at: datetime | None
    consecutive_failures: int
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class DestinationValidationResponse(BaseModel):
    ok: bool
    destination: DestinationRead
    capabilities: dict[str, Any] = Field(default_factory=dict)


class DestinationValidationRecordRead(ORMModel):
    id: str
    organization_id: str
    destination_id: str
    connection_id: str
    status: DestinationValidationStatus
    capabilities: dict[str, Any]
    error_code: str | None
    error_message: str | None
    source: str
    checked_by_id: str | None
    checked_at: datetime


class DestinationBatchValidationRequest(BaseModel):
    destination_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("destination_ids")
    @classmethod
    def unique_destination_ids(cls, value: list[str]) -> list[str]:
        """Выполнить операцию unique destination ids класса DestinationBatchValidationRequest.
        Аргументы интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        unique = list(dict.fromkeys(value))
        if len(unique) != len(value):
            raise ValueError("Список содержит повторяющиеся destination_id")
        return unique


class DestinationBatchValidationItem(BaseModel):
    destination_id: str
    ok: bool
    status: Literal["passed", "failed", "write_forbidden", "deferred"]
    title: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class DestinationBatchValidationResponse(BaseModel):
    total: int
    passed: int
    failed: int
    write_forbidden: int
    deferred: int
    items: list[DestinationBatchValidationItem]


class DestinationImportRow(BaseModel):
    row_number: int
    source: str
    status: Literal["ready", "created", "duplicate", "error", "deferred"]
    message: str
    username: str | None = None
    telegram_chat_id: int | None = None
    topic_id: int | None = None
    title: str | None = None
    permission_confirmed: bool = False
    destination_id: str | None = None


class DestinationImportResponse(BaseModel):
    dry_run: bool
    total: int
    ready: int
    created: int
    duplicates: int
    errors: int
    deferred: int
    rows: list[DestinationImportRow]


class DestinationDiscoveryImportItem(BaseModel):
    telegram_chat_id: int
    username: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    kind: DestinationKind = DestinationKind.SUPERGROUP
    topic_id: int | None = Field(default=None, ge=1)


class DestinationDiscoveryImportRequest(BaseModel):
    connection_id: str
    items: list[DestinationDiscoveryImportItem] = Field(min_length=1, max_length=500)
    permission_confirmed: bool = False
    permission_note: str | None = Field(default=None, max_length=4000)
    rules_url: HttpUrl | None = None
    permission_expires_at: datetime | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_weekdays: list[int] = Field(default_factory=list, max_length=7)
    allowed_start_time: time | None = None
    allowed_end_time: time | None = None
    cooldown_minutes_override: int | None = Field(default=None, ge=1, le=525600)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        """Проверить timezone name класса DestinationDiscoveryImportRequest. Некорректные данные
        или состояние отклоняются до побочного эффекта.
        """
        return DestinationCreate.validate_timezone_name(value)

    @field_validator("allowed_weekdays")
    @classmethod
    def validate_allowed_weekdays(cls, value: list[int]) -> list[int]:
        """Проверить allowed weekdays класса DestinationDiscoveryImportRequest. Некорректные данные
        или состояние отклоняются до побочного эффекта.
        """
        return DestinationCreate.validate_allowed_weekdays(value)

    @model_validator(mode="after")
    def validate_import(self) -> DestinationDiscoveryImportRequest:
        """Проверить import класса DestinationDiscoveryImportRequest. Некорректные данные или
        состояние отклоняются до побочного эффекта.
        """
        if self.permission_confirmed and not (self.permission_note or self.rules_url):
            raise ValueError(
                "Для подтверждения разрешения добавьте примечание или ссылку на правила"
            )
        if self.permission_expires_at and not self.permission_confirmed:
            raise ValueError("Срок разрешения задаётся только для подтверждённого разрешения")
        if self.permission_expires_at:
            expiry = self.permission_expires_at
            expiry_utc = expiry.astimezone(UTC) if expiry.tzinfo else expiry.replace(tzinfo=UTC)
            if expiry_utc <= datetime.now(UTC):
                raise ValueError("Срок разрешения должен быть в будущем")
        if (self.allowed_start_time is None) != (self.allowed_end_time is None):
            raise ValueError("Начало и окончание временного окна задаются вместе")
        if self.allowed_start_time and self.allowed_start_time == self.allowed_end_time:
            raise ValueError("Начало и окончание временного окна не должны совпадать")
        return self


class PublishingBlackoutCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    reason: str = Field(min_length=3, max_length=4000)
    scope: BlackoutScope = BlackoutScope.ORGANIZATION
    kind: BlackoutKind = BlackoutKind.ONE_TIME
    connection_id: str | None = None
    destination_id: str | None = None
    enabled: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone_name: str | None = Field(default=None, max_length=80)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    start_time: time | None = None
    end_time: time | None = None

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        """Проверить timezone name класса PublishingBlackoutCreate. Некорректные данные или
        состояние отклоняются до побочного эффекта.
        """
        return DestinationCreate.validate_timezone_name(value)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        """Проверить weekdays класса PublishingBlackoutCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        return DestinationCreate.validate_allowed_weekdays(value)

    @model_validator(mode="after")
    def validate_scope_and_window(self) -> PublishingBlackoutCreate:
        """Проверить scope and window класса PublishingBlackoutCreate. Некорректные данные или
        состояние отклоняются до побочного эффекта.
        """
        if self.scope == BlackoutScope.ORGANIZATION:
            if self.connection_id or self.destination_id:
                raise ValueError("Для запрета организации connection_id/destination_id не задаются")
        elif self.scope == BlackoutScope.CONNECTION:
            if not self.connection_id or self.destination_id:
                raise ValueError("Для запрета подключения требуется только connection_id")
        elif self.scope == BlackoutScope.DESTINATION:
            if not self.destination_id:
                raise ValueError("Для запрета назначения требуется destination_id")

        if self.kind == BlackoutKind.ONE_TIME:
            if not self.starts_at or not self.ends_at:
                raise ValueError("Для разового запрета задайте starts_at и ends_at")
            starts = (
                self.starts_at.astimezone(UTC)
                if self.starts_at.tzinfo
                else self.starts_at.replace(tzinfo=UTC)
            )
            ends = (
                self.ends_at.astimezone(UTC)
                if self.ends_at.tzinfo
                else self.ends_at.replace(tzinfo=UTC)
            )
            if ends <= starts:
                raise ValueError("Окончание запрета должно быть позже начала")
            if self.weekdays or self.start_time or self.end_time:
                raise ValueError("Разовый запрет не использует weekly-поля")
        else:
            if not self.timezone_name:
                raise ValueError("Для еженедельного запрета требуется timezone_name")
            if not self.weekdays:
                raise ValueError("Для еженедельного запрета выберите дни недели")
            if self.start_time is None or self.end_time is None:
                raise ValueError("Для еженедельного запрета задайте start_time и end_time")
            if self.start_time == self.end_time:
                raise ValueError("Начало и окончание weekly-запрета не должны совпадать")
            if self.starts_at or self.ends_at:
                raise ValueError("Еженедельный запрет не использует starts_at/ends_at")
        return self


class PublishingBlackoutPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=180)
    reason: str | None = Field(default=None, min_length=3, max_length=4000)
    scope: BlackoutScope | None = None
    kind: BlackoutKind | None = None
    connection_id: str | None = None
    destination_id: str | None = None
    enabled: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone_name: str | None = Field(default=None, max_length=80)
    weekdays: list[int] | None = Field(default=None, max_length=7)
    start_time: time | None = None
    end_time: time | None = None

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str | None) -> str | None:
        """Проверить timezone name класса PublishingBlackoutPatch. Некорректные данные или
        состояние отклоняются до побочного эффекта.
        """
        return DestinationCreate.validate_timezone_name(value)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        """Проверить weekdays класса PublishingBlackoutPatch. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if value is None:
            return value
        return DestinationCreate.validate_allowed_weekdays(value)


class PublishingBlackoutRead(ORMModel):
    id: str
    organization_id: str
    title: str
    reason: str
    scope: BlackoutScope
    kind: BlackoutKind
    connection_id: str | None
    destination_id: str | None
    enabled: bool
    starts_at: datetime | None
    ends_at: datetime | None
    timezone_name: str | None
    weekdays: list[int]
    start_time: time | None
    end_time: time | None
    created_by_id: str
    created_at: datetime
    updated_at: datetime


class BlackoutMatchRead(BaseModel):
    blackout_id: str
    title: str
    reason: str
    scope: BlackoutScope
    active_until: datetime


class BlackoutEvaluationRead(BaseModel):
    active: bool
    defer_until: datetime | None
    matches: list[BlackoutMatchRead]


class MediaAssetRead(ORMModel):
    id: str
    organization_id: str
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_backend: str
    created_at: datetime


class TemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    body: str = Field(min_length=1, max_length=4096)
    parse_mode: ParseMode = ParseMode.PLAIN
    media_asset_id: str | None = None
    link_preview: bool = True

    @model_validator(mode="after")
    def validate_caption_length(self) -> TemplateCreate:
        """Проверить caption length класса TemplateCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if self.media_asset_id and len(self.body) > 1024:
            raise ValueError("Подпись к медиа не может превышать 1024 символа")
        return self


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    body: str | None = Field(default=None, min_length=1, max_length=4096)
    parse_mode: ParseMode | None = None
    media_asset_id: str | None = None
    link_preview: bool | None = None
    is_active: bool | None = None


class TemplateRead(ORMModel):
    id: str
    organization_id: str
    name: str
    body: str
    parse_mode: ParseMode
    media_asset_id: str | None
    link_preview: bool
    is_active: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    connection_id: str
    template_id: str
    secondary_template_id: str | None = None
    secondary_template_weight: int = Field(default=0, ge=0, le=99)
    destination_ids: list[str] = Field(min_length=1, max_length=500)
    schedule_type: ScheduleType = ScheduleType.ONCE
    schedule_at: datetime
    timezone_name: str = Field(default="Europe/Helsinki", min_length=1, max_length=80)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    spacing_seconds: int = Field(default=90, ge=1, le=86400)
    rollout_mode: RolloutMode = RolloutMode.STANDARD
    rollout_batch_size: int = Field(default=5, ge=1, le=500)
    rollout_pause_seconds: int = Field(default=300, ge=0, le=86400)
    rollout_require_checkpoint: bool = True
    rollout_failure_threshold_percent: int = Field(default=20, ge=0, le=100)
    duplicate_guard_minutes: int = Field(default=1380, ge=0, le=43200)
    end_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_schedule(self) -> CampaignCreate:
        """Проверить schedule класса CampaignCreate. Некорректные данные или состояние отклоняются
        до побочного эффекта.
        """
        if len(set(self.destination_ids)) != len(self.destination_ids):
            raise ValueError("Список назначений содержит дубликаты")
        if self.secondary_template_id:
            if self.secondary_template_id == self.template_id:
                raise ValueError("Варианты A и B должны использовать разные шаблоны")
            if not 1 <= self.secondary_template_weight <= 99:
                raise ValueError("Вес варианта B должен быть от 1 до 99 процентов")
        elif self.secondary_template_weight != 0:
            raise ValueError("Вес варианта B задаётся только вместе со вторым шаблоном")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("Дни недели должны быть в диапазоне 0..6")
        if self.schedule_type == ScheduleType.WEEKLY and not self.weekdays:
            raise ValueError("Для еженедельного расписания выберите дни недели")
        if self.end_at and self.end_at <= self.schedule_at:
            raise ValueError("Дата окончания должна быть позже старта")
        if self.rollout_mode == RolloutMode.STAGED and self.rollout_batch_size < 1:
            raise ValueError("Размер пакета staged-запуска должен быть положительным")
        return self


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    secondary_template_id: str | None = None
    secondary_template_weight: int | None = Field(default=None, ge=0, le=99)
    schedule_type: ScheduleType | None = None
    schedule_at: datetime | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=80)
    weekdays: list[int] | None = None
    spacing_seconds: int | None = Field(default=None, ge=1, le=86400)
    rollout_mode: RolloutMode | None = None
    rollout_batch_size: int | None = Field(default=None, ge=1, le=500)
    rollout_pause_seconds: int | None = Field(default=None, ge=0, le=86400)
    rollout_require_checkpoint: bool | None = None
    rollout_failure_threshold_percent: int | None = Field(default=None, ge=0, le=100)
    duplicate_guard_minutes: int | None = Field(default=None, ge=0, le=43200)
    end_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)


class CampaignRead(ORMModel):
    id: str
    organization_id: str
    name: str
    connection_id: str
    template_id: str
    secondary_template_id: str | None
    secondary_template_weight: int
    status: CampaignStatus
    schedule_type: ScheduleType
    schedule_at: datetime
    timezone_name: str
    weekdays: list[int]
    spacing_seconds: int
    rollout_mode: RolloutMode
    rollout_batch_size: int
    rollout_pause_seconds: int
    rollout_require_checkpoint: bool
    rollout_failure_threshold_percent: int
    duplicate_guard_minutes: int
    next_run_at: datetime | None
    last_run_at: datetime | None
    end_at: datetime | None
    manual_approval_required: bool
    approved_at: datetime | None
    approved_fingerprint: str | None
    active_approval_request_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    destination_count: int = 0
    destination_ids: list[str] = Field(default_factory=list)
    approval_status: CampaignApprovalStatus | None = None
    approval_required: int = 0
    approval_received: int = 0
    approval_expires_at: datetime | None = None


class CampaignApprovalSubmitRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4000)


class CampaignApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision
    note: str | None = Field(default=None, max_length=4000)


class CampaignApprovalDecisionRead(ORMModel):
    id: str
    organization_id: str
    request_id: str
    user_id: str
    decision: ApprovalDecision
    note: str | None
    created_at: datetime


class CampaignApprovalRead(ORMModel):
    id: str
    organization_id: str
    campaign_id: str
    fingerprint: str
    status: CampaignApprovalStatus
    required_approvals: int
    require_distinct_requester: bool
    requested_by_id: str
    request_note: str | None
    expires_at: datetime
    completed_at: datetime | None
    created_at: datetime
    decisions: list[CampaignApprovalDecisionRead] = Field(default_factory=list)


class CampaignPreviewItem(BaseModel):
    destination_id: str
    destination_title: str
    due_at: datetime
    template_id: str
    template_name: str
    template_variant: str
    body: str
    permission_status: PermissionStatus
    enabled: bool
    batch_number: int = 1
    content_fingerprint: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CampaignPreviewResponse(BaseModel):
    campaign_id: str
    valid: bool
    blockers: list[str]
    warnings: list[str]
    items: list[CampaignPreviewItem]


class CampaignRunRead(ORMModel):
    id: str
    organization_id: str
    campaign_id: str
    scheduled_for: datetime
    status: RunStatus
    total_jobs: int
    sent_jobs: int
    failed_jobs: int
    rollout_mode: RolloutMode
    batch_size: int
    total_batches: int
    active_batch: int
    rollout_pause_seconds: int
    failure_threshold_percent: int
    checkpoint_required: bool
    checkpoint_reason: str | None
    checkpoint_requested_at: datetime | None
    checkpoint_approved_at: datetime | None
    checkpoint_approved_by_id: str | None
    checkpoint_note: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class DeliveryJobRead(ORMModel):
    id: str
    organization_id: str
    run_id: str
    campaign_id: str
    connection_id: str
    destination_id: str
    status: JobStatus
    due_at: datetime
    attempt_count: int
    max_attempts: int
    batch_number: int
    content_fingerprint: str | None
    telegram_message_id: str | None
    error_code: str | None
    error_message: str | None
    safety_decision: dict[str, Any] | None
    review_resolution: DeliveryReviewResolution | None
    reviewed_at: datetime | None
    reviewed_by_id: str | None
    review_note: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class CampaignPreflightItem(BaseModel):
    destination_id: str
    destination_title: str
    due_at: datetime
    batch_number: int
    content_fingerprint: str
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    permission_expires_at: datetime | None = None
    validation_expires_at: datetime | None = None
    blackout_until: datetime | None = None
    duplicate_job_id: str | None = None


class CampaignPreflightRead(ORMModel):
    id: str
    organization_id: str
    campaign_id: str
    fingerprint: str
    status: PreflightStatus
    blockers: list[str]
    warnings: list[str]
    items: list[dict[str, Any]]
    summary: dict[str, Any]
    created_by_id: str
    expires_at: datetime
    created_at: datetime


class ReadinessCheckRead(BaseModel):
    code: str
    title: str
    status: Literal["passed", "warning", "blocked"]
    message: str
    entity_type: str | None = None
    entity_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PilotReadinessRead(ORMModel):
    id: str
    organization_id: str
    campaign_id: str
    connection_id: str
    fingerprint: str
    status: ReadinessStatus
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    summary: dict[str, Any]
    preflight_report_id: str | None
    created_by_id: str
    expires_at: datetime
    created_at: datetime


class PilotAttentionItemRead(BaseModel):
    destination_id: str
    title: str
    issue: str
    severity: Literal["warning", "blocked"]
    due_at: datetime | None = None


class PilotOverviewRead(BaseModel):
    active_connections: int
    destinations_total: int
    destinations_validation_due: int
    permissions_expiring: int
    permissions_expired: int
    active_blackouts: int
    worker_fresh: bool
    attention: list[PilotAttentionItemRead]


class PilotStageAssessmentRequest(BaseModel):
    requested_stage: PilotStage


class PilotStageAdvanceRequest(BaseModel):
    assessment_id: str = Field(min_length=36, max_length=36)
    confirmation: str = Field(min_length=5, max_length=160)
    note: str = Field(min_length=5, max_length=1000)


class PilotStageLowerRequest(BaseModel):
    target_stage: PilotStage
    confirmation: str = Field(min_length=5, max_length=160)
    reason: str = Field(min_length=5, max_length=1000)


class PilotStageAssessmentRead(ORMModel):
    id: str
    organization_id: str
    current_stage: PilotStage
    requested_stage: PilotStage
    status: ReadinessStatus
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    summary: dict[str, Any]
    fingerprint: str
    created_by_id: str
    expires_at: datetime
    created_at: datetime


class PilotStageOverviewRead(BaseModel):
    current_stage: PilotStage
    current_limit: int
    next_stage: PilotStage | None
    next_limit: int | None
    enforcement_required: bool
    latest_assessment: PilotStageAssessmentRead | None = None
    successful_real_canary_at: datetime | None = None
    stage_limits: dict[str, int]


class PilotCanaryCreate(BaseModel):
    destination_id: str = Field(min_length=36, max_length=36)
    confirmation: str = Field(min_length=5, max_length=160)


class PilotCanaryRead(ORMModel):
    id: str
    organization_id: str
    connection_id: str
    destination_id: str
    status: PilotCanaryStatus
    marker: str
    body_sha256: str
    telegram_message_id: str | None
    is_fake: bool
    error_code: str | None
    error_message: str | None
    requested_by_id: str
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime


class SupportBundleCreate(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class SupportBundleRead(ORMModel):
    id: str
    organization_id: str
    status: SupportBundleStatus
    sha256: str | None
    size_bytes: int | None
    sections: list[str]
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    created_by_id: str
    expires_at: datetime
    deleted_at: datetime | None
    error_message: str | None
    created_at: datetime


class CommissioningCheckRead(ORMModel):
    id: str
    organization_id: str
    status: ReadinessStatus
    fingerprint: str
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    summary: dict[str, Any]
    created_by_id: str
    expires_at: datetime
    created_at: datetime


class RecoveryPolicyPatch(BaseModel):
    enabled: bool | None = None
    rpo_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    rto_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    restore_drill_max_age_days: int | None = Field(default=None, ge=1, le=3650)
    minimum_retained_backups: int | None = Field(default=None, ge=1, le=1000)
    require_encrypted_backup: bool | None = None
    require_trusted_signature: bool | None = None
    require_restore_drill: bool | None = None


class RecoveryPolicyRead(ORMModel):
    id: str
    organization_id: str
    enabled: bool
    rpo_hours: int
    rto_minutes: int
    restore_drill_max_age_days: int
    minimum_retained_backups: int
    require_encrypted_backup: bool
    require_trusted_signature: bool
    require_restore_drill: bool
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class RecoveryBackupEvidenceRead(ORMModel):
    id: str
    organization_id: str
    backup_id: str
    status: RecoveryBackupStatus
    product_version: str
    database_kind: str
    storage_backend: str
    artifact_filename: str
    artifact_sha256: str
    artifact_size_bytes: int
    artifact_encrypted: bool
    encryption_algorithm: str | None
    backup_created_at: datetime
    receipt_sha256: str
    manifest_sha256: str
    manifest_summary: dict[str, Any]
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    imported_by_id: str
    verified_at: datetime | None
    expires_at: datetime | None
    error_message: str | None
    created_at: datetime


class RecoveryRestoreDrillRead(ORMModel):
    id: str
    organization_id: str
    backup_evidence_id: str | None
    drill_id: str
    backup_id: str
    backup_artifact_sha256: str
    product_version: str
    mode: RecoveryDrillMode
    status: RecoveryDrillStatus
    started_at: datetime
    completed_at: datetime
    duration_seconds: int
    target_rto_minutes: int
    rto_met: bool
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    evidence_sha256: str
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    executed_host_hash: str | None
    imported_by_id: str
    error_message: str | None
    created_at: datetime


class RecoveryComplianceRead(BaseModel):
    status: ReadinessStatus
    policy: RecoveryPolicyRead
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    latest_backup: RecoveryBackupEvidenceRead | None = None
    latest_drill: RecoveryRestoreDrillRead | None = None


class PilotProgramCreate(BaseModel):
    name: str = Field(min_length=3, max_length=180)
    campaign_id: str
    stage_sizes: list[int] = Field(
        default_factory=lambda: [1, 5, 20, 50, 100], min_length=1, max_length=10
    )
    require_distinct_signoff: bool = True
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("stage_sizes")
    @classmethod
    def validate_stage_sizes(cls, value: list[int]) -> list[int]:
        """Проверить stage sizes класса PilotProgramCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        normalized = [int(item) for item in value]
        allowed = {1, 5, 20, 50, 100}
        if any(item not in allowed for item in normalized):
            raise ValueError("Допустимые размеры live-этапов: 1, 5, 20, 50 и 100 назначений")
        if normalized != sorted(set(normalized)):
            raise ValueError("Размеры этапов должны быть уникальными и возрастать")
        if normalized[0] != 1:
            raise ValueError("Первый live-этап должен содержать одно служебное назначение")
        return normalized


class PilotStageRead(ORMModel):
    id: str
    organization_id: str
    program_id: str
    stage_order: int
    code: str
    title: str
    target_destination_count: int
    requires_live_telegram: bool
    status: PilotStageStatus
    commissioning_check_id: str | None
    readiness_report_id: str | None
    preflight_report_id: str | None
    campaign_run_id: str | None
    evidence: dict[str, Any]
    evidence_sha256: str | None
    started_by_id: str | None
    started_at: datetime | None
    signed_off_by_id: str | None
    signed_off_at: datetime | None
    signoff_note: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class PilotProgramRead(ORMModel):
    id: str
    organization_id: str
    campaign_id: str
    name: str
    status: PilotProgramStatus
    stage_sizes: list[int]
    current_stage_order: int
    require_distinct_signoff: bool
    notes: str | None
    created_by_id: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    stages: list[PilotStageRead] = Field(default_factory=list)


class PilotStageAttachRunRequest(BaseModel):
    campaign_run_id: str
    note: str | None = Field(default=None, max_length=2000)


class PilotStageSignoffRequest(BaseModel):
    decision: Literal["passed", "failed"]
    note: str = Field(min_length=5, max_length=4000)


class ConfigurationBundleExportRequest(BaseModel):
    include_media: bool = False


class ConfigurationBundleRead(ORMModel):
    id: str
    organization_id: str
    kind: ConfigurationBundleKind
    status: ConfigurationBundleStatus
    schema_version: int
    source_product_version: str | None
    filename: str
    sha256: str
    size_bytes: int
    include_media: bool
    conflict_mode: str | None
    manifest: dict[str, Any]
    summary: dict[str, Any]
    signature_status: ArtifactSignatureStatus
    signature_info: dict[str, Any]
    signer_fingerprint: str | None
    error_message: str | None
    created_by_id: str
    applied_by_id: str | None
    applied_at: datetime | None
    created_at: datetime


class ConfigurationBundlePreview(BaseModel):
    valid: bool
    schema_version: int
    source_product_version: str | None
    sha256: str
    size_bytes: int
    summary: dict[str, int]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)
    signature: dict[str, Any] = Field(default_factory=dict)


class ArtifactSigningKeyGenerateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    make_default: bool = True
    trusted_for_import: bool = True
    note: str | None = Field(default=None, max_length=2000)


class ArtifactSigningKeyImportRequest(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    public_key: str = Field(min_length=32, max_length=4000)
    trusted_for_import: bool = True
    note: str | None = Field(default=None, max_length=2000)


class ArtifactSigningKeyRevokeRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


class ArtifactSigningKeyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=160)
    trusted_for_import: bool | None = None
    note: str | None = Field(default=None, max_length=2000)


class ArtifactSigningKeyRead(ORMModel):
    id: str
    organization_id: str
    name: str
    key_id: str
    algorithm: str
    fingerprint: str
    public_key_b64: str
    status: ArtifactSigningKeyStatus
    trusted_for_import: bool
    is_default: bool
    created_by_id: str
    revoked_by_id: str | None
    revoked_at: datetime | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    has_private_key: bool = False


class ArtifactPublicKeyExport(BaseModel):
    key_id: str
    fingerprint: str
    algorithm: str
    public_key_b64: str
    public_key_pem: str


class ArtifactInspectionRead(BaseModel):
    artifact_type: str
    integrity_valid: bool
    sha256: str
    size_bytes: int
    signature: dict[str, Any]
    details: dict[str, Any] = Field(default_factory=dict)


class ConfigurationBundleImportResult(BaseModel):
    bundle: ConfigurationBundleRead
    created: dict[str, int]
    skipped: dict[str, int]
    renamed: dict[str, int]
    warnings: list[str] = Field(default_factory=list)


class DeliveryReviewRequest(BaseModel):
    resolution: DeliveryReviewResolution
    note: str = Field(min_length=5, max_length=2000)
    telegram_message_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_message_id(self) -> DeliveryReviewRequest:
        """Проверить message id класса DeliveryReviewRequest. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if (
            self.resolution == DeliveryReviewResolution.CONFIRMED_SENT
            and not self.telegram_message_id
        ):
            raise ValueError("Для подтверждённой отправки укажите Telegram message ID")
        if self.resolution != DeliveryReviewResolution.CONFIRMED_SENT and self.telegram_message_id:
            raise ValueError("Telegram message ID допустим только при подтверждении отправки")
        return self


class RunCheckpointRequest(BaseModel):
    note: str = Field(min_length=5, max_length=2000)


class RunAbortRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=2000)


class AuditLogRead(ORMModel):
    id: str
    organization_id: str | None
    actor_user_id: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    severity: SafetySeverity
    ip_address: str | None
    request_id: str | None
    details: dict[str, Any]
    sequence: int | None
    prev_hash: str | None
    entry_hash: str | None
    chain_version: int | None
    created_at: datetime


class AuditVerificationRead(BaseModel):
    valid: bool
    organization_id: str | None
    checked_entries: int
    legacy_entries: int
    first_error: str | None
    first_error_entry_id: str | None
    head_sequence: int
    computed_head_hash: str
    stored_head_hash: str | None
    verified_at: datetime


class NotificationRead(ORMModel):
    id: str
    organization_id: str
    event_type: str
    severity: SafetySeverity
    status: NotificationStatus
    title: str
    message: str
    entity_type: str | None
    entity_id: str | None
    dedup_key: str | None
    details: dict[str, Any]
    occurrence_count: int
    last_occurred_at: datetime
    created_at: datetime
    read_at: datetime | None
    read_by_id: str | None
    acknowledged_at: datetime | None
    acknowledged_by_id: str | None


class NotificationCountRead(BaseModel):
    unread: int
    critical_unacknowledged: int


class DashboardSummary(BaseModel):
    connections_total: int
    connections_active: int
    destinations_total: int
    destinations_confirmed: int
    campaigns_active: int
    jobs_pending: int
    jobs_sent_today: int
    jobs_failed_today: int
    worker_online: bool
    worker_last_seen_at: datetime | None
    conversations_open: int = 0
    candidates_ready: int = 0
    outbox_pending: int = 0
    publishing_paused: bool = False
    publishing_pause_reason: str | None = None
    notifications_unread: int = 0
    approvals_pending: int = 0
    recent_safety_events: list[AuditLogRead]


# Business automation and AI


class BusinessConnectionRead(ORMModel):
    id: str
    organization_id: str
    telegram_connection_id: str
    business_connection_id: str
    status: BusinessConnectionStatus
    telegram_user_id: int
    user_chat_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    rights: dict[str, Any]
    is_enabled: bool
    connected_at: datetime | None
    disconnected_at: datetime | None
    last_update_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BusinessConnectionPatch(BaseModel):
    is_enabled: bool | None = None


class WebhookSetupRequest(BaseModel):
    drop_pending_updates: bool = False


class WebhookSetupResponse(BaseModel):
    webhook_url: str
    secret_hint: str
    allowed_updates: list[str]


FlowFieldName = Literal[
    "full_name",
    "city",
    "age",
    "experience",
    "schedule",
    "phone",
    "email",
    "vacancy_key",
]
FlowNodeType = Literal["message", "question", "choice", "handoff", "end"]
FlowValidationKind = Literal["text", "age", "phone", "email"]
FlowCompletionMode = Literal["handoff", "ai"]


class AutomationFlowChoice(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    next_node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def normalize_aliases(self) -> AutomationFlowChoice:
        """Выполнить операцию normalize aliases класса AutomationFlowChoice. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        normalized = []
        seen: set[str] = set()
        for value in [self.label, *self.aliases]:
            item = " ".join(value.strip().lower().split())
            if item and item not in seen:
                normalized.append(item)
                seen.add(item)
        self.aliases = normalized[1:]
        return self


class AutomationFlowNode(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: FlowNodeType
    title: str | None = Field(default=None, max_length=160)
    text: str | None = Field(default=None, max_length=4000)
    field: FlowFieldName | None = None
    validation: FlowValidationKind = "text"
    next_node_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    options: list[AutomationFlowChoice] = Field(default_factory=list, max_length=12)
    skip_if_present: bool = True
    completion_mode: FlowCompletionMode = "handoff"

    @model_validator(mode="after")
    def validate_node(self) -> AutomationFlowNode:
        """Проверить node класса AutomationFlowNode. Некорректные данные или состояние отклоняются
        до побочного эффекта.
        """
        if self.type in {"message", "question", "choice", "handoff", "end"} and not self.text:
            raise ValueError("Для шага нужен текст сообщения")
        if self.type in {"message", "question"} and not self.next_node_id:
            raise ValueError("Для шага нужен следующий узел")
        if self.type in {"question", "choice"} and not self.field:
            raise ValueError("Для вопроса выберите поле анкеты")
        if self.type == "choice":
            if len(self.options) < 2:
                raise ValueError("Для выбора нужны минимум два варианта")
            aliases: set[str] = set()
            for option in self.options:
                for value in [option.label, *option.aliases]:
                    normalized = " ".join(value.strip().lower().split())
                    if normalized in aliases:
                        raise ValueError("Варианты выбора и их синонимы должны быть уникальны")
                    aliases.add(normalized)
        elif self.options:
            raise ValueError("Варианты допустимы только для шага выбора")
        if self.type in {"handoff", "end"} and self.next_node_id:
            raise ValueError("Терминальный шаг не должен иметь продолжение")
        if self.type not in {"question", "choice"} and self.field:
            raise ValueError("Поле анкеты допустимо только для вопроса или выбора")
        return self


class AutomationFlowDefinition(BaseModel):
    version: Literal[1] = 1
    start_node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    nodes: list[AutomationFlowNode] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_graph(self) -> AutomationFlowDefinition:
        """Проверить graph класса AutomationFlowDefinition. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("Идентификаторы шагов должны быть уникальны")
        if self.start_node_id not in by_id:
            raise ValueError("Стартовый шаг не найден")
        references: dict[str, list[str]] = {}
        for node in self.nodes:
            targets: list[str] = []
            if node.next_node_id:
                targets.append(node.next_node_id)
            targets.extend(option.next_node_id for option in node.options)
            unknown = [target for target in targets if target not in by_id]
            if unknown:
                raise ValueError(f"Шаг {node.id} ссылается на неизвестный узел {unknown[0]}")
            references[node.id] = targets

        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node_id: str) -> None:
            """Выполнить операцию walk класса AutomationFlowDefinition. Аргументы интерпретируются
            в контексте модуля, результат возвращается вызывающему коду.
            """
            if node_id in visiting:
                raise ValueError("Сценарий не должен содержать циклы")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target in references[node_id]:
                walk(target)
            visiting.remove(node_id)
            visited.add(node_id)

        walk(self.start_node_id)
        unreachable = sorted(set(by_id) - visited)
        if unreachable:
            raise ValueError(f"Недостижимый шаг: {unreachable[0]}")
        if not any(node.type in {"handoff", "end"} for node in self.nodes):
            raise ValueError("Сценарий должен завершаться шагом end или handoff")
        return self


class AutomationFlowCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    is_active: bool = False
    definition: AutomationFlowDefinition


class AutomationFlowPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    is_active: bool | None = None
    definition: AutomationFlowDefinition | None = None


class AutomationFlowRead(ORMModel):
    id: str
    organization_id: str
    name: str
    description: str | None
    is_active: bool
    revision: int
    definition: AutomationFlowDefinition
    created_at: datetime
    updated_at: datetime


class AutomationPolicyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    telegram_connection_id: str
    ai_provider_config_id: str | None = None
    automation_flow_id: str | None = None
    enabled: bool = False
    timezone_name: str = Field(default="Europe/Helsinki", max_length=80)
    active_hours: dict[str, Any] = Field(default_factory=dict)
    allowed_chat_types: list[str] = Field(default_factory=lambda: ["private"])
    consent_notice: str = Field(min_length=20, max_length=4000)
    fallback_message: str = Field(min_length=1, max_length=4000)
    max_auto_replies_per_day: int = Field(default=20, ge=1, le=200)
    require_consent_before_ai: bool = True
    handoff_keywords: list[str] = Field(default_factory=list, max_length=100)
    stop_words: list[str] = Field(default_factory=list, max_length=100)
    vacancy_detection_rules: dict[str, Any] = Field(default_factory=dict)


class AutomationPolicyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    ai_provider_config_id: str | None = None
    automation_flow_id: str | None = None
    enabled: bool | None = None
    timezone_name: str | None = Field(default=None, max_length=80)
    active_hours: dict[str, Any] | None = None
    allowed_chat_types: list[str] | None = None
    consent_notice: str | None = Field(default=None, min_length=20, max_length=4000)
    fallback_message: str | None = Field(default=None, min_length=1, max_length=4000)
    max_auto_replies_per_day: int | None = Field(default=None, ge=1, le=200)
    require_consent_before_ai: bool | None = None
    handoff_keywords: list[str] | None = None
    stop_words: list[str] | None = None
    vacancy_detection_rules: dict[str, Any] | None = None


class AutomationPolicyRead(ORMModel):
    id: str
    organization_id: str
    name: str
    telegram_connection_id: str
    ai_provider_config_id: str | None
    automation_flow_id: str | None
    enabled: bool
    timezone_name: str
    active_hours: dict[str, Any]
    allowed_chat_types: list[str]
    consent_notice: str
    fallback_message: str
    max_auto_replies_per_day: int
    require_consent_before_ai: bool
    handoff_keywords: list[str]
    stop_words: list[str]
    vacancy_detection_rules: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ConversationRead(ORMModel):
    id: str
    organization_id: str
    business_connection_id: str
    automation_policy_id: str | None
    telegram_chat_id: int
    telegram_user_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    status: ConversationStatus
    consent_status: ConsentStatus
    consent_at: datetime | None
    source_campaign_id: str | None
    vacancy_key: str | None
    assigned_user_id: str | None
    ai_enabled: bool
    flow_node_id: str | None
    flow_state_json: dict[str, Any]
    flow_completed_at: datetime | None
    last_message_at: datetime | None
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    retention_until: datetime | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ConversationPatch(BaseModel):
    status: ConversationStatus | None = None
    consent_status: ConsentStatus | None = None
    vacancy_key: str | None = Field(default=None, max_length=120)
    assigned_user_id: str | None = None
    ai_enabled: bool | None = None


class ConversationReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class ConversationMessageRead(ORMModel):
    id: str
    organization_id: str
    conversation_id: str
    telegram_message_id: int | None
    direction: MessageDirection
    author: MessageAuthor
    body_preview: str
    content_type: str
    reply_to_message_id: int | None
    ai_processed: bool
    raw_metadata: dict[str, Any]
    created_at: datetime


class CandidateRead(ORMModel):
    id: str
    organization_id: str
    conversation_id: str
    full_name: str | None
    city: str | None
    age: int | None
    experience: str | None
    schedule: str | None
    vacancy_key: str | None
    status: CandidateStatus
    summary: str | None
    structured_data: dict[str, Any]
    consent_to_storage: bool
    created_at: datetime
    updated_at: datetime


class CandidatePatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=180)
    city: str | None = Field(default=None, max_length=120)
    age: int | None = Field(default=None, ge=14, le=120)
    experience: str | None = Field(default=None, max_length=4000)
    schedule: str | None = Field(default=None, max_length=250)
    phone: str | None = Field(default=None, max_length=80)
    email: EmailStr | None = None
    vacancy_key: str | None = Field(default=None, max_length=120)
    status: CandidateStatus | None = None
    summary: str | None = Field(default=None, max_length=8000)
    structured_data: dict[str, Any] | None = None
    consent_to_storage: bool | None = None


class KnowledgeArticleCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    content: str = Field(min_length=1, max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    vacancy_key: str | None = Field(default=None, max_length=120)
    is_active: bool = True


class KnowledgeArticlePatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=180)
    content: str | None = Field(default=None, min_length=1, max_length=50000)
    tags: list[str] | None = None
    vacancy_key: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class KnowledgeArticleRead(ORMModel):
    id: str
    organization_id: str
    title: str
    content: str
    tags: list[str]
    vacancy_key: str | None
    is_active: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class AIProviderCreate(BaseModel):
    name: str = Field(min_length=2, max_length=140)
    kind: AIProviderKind = AIProviderKind.RULE_BASED
    base_url: HttpUrl | None = None
    model_name: str | None = Field(default=None, max_length=160)
    api_key: str | None = Field(default=None, max_length=1000)
    enabled: bool = False
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_output_tokens: int = Field(default=500, ge=50, le=8000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    data_region: str | None = Field(default=None, max_length=80)
    system_prompt: str = Field(default="", max_length=12000)
    allowed_models: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_provider(self) -> AIProviderCreate:
        """Проверить provider класса AIProviderCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if self.kind == AIProviderKind.OPENAI_COMPATIBLE:
            if not self.base_url or not self.model_name or not self.api_key:
                raise ValueError(
                    "Для OpenAI-compatible провайдера нужны base_url, model_name и api_key"
                )
        return self


class AIProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=140)
    base_url: HttpUrl | None = None
    model_name: str | None = Field(default=None, max_length=160)
    api_key: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    max_output_tokens: int | None = Field(default=None, ge=50, le=8000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    data_region: str | None = Field(default=None, max_length=80)
    system_prompt: str | None = Field(default=None, max_length=12000)
    allowed_models: list[str] | None = None


class AIProviderRead(ORMModel):
    id: str
    organization_id: str
    name: str
    kind: AIProviderKind
    base_url: str | None
    model_name: str | None
    enabled: bool
    timeout_seconds: int
    max_output_tokens: int
    temperature_milli: int
    data_region: str | None
    system_prompt: str
    allowed_models: list[str]
    created_at: datetime
    updated_at: datetime


class AIProviderTestResponse(BaseModel):
    ok: bool
    provider: str
    model: str | None
    sample: str
    latency_ms: int


class AIInteractionRead(ORMModel):
    id: str
    organization_id: str
    conversation_id: str
    source_message_id: str | None
    provider_config_id: str | None
    provider_kind: str
    model_name: str | None
    request_hash: str
    prompt_preview: str
    response_preview: str
    status: AIInteractionStatus
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    safety_flags: dict[str, Any]
    error_message: str | None
    created_at: datetime


class IntegrationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    kind: IntegrationKind
    config: dict[str, Any] = Field(default_factory=dict)
    event_types: list[str] = Field(default_factory=list, max_length=100)
    is_active: bool = False


class IntegrationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    config: dict[str, Any] | None = None
    event_types: list[str] | None = None
    is_active: bool | None = None


class IntegrationRead(ORMModel):
    id: str
    organization_id: str
    name: str
    kind: IntegrationKind
    event_types: list[str]
    is_active: bool
    last_delivery_at: datetime | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class OutboxEventRead(ORMModel):
    id: str
    organization_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    target_endpoint_ids: list[str] | None
    delivery_state: dict[str, Any]
    status: OutboxStatus
    attempt_count: int
    max_attempts: int
    due_at: datetime
    last_error_message: str | None
    delivered_at: datetime | None
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    scopes: list[str] = Field(default_factory=list, max_length=100)
    expires_at: datetime | None = None


class ApiKeyRead(ORMModel):
    id: str
    organization_id: str
    name: str
    key_prefix: str
    scopes: list[str]
    status: ApiKeyStatus
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(BaseModel):
    api_key: ApiKeyRead
    secret: str


class PrivacyRequestCreate(BaseModel):
    request_type: PrivacyRequestType
    telegram_user_id: int | None = None
    telegram_chat_id: int | None = None
    conversation_id: str | None = None

    @model_validator(mode="after")
    def validate_subject(self) -> PrivacyRequestCreate:
        """Проверить subject класса PrivacyRequestCreate. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if not any([self.telegram_user_id, self.telegram_chat_id, self.conversation_id]):
            raise ValueError("Укажите conversation_id, telegram_user_id или telegram_chat_id")
        return self


class PrivacyRequestRead(ORMModel):
    id: str
    organization_id: str
    request_type: PrivacyRequestType
    status: PrivacyRequestStatus
    telegram_user_id: int | None
    telegram_chat_id: int | None
    conversation_id: str | None
    output_relative_path: str | None
    error_message: str | None
    details: dict[str, Any]
    created_at: datetime
    processed_at: datetime | None


class ConversationMessageBodyResponse(BaseModel):
    id: str
    body: str


class CandidateContactResponse(BaseModel):
    candidate_id: str
    phone: str | None = None
    email: str | None = None


class CandidateExportResponse(BaseModel):
    filename: str
    rows: int


class AnalyticsStatusCount(BaseModel):
    key: str
    label: str
    count: int


class AnalyticsFunnelStage(BaseModel):
    key: str
    label: str
    count: int
    conversion_from_previous: float | None = None


class AnalyticsCampaignRow(BaseModel):
    campaign_id: str
    campaign_name: str
    total: int
    sent: int
    failed: int
    waiting_review: int
    success_rate: float


class AnalyticsTemplateVariantRow(BaseModel):
    campaign_id: str
    campaign_name: str
    template_id: str
    template_name: str
    variant: str
    total: int
    sent: int
    failed: int
    waiting_review: int
    success_rate: float


class AnalyticsOverview(BaseModel):
    date_from: datetime
    date_to: datetime
    timezone_name: str
    delivery_total: int
    delivery_sent: int
    delivery_failed: int
    delivery_waiting_review: int
    delivery_success_rate: float
    conversations_created: int
    conversations_open: int
    conversations_handoff: int
    consent_granted: int
    inbound_messages: int
    candidates_created: int
    candidates_ready: int
    candidates_contacted: int
    ai_interactions: int
    ai_success_rate: float
    ai_average_latency_ms: float | None
    outbox_events: int
    outbox_delivered: int
    delivery_statuses: list[AnalyticsStatusCount]
    conversation_statuses: list[AnalyticsStatusCount]
    candidate_statuses: list[AnalyticsStatusCount]
    funnel: list[AnalyticsFunnelStage]
    campaigns: list[AnalyticsCampaignRow]
    template_variants: list[AnalyticsTemplateVariantRow]


class AnalyticsDailyPoint(BaseModel):
    date: str
    delivery_sent: int = 0
    delivery_failed: int = 0
    inbound_messages: int = 0
    conversations_created: int = 0
    candidates_created: int = 0
    candidates_ready: int = 0


class ExecutionSiteRead(ORMModel):
    id: str
    organization_id: str
    site_key: str
    display_name: str
    enabled: bool
    last_worker_id: str | None
    hostname: str | None
    version: str | None
    environment: str | None
    current_revision: str | None
    expected_revision: str | None
    schema_current: bool | None
    critical_config_sha256: str | None
    release_payload_sha256: str | None
    runtime_fingerprint: str | None
    runtime_checked_at: datetime | None
    last_seen_at: datetime | None
    details: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    online: bool = False
    is_active_site: bool = False


class ExecutionLeaseRead(ORMModel):
    id: str
    organization_id: str
    active_site_key: str
    holder_worker_id: str | None
    epoch: int
    status: ExecutionLeaseStatus
    lease_expires_at: datetime | None
    last_renewed_at: datetime | None
    drain_reason: str | None
    drain_started_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FailoverRequestCreate(BaseModel):
    target_site_key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    reason: str = Field(min_length=10, max_length=2000)


class FailoverApprovalRequest(BaseModel):
    confirmation: str = Field(min_length=10, max_length=200)


class FailoverRequestRead(ORMModel):
    id: str
    organization_id: str
    source_site_key: str
    target_site_key: str
    source_epoch: int
    target_epoch: int | None
    reason: str
    status: FailoverRequestStatus
    requested_by_id: str
    approved_by_id: str | None
    blockers: list[str]
    requested_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeliveryAttemptRead(ORMModel):
    id: str
    organization_id: str
    job_id: str
    attempt_number: int
    worker_id: str
    site_key: str
    fence_epoch: int
    status: DeliveryAttemptStatus
    prepared_at: datetime
    network_started_at: datetime | None
    finished_at: datetime | None
    telegram_message_id: str | None
    error_code: str | None
    error_message: str | None
    details: dict[str, Any]
    created_at: datetime


class ExecutionOverviewRead(BaseModel):
    fencing_required: bool
    current_site_key: str
    primary_site_key: str
    lease: ExecutionLeaseRead
    sites: list[ExecutionSiteRead]
    open_failover: FailoverRequestRead | None
    processing_jobs: int
    uncertain_jobs: int
    active_attempts: int


class ContinuityPolicyPatch(BaseModel):
    """Operator-editable continuity requirements with hard safety bounds."""

    enabled: bool
    require_live_drill: bool
    max_rto_seconds: int = Field(ge=30, le=86400)
    evidence_valid_days: int = Field(ge=1, le=365)
    require_distinct_signoff: bool


class ContinuityPolicyRead(ORMModel):
    """Serialized tenant continuity policy."""

    id: str
    organization_id: str
    enabled: bool
    require_live_drill: bool
    max_rto_seconds: int
    evidence_valid_days: int
    require_distinct_signoff: bool
    created_by_id: str | None
    updated_by_id: str | None
    created_at: datetime
    updated_at: datetime


class ContinuityDrillCreate(BaseModel):
    """Input used to prepare a simulation or live continuity exercise."""

    mode: ContinuityDrillMode
    target_site_key: str = Field(
        min_length=2,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_-]+$",
    )


class ContinuitySignoffRequest(BaseModel):
    """Independent acceptance or rejection of completed drill evidence."""

    accepted: bool
    note: str = Field(min_length=3, max_length=4000)


class ContinuityDrillRead(ORMModel):
    """Full state and evidence summary of one continuity drill."""

    id: str
    organization_id: str
    mode: ContinuityDrillMode
    status: ContinuityDrillStatus
    source_site_key: str
    target_site_key: str
    source_epoch: int
    target_epoch: int | None
    return_epoch: int | None
    failover_request_id: str | None
    failback_request_id: str | None
    policy_snapshot: dict[str, Any]
    policy_sha256: str
    runtime_snapshot: dict[str, Any]
    evidence_payload: dict[str, Any] | None
    evidence_sha256: str | None
    rto_seconds: int | None
    started_at: datetime | None
    target_active_at: datetime | None
    failback_requested_at: datetime | None
    primary_restored_at: datetime | None
    completed_at: datetime | None
    created_by_id: str | None
    signed_off_at: datetime | None
    signed_off_by_id: str | None
    signoff_note: str | None
    failure_reason: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContinuityDrillEventRead(ORMModel):
    """One hash-linked continuity history event."""

    id: str
    organization_id: str
    drill_id: str
    sequence: int
    event_type: ContinuityDrillEventType
    actor_user_id: str | None
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


class ContinuityOverviewRead(BaseModel):
    """Current compliance decision rendered by the web control plane."""

    compliant: bool
    required: bool
    blockers: list[str]
    warnings: list[str]
    policy: ContinuityPolicyRead
    latest_drill: ContinuityDrillRead | None
    runtime_snapshot: dict[str, Any]


class CapacityPolicyRead(ORMModel):
    """Tenant queue, concurrency and Telegram dispatch limits exposed to operators."""

    id: str
    organization_id: str
    enabled: bool
    max_active_jobs: int
    max_ready_jobs: int
    max_processing_jobs: int
    max_active_runs: int
    max_jobs_per_run: int
    max_network_starts_per_minute: int
    max_network_starts_per_hour: int
    max_estimated_drain_seconds: int
    warning_utilization_percent: int
    admission_block_utilization_percent: int
    assessment_ttl_minutes: int
    gate_admission: bool
    gate_dispatch: bool
    created_by_id: str | None
    updated_by_id: str | None
    created_at: datetime
    updated_at: datetime


class CapacityPolicyPatch(BaseModel):
    """Partial update accepted by the capacity control-plane endpoint."""

    enabled: bool | None = None
    max_active_jobs: int | None = Field(default=None, ge=1, le=1_000_000)
    max_ready_jobs: int | None = Field(default=None, ge=1, le=1_000_000)
    max_processing_jobs: int | None = Field(default=None, ge=1, le=100_000)
    max_active_runs: int | None = Field(default=None, ge=1, le=100_000)
    max_jobs_per_run: int | None = Field(default=None, ge=1, le=1_000_000)
    max_network_starts_per_minute: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
    )
    max_network_starts_per_hour: int | None = Field(
        default=None,
        ge=1,
        le=10_000_000,
    )
    max_estimated_drain_seconds: int | None = Field(
        default=None,
        ge=60,
        le=30 * 24 * 3600,
    )
    warning_utilization_percent: int | None = Field(default=None, ge=1, le=99)
    admission_block_utilization_percent: int | None = Field(
        default=None,
        ge=2,
        le=100,
    )
    assessment_ttl_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    gate_admission: bool | None = None
    gate_dispatch: bool | None = None

    @model_validator(mode="after")
    def validate_related_limits(self):
        """Отклонить contradictory limits when all involved values are present in one request."""

        processing = self.max_processing_jobs
        ready = self.max_ready_jobs
        active = self.max_active_jobs
        if processing is not None and ready is not None and processing > ready:
            raise ValueError("max_processing_jobs не может превышать max_ready_jobs")
        if ready is not None and active is not None and ready > active:
            raise ValueError("max_ready_jobs не может превышать max_active_jobs")
        if (
            self.max_jobs_per_run is not None
            and active is not None
            and self.max_jobs_per_run > active
        ):
            raise ValueError("max_jobs_per_run не может превышать max_active_jobs")
        if (
            self.max_network_starts_per_minute is not None
            and self.max_network_starts_per_hour is not None
            and self.max_network_starts_per_hour < self.max_network_starts_per_minute
        ):
            raise ValueError("Часовой сетевой лимит не может быть меньше минутного")
        warning = self.warning_utilization_percent
        blocking = self.admission_block_utilization_percent
        if warning is not None and blocking is not None and warning >= blocking:
            raise ValueError("Порог предупреждения должен быть меньше порога блокировки")
        return self


class CapacityAssessmentRead(ORMModel):
    """Immutable current or projected capacity evidence."""

    id: str
    organization_id: str
    source: CapacityAssessmentSource
    status: ReadinessStatus
    policy_snapshot: dict[str, Any]
    policy_sha256: str
    metrics: dict[str, Any]
    checks: list[dict[str, Any]]
    blockers: list[str]
    warnings: list[str]
    active_jobs: int
    ready_jobs: int
    held_jobs: int
    processing_jobs: int
    waiting_review_jobs: int
    active_runs: int
    network_starts_last_minute: int
    network_starts_last_hour: int
    estimated_drain_seconds: int
    queue_utilization_percent: int
    projected_jobs: int
    projected_ready_jobs: int
    projected_runs: int
    fingerprint: str
    created_by_id: str | None
    expires_at: datetime
    created_at: datetime


class CapacityOverviewRead(BaseModel):
    """Current policy, latest evidence and non-mutating admission decision."""

    policy: CapacityPolicyRead
    latest_assessment: CapacityAssessmentRead | None
    assessment_current: bool
    admission_allowed: bool
    admission_code: str
    admission_message: str
    metrics: dict[str, Any]
