from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ConnectionKind(StrEnum):
    BOT = "bot"
    USER = "user"


class ConnectionStatus(StrEnum):
    DRAFT = "draft"
    AUTH_PENDING = "auth_pending"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    REVOKED = "revoked"


class DestinationKind(StrEnum):
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"
    FORUM_TOPIC = "forum_topic"


class DestinationValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WRITE_FORBIDDEN = "write_forbidden"


class BlackoutScope(StrEnum):
    ORGANIZATION = "organization"
    CONNECTION = "connection"
    DESTINATION = "destination"


class BlackoutKind(StrEnum):
    ONE_TIME = "one_time"
    WEEKLY = "weekly"


class ReadinessStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


class PilotStage(StrEnum):
    LOCAL = "local"
    SERVICE = "service"
    FIVE = "five"
    TWENTY = "twenty"
    FIFTY = "fifty"
    HUNDRED = "hundred"


class PilotCanaryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    BLOCKED = "blocked"


class SupportBundleStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"
    DELETED = "deleted"


class PermissionStatus(StrEnum):
    UNVERIFIED = "unverified"
    CONFIRMED = "confirmed"
    DENIED = "denied"


class ParseMode(StrEnum):
    PLAIN = "plain"
    HTML = "html"
    MARKDOWN = "markdown"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CampaignApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ScheduleType(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"


class RolloutMode(StrEnum):
    STANDARD = "standard"
    STAGED = "staged"


class PreflightStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"
    EXPIRED = "expired"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CHECKPOINT = "awaiting_checkpoint"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    HELD = "held"
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    WAITING_REVIEW = "waiting_review"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class DeliveryReviewResolution(StrEnum):
    CONFIRMED_SENT = "confirmed_sent"
    CONFIRMED_NOT_SENT = "confirmed_not_sent"
    SKIPPED = "skipped"


class ExecutionLeaseStatus(StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    FROZEN = "frozen"


class FailoverRequestStatus(StrEnum):
    REQUESTED = "requested"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ContinuityDrillMode(StrEnum):
    """Type of continuity exercise: non-mutating simulation or real failover."""

    SIMULATION = "simulation"
    LIVE = "live"


class ContinuityDrillStatus(StrEnum):
    """Persisted lifecycle of a continuity exercise."""

    DRAFT = "draft"
    RUNNING = "running"
    AWAITING_FAILBACK = "awaiting_failback"
    AWAITING_SIGNOFF = "awaiting_signoff"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


class ContinuityDrillEventType(StrEnum):
    """Append-only event types protected by the drill hash chain."""

    CREATED = "created"
    SIMULATION_COMPLETED = "simulation_completed"
    FAILOVER_REQUESTED = "failover_requested"
    TARGET_ACTIVE = "target_active"
    FAILBACK_REQUESTED = "failback_requested"
    PRIMARY_RESTORED = "primary_restored"
    SIGNED_OFF = "signed_off"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    FAILED = "failed"


class DeliveryAttemptStatus(StrEnum):
    PREPARED = "prepared"
    NETWORK_STARTED = "network_started"
    SENT = "sent"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    ABANDONED = "abandoned"
    RECONCILED_NOT_SENT = "reconciled_not_sent"
    RECONCILED_SKIPPED = "reconciled_skipped"


class ChallengeStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SafetySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationStatus(StrEnum):
    UNREAD = "unread"
    READ = "read"
    ACKNOWLEDGED = "acknowledged"


class BusinessConnectionStatus(StrEnum):
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    REVOKED = "revoked"


class InboundUpdateStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class ConversationStatus(StrEnum):
    NEW = "new"
    AWAITING_CONSENT = "awaiting_consent"
    AI_ACTIVE = "ai_active"
    HUMAN_HANDOFF = "human_handoff"
    CLOSED = "closed"
    BLOCKED = "blocked"


class ConsentStatus(StrEnum):
    UNKNOWN = "unknown"
    REQUESTED = "requested"
    GRANTED = "granted"
    DECLINED = "declined"
    REVOKED = "revoked"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    SYSTEM = "system"


class MessageAuthor(StrEnum):
    CONTACT = "contact"
    BUSINESS_USER = "business_user"
    ASSISTANT = "assistant"
    OPERATOR = "operator"
    SYSTEM = "system"


class CandidateStatus(StrEnum):
    NEW = "new"
    QUALIFYING = "qualifying"
    READY_FOR_REVIEW = "ready_for_review"
    CONTACTED = "contacted"
    ARCHIVED = "archived"


class AIProviderKind(StrEnum):
    DISABLED = "disabled"
    RULE_BASED = "rule_based"
    OPENAI_COMPATIBLE = "openai_compatible"


class AIInteractionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class IntegrationKind(StrEnum):
    WEBHOOK = "webhook"
    GOOGLE_SHEETS = "google_sheets"
    CSV_EXPORT = "csv_export"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"


class PilotProgramStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PilotStageStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_SIGNOFF = "awaiting_signoff"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ConfigurationBundleKind(StrEnum):
    EXPORT = "export"
    IMPORT = "import"


class ConfigurationBundleStatus(StrEnum):
    READY = "ready"
    IMPORTED = "imported"
    FAILED = "failed"


class ArtifactSigningKeyStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ArtifactSignatureStatus(StrEnum):
    UNSIGNED = "unsigned"
    VALID_TRUSTED = "valid_trusted"
    VALID_UNTRUSTED = "valid_untrusted"
    INVALID = "invalid"
    REVOKED = "revoked"


class RecoveryBackupStatus(StrEnum):
    REGISTERED = "registered"
    VERIFIED = "verified"
    INVALID = "invalid"
    EXPIRED = "expired"
    DELETED = "deleted"


class RecoveryDrillStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class RecoveryDrillMode(StrEnum):
    SQLITE_ISOLATED = "sqlite_isolated"
    POSTGRES_ISOLATED = "postgres_isolated"
    METADATA_ONLY = "metadata_only"


class ChangeRequestType(StrEnum):
    UPGRADE = "upgrade"
    CONFIGURATION = "configuration"
    DATABASE_MIGRATION = "database_migration"
    INFRASTRUCTURE = "infrastructure"


class ChangeRequestStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeploymentVerificationPhase(StrEnum):
    PRE_CHANGE = "pre_change"
    POST_CHANGE = "post_change"


class ReleaseTransparencyEventType(StrEnum):
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class DependencyReportKind(StrEnum):
    INVENTORY_ONLY = "inventory_only"
    VULNERABILITY_SCAN = "vulnerability_scan"


class SLOAssessmentSource(StrEnum):
    MANUAL = "manual"
    WORKER = "worker"


class CapacityAssessmentSource(StrEnum):
    """Origin of an immutable capacity snapshot."""

    MANUAL = "manual"
    WORKER = "worker"
    ADMISSION = "admission"
    DISPATCH = "dispatch"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentSource(StrEnum):
    MANUAL = "manual"
    SLO = "slo"
    DELIVERY = "delivery"
    WORKER = "worker"
    CHANGE = "change"
    SECURITY = "security"


class IncidentEventType(StrEnum):
    CREATED = "created"
    ACKNOWLEDGED = "acknowledged"
    MITIGATION_STARTED = "mitigation_started"
    COMMENTED = "commented"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"
    OWNER_ASSIGNED = "owner_assigned"
    SEVERITY_CHANGED = "severity_changed"


class PrivacyRequestType(StrEnum):
    EXPORT = "export"
    DELETE = "delete"


class PrivacyRequestStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ApiKeyStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
