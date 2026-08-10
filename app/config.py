from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TELEFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "TeleFlow Platform"
    version: str = "2.5.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"

    database_url: str = "sqlite:///./data/teleflow.db"
    auto_create_schema: bool | None = None
    redis_url: str = "redis://localhost:6379/0"
    use_redis_locks: bool = False
    require_worker_for_readiness: bool = False
    worker_readiness_max_age_seconds: int = 60
    master_key: str = "development-only-change-me"
    jwt_secret: str = "development-jwt-change-me"
    jwt_issuer: str = "teleflow-platform"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    bootstrap_organization_name: str = "Основная организация"
    bootstrap_organization_slug: str = "main"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "ChangeMe_123456!"
    bootstrap_admin_name: str = "Владелец"

    public_base_url: str = "http://localhost:8080"
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    allowed_hosts: str = "localhost,127.0.0.1"
    ip_allowlist: str = ""
    trust_proxy_headers: bool = False

    access_cookie_name: str = "teleflow_access"
    refresh_cookie_name: str = "teleflow_refresh"
    csrf_cookie_name: str = "teleflow_csrf"
    cookie_secure: bool | None = None
    cookie_samesite: Literal["lax", "strict", "none"] = "strict"

    login_max_failures: int = 5
    login_lock_minutes: int = 15
    require_admin_totp: bool = False
    password_hash_time_cost: int = 3
    password_hash_memory_cost_kib: int = 65536
    password_hash_parallelism: int = 2
    telegram_fake_mode: bool = True
    worker_poll_seconds: float = 2.0
    job_lease_minutes: int = 10
    execution_fencing_required: bool = True
    execution_site_key: str = "primary"
    execution_site_name: str = "Основная площадка"
    execution_primary_site_key: str = "primary"
    execution_lease_ttl_seconds: int = 45
    execution_site_heartbeat_ttl_seconds: int = 90
    execution_require_distinct_failover_approver: bool = True
    continuity_assurance_required: bool = False
    continuity_require_distinct_signoff: bool = True
    continuity_default_require_live_drill: bool = True
    continuity_default_max_rto_seconds: int = Field(default=300, ge=30, le=86400)
    continuity_default_evidence_valid_days: int = Field(default=30, ge=1, le=365)
    continuity_sync_interval_seconds: int = Field(default=15, ge=5, le=3600)
    outbox_poll_seconds: float = 2.0
    outbox_lease_minutes: int = 5
    webhook_timeout_seconds: float = 15.0
    allow_private_integration_urls: bool = False

    storage_path: Path = Path("./storage")
    storage_backend: Literal["local", "s3"] = "local"
    max_media_bytes: int = 20 * 1024 * 1024
    max_export_bytes: int = 50 * 1024 * 1024
    antivirus_mode: Literal["disabled", "clamav"] = "disabled"
    antivirus_fail_closed: bool = True
    clamav_host: str = "127.0.0.1"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 8.0
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_secure: bool = True

    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    sentry_dsn: str | None = None
    otel_enabled: bool = False
    otel_service_name: str = "teleflow-platform"
    otel_exporter_otlp_endpoint: str | None = None

    global_hard_daily_cap: int = 500
    user_hard_min_interval_seconds: int = 60
    bot_hard_min_interval_seconds: int = 1
    user_default_interval_seconds: int = 90
    bot_default_interval_seconds: int = 30
    default_destination_cooldown_minutes: int = 24 * 60
    notification_dedup_minutes: int = 10
    default_require_distinct_campaign_approver: bool = False
    default_high_risk_destination_threshold: int = 20
    default_high_risk_required_approvals: int = 2
    default_approval_request_ttl_hours: int = 24
    preflight_ttl_minutes: int = 60
    permission_expiry_warning_days: int = 14
    destination_validation_ttl_hours: int = 168
    connection_health_ttl_hours: int = 24
    readiness_ttl_minutes: int = 30
    commissioning_ttl_minutes: int = 30
    change_verification_ttl_minutes: int = 30
    require_trusted_release_attestation: bool = False
    require_release_transparency: bool = False
    require_dependency_assessment: bool = False
    dependency_assessment_ttl_hours: int = 24
    dependency_require_vulnerability_scan: bool = False
    dependency_require_trusted_report: bool = False
    dependency_report_max_bytes: int = 5 * 1024 * 1024
    slo_gate_required: bool = False
    slo_auto_evaluate_minutes: int = 15
    slo_assessment_ttl_minutes: int = 30
    slo_default_evaluation_window_hours: int = 24
    slo_default_delivery_success_target_bps: int = 9900
    slo_default_minimum_delivery_sample_size: int = 10
    slo_default_max_queue_age_seconds: int = 900
    slo_default_max_worker_heartbeat_age_seconds: int = 120
    slo_default_max_unresolved_delivery_reviews: int = 0
    slo_default_max_open_critical_incidents: int = 0
    slo_default_error_budget_warning_percent: int = 50
    slo_default_error_budget_critical_percent: int = 100
    capacity_assurance_required: bool = False
    capacity_auto_evaluate_minutes: int = 5
    capacity_assessment_ttl_minutes: int = 15
    capacity_default_max_active_jobs: int = 500
    capacity_default_max_ready_jobs: int = 100
    capacity_default_max_processing_jobs: int = 5
    capacity_default_max_active_runs: int = 20
    capacity_default_max_jobs_per_run: int = 100
    capacity_default_max_network_starts_per_minute: int = 30
    capacity_default_max_network_starts_per_hour: int = 500
    capacity_default_max_estimated_drain_seconds: int = 21600
    capacity_default_warning_utilization_percent: int = 70
    capacity_default_admission_block_utilization_percent: int = 90
    capacity_retry_delay_seconds: int = 15
    pilot_readiness_required: bool = False
    pilot_staged_threshold: int = 5
    pilot_stage_enforcement_required: bool = False
    pilot_stage_assessment_ttl_minutes: int = 30
    pilot_canary_cooldown_hours: int = 24
    pilot_canary_valid_hours: int = 168
    pilot_run_evidence_max_age_days: int = 30
    pilot_max_failure_percent: int = 10
    support_bundle_ttl_hours: int = 24
    artifact_signature_policy: Literal["optional", "require_valid", "require_trusted"] = "optional"

    recovery_default_rpo_hours: int = 24
    recovery_default_rto_minutes: int = 60
    recovery_default_drill_max_age_days: int = 30
    recovery_default_minimum_retained_backups: int = 3
    recovery_evidence_retention_days: int = 365
    recovery_require_encrypted_backup: bool = False
    recovery_require_trusted_signature: bool = False
    recovery_age_recipient: str | None = None
    recovery_age_identity_file: Path | None = None

    inbound_enabled: bool = True
    inbound_update_max_bytes: int = 2 * 1024 * 1024
    inbound_message_max_chars: int = 12000
    default_retention_days: int = 90
    inbound_raw_retention_days: int = 7
    ai_max_context_messages: int = 20
    ai_max_context_chars: int = 16000
    ai_provider_allowlist: str = "rule_based,openai_compatible"
    ai_request_rate_per_minute: int = 30
    pii_preview_length: int = 240
    privacy_export_ttl_hours: int = 24
    webhook_secret_length: int = 48

    @field_validator(
        "access_token_minutes",
        "refresh_token_days",
        "login_max_failures",
        "default_retention_days",
        "ai_max_context_messages",
        "ai_request_rate_per_minute",
        "worker_readiness_max_age_seconds",
        "execution_lease_ttl_seconds",
        "execution_site_heartbeat_ttl_seconds",
        "password_hash_time_cost",
        "password_hash_memory_cost_kib",
        "password_hash_parallelism",
        "clamav_port",
        "notification_dedup_minutes",
        "default_high_risk_destination_threshold",
        "default_high_risk_required_approvals",
        "default_approval_request_ttl_hours",
        "preflight_ttl_minutes",
        "permission_expiry_warning_days",
        "destination_validation_ttl_hours",
        "connection_health_ttl_hours",
        "readiness_ttl_minutes",
        "commissioning_ttl_minutes",
        "change_verification_ttl_minutes",
        "dependency_assessment_ttl_hours",
        "slo_auto_evaluate_minutes",
        "slo_assessment_ttl_minutes",
        "slo_default_evaluation_window_hours",
        "slo_default_minimum_delivery_sample_size",
        "slo_default_max_queue_age_seconds",
        "slo_default_max_worker_heartbeat_age_seconds",
        "slo_default_error_budget_warning_percent",
        "slo_default_error_budget_critical_percent",
        "capacity_auto_evaluate_minutes",
        "capacity_assessment_ttl_minutes",
        "capacity_default_max_active_jobs",
        "capacity_default_max_ready_jobs",
        "capacity_default_max_processing_jobs",
        "capacity_default_max_active_runs",
        "capacity_default_max_jobs_per_run",
        "capacity_default_max_network_starts_per_minute",
        "capacity_default_max_network_starts_per_hour",
        "capacity_default_max_estimated_drain_seconds",
        "capacity_default_warning_utilization_percent",
        "capacity_default_admission_block_utilization_percent",
        "capacity_retry_delay_seconds",
        "pilot_staged_threshold",
        "pilot_stage_assessment_ttl_minutes",
        "pilot_canary_cooldown_hours",
        "pilot_canary_valid_hours",
        "pilot_run_evidence_max_age_days",
        "pilot_max_failure_percent",
        "support_bundle_ttl_hours",
        "recovery_default_rpo_hours",
        "recovery_default_rto_minutes",
        "recovery_default_drill_max_age_days",
        "recovery_default_minimum_retained_backups",
        "recovery_evidence_retention_days",
    )
    @classmethod
    def positive_int(cls, value: int) -> int:
        """Выполнить операцию positive int класса Settings. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        if value <= 0:
            raise ValueError("Значение должно быть больше нуля")
        return value

    @field_validator("execution_site_key", "execution_primary_site_key")
    @classmethod
    def valid_execution_site_key(cls, value: str) -> str:
        """Выполнить операцию valid execution site key класса Settings. Аргументы интерпретируются
        в контексте модуля, результат возвращается вызывающему коду.
        """
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,79}", normalized):
            raise ValueError("Ключ площадки должен содержать 2–80 символов: a-z, 0-9, _ или -")
        return normalized

    @field_validator("slo_default_delivery_success_target_bps")
    @classmethod
    def valid_slo_target(cls, value: int) -> int:
        """Выполнить операцию valid slo target класса Settings. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if not 5000 <= value <= 10000:
            raise ValueError("SLO delivery target должен быть от 5000 до 10000 basis points")
        return value

    @field_validator(
        "slo_default_max_unresolved_delivery_reviews",
        "slo_default_max_open_critical_incidents",
    )
    @classmethod
    def non_negative_int(cls, value: int) -> int:
        """Выполнить операцию non negative int класса Settings. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if value < 0:
            raise ValueError("Значение не может быть отрицательным")
        return value

    @field_validator(
        "max_media_bytes",
        "inbound_update_max_bytes",
        "max_export_bytes",
        "dependency_report_max_bytes",
    )
    @classmethod
    def valid_size(cls, value: int) -> int:
        """Выполнить операцию valid size класса Settings. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        if value < 1024:
            raise ValueError("Лимит размера слишком мал")
        return value

    @model_validator(mode="after")
    def validate_slo_budget_thresholds(self):
        """Проверить slo budget thresholds класса Settings. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if (
            self.slo_default_error_budget_warning_percent
            >= self.slo_default_error_budget_critical_percent
        ):
            raise ValueError("SLO warning error budget должен быть меньше critical threshold")
        if not (
            self.capacity_default_max_processing_jobs
            <= self.capacity_default_max_ready_jobs
            <= self.capacity_default_max_active_jobs
        ):
            raise ValueError("Capacity job limits должны соблюдать processing <= ready <= active")
        if self.capacity_default_max_jobs_per_run > self.capacity_default_max_active_jobs:
            raise ValueError("Capacity max jobs per run не может превышать active jobs")
        if (
            self.capacity_default_max_network_starts_per_hour
            < self.capacity_default_max_network_starts_per_minute
        ):
            raise ValueError("Capacity hourly network limit должен быть не меньше minute limit")
        if not (
            self.capacity_default_warning_utilization_percent
            < self.capacity_default_admission_block_utilization_percent
            <= 100
        ):
            raise ValueError(
                "Capacity warning threshold должен быть меньше admission block threshold"
            )
        return self

    @property
    def is_production(self) -> bool:
        """Выполнить операцию is production класса Settings. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return self.environment == "production"

    @property
    def effective_auto_create_schema(self) -> bool:
        """Выполнить операцию effective auto create schema класса Settings. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        if self.auto_create_schema is not None:
            return self.auto_create_schema
        return not self.is_production

    @property
    def effective_cookie_secure(self) -> bool:
        """Выполнить операцию effective cookie secure класса Settings. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_production

    @property
    def cors_origin_list(self) -> list[str]:
        """Выполнить операцию cors origin list класса Settings. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        """Выполнить операцию allowed host list класса Settings. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def ip_allowlist_values(self) -> list[str]:
        """Выполнить операцию ip allowlist values класса Settings. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        return [item.strip() for item in self.ip_allowlist.split(",") if item.strip()]

    @property
    def ai_provider_allowlist_values(self) -> set[str]:
        """Выполнить операцию ai provider allowlist values класса Settings. Аргументы
        интерпретируются в контексте модуля, результат возвращается вызывающему коду.
        """
        return {item.strip() for item in self.ai_provider_allowlist.split(",") if item.strip()}

    @property
    def media_path(self) -> Path:
        """Выполнить операцию media path класса Settings. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return self.storage_path / "media"

    @property
    def exports_path(self) -> Path:
        """Выполнить операцию exports path класса Settings. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return self.storage_path / "exports"

    @property
    def backups_path(self) -> Path:
        """Выполнить операцию backups path класса Settings. Аргументы интерпретируются в контексте
        модуля, результат возвращается вызывающему коду.
        """
        return self.storage_path / "backups"

    def validate_runtime_security(self) -> None:
        """Проверить runtime security класса Settings. Некорректные данные или состояние
        отклоняются до побочного эффекта.
        """
        if not self.is_production:
            return
        insecure_values = {
            "development-only-change-me",
            "development-jwt-change-me",
            "ChangeMe_123456!",
        }
        if self.debug:
            raise RuntimeError("В production TELEFLOW_DEBUG должен быть false")
        if self.master_key in insecure_values or len(self.master_key) < 32:
            raise RuntimeError("TELEFLOW_MASTER_KEY должен быть случайным и не короче 32 символов")
        if self.jwt_secret in insecure_values or len(self.jwt_secret) < 32:
            raise RuntimeError("TELEFLOW_JWT_SECRET должен быть случайным и не короче 32 символов")
        if (
            self.bootstrap_admin_password in insecure_values
            or len(self.bootstrap_admin_password) < 14
        ):
            raise RuntimeError("Пароль bootstrap-администратора небезопасен")
        if not self.public_base_url.startswith("https://"):
            raise RuntimeError("В production TELEFLOW_PUBLIC_BASE_URL должен использовать HTTPS")
        if self.telegram_fake_mode:
            raise RuntimeError("В production TELEFLOW_TELEGRAM_FAKE_MODE должен быть false")
        if self.database_url.startswith("sqlite"):
            raise RuntimeError("В production требуется PostgreSQL, SQLite запрещён")
        if self.effective_auto_create_schema:
            raise RuntimeError("В production схема БД должна обновляться только через Alembic")
        if self.use_redis_locks and not self.redis_url.strip():
            raise RuntimeError("Для Redis-lock требуется TELEFLOW_REDIS_URL")
        if not self.require_admin_totp:
            raise RuntimeError("В production TELEFLOW_REQUIRE_ADMIN_TOTP должен быть true")
        if self.password_hash_time_cost < 2:
            raise RuntimeError("В production Argon2 time_cost должен быть не меньше 2")
        if self.password_hash_memory_cost_kib < 32768:
            raise RuntimeError("В production Argon2 memory_cost должен быть не меньше 32768 KiB")
        if self.password_hash_parallelism < 1:
            raise RuntimeError("В production Argon2 parallelism должен быть не меньше 1")
        if not self.effective_cookie_secure:
            raise RuntimeError("В production cookie должны иметь флаг Secure")
        if "*" in self.cors_origin_list or "*" in self.allowed_host_list:
            raise RuntimeError("Wildcard CORS/Allowed Hosts запрещён в production")
        if self.cookie_samesite == "none" and not self.effective_cookie_secure:
            raise RuntimeError("SameSite=None допускается только с Secure cookie")
        if self.antivirus_mode == "clamav" and not self.clamav_host.strip():
            raise RuntimeError("Для ClamAV заполните TELEFLOW_CLAMAV_HOST")
        if self.antivirus_mode == "clamav" and self.clamav_timeout_seconds <= 0:
            raise RuntimeError("TELEFLOW_CLAMAV_TIMEOUT_SECONDS должен быть больше нуля")
        if self.storage_backend == "s3":
            required = [self.s3_bucket, self.s3_access_key, self.s3_secret_key]
            if not all(required):
                raise RuntimeError("Для S3-хранилища заполните bucket/access/secret")
        if not self.pilot_readiness_required:
            raise RuntimeError("В production TELEFLOW_PILOT_READINESS_REQUIRED должен быть true")
        if not self.pilot_stage_enforcement_required:
            raise RuntimeError(
                "В production TELEFLOW_PILOT_STAGE_ENFORCEMENT_REQUIRED должен быть true"
            )
        if not self.recovery_require_encrypted_backup:
            raise RuntimeError(
                "В production TELEFLOW_RECOVERY_REQUIRE_ENCRYPTED_BACKUP должен быть true"
            )
        if not self.recovery_require_trusted_signature:
            raise RuntimeError(
                "В production TELEFLOW_RECOVERY_REQUIRE_TRUSTED_SIGNATURE должен быть true"
            )
        if not (self.recovery_age_recipient or "").strip():
            raise RuntimeError(
                "В production заполните TELEFLOW_RECOVERY_AGE_RECIPIENT для встроенного backup"
            )
        if not self.require_trusted_release_attestation:
            raise RuntimeError(
                "В production TELEFLOW_REQUIRE_TRUSTED_RELEASE_ATTESTATION должен быть true"
            )
        if not self.require_release_transparency:
            raise RuntimeError(
                "В production TELEFLOW_REQUIRE_RELEASE_TRANSPARENCY должен быть true"
            )
        if not self.require_dependency_assessment:
            raise RuntimeError(
                "В production TELEFLOW_REQUIRE_DEPENDENCY_ASSESSMENT должен быть true"
            )
        if not self.dependency_require_vulnerability_scan:
            raise RuntimeError(
                "В production TELEFLOW_DEPENDENCY_REQUIRE_VULNERABILITY_SCAN должен быть true"
            )
        if not self.dependency_require_trusted_report:
            raise RuntimeError(
                "В production TELEFLOW_DEPENDENCY_REQUIRE_TRUSTED_REPORT должен быть true"
            )
        if not self.slo_gate_required:
            raise RuntimeError("В production TELEFLOW_SLO_GATE_REQUIRED должен быть true")
        if not self.capacity_assurance_required:
            raise RuntimeError("В production TELEFLOW_CAPACITY_ASSURANCE_REQUIRED должен быть true")
        if not self.execution_fencing_required:
            raise RuntimeError("В production TELEFLOW_EXECUTION_FENCING_REQUIRED должен быть true")
        if not self.execution_site_key.strip() or not self.execution_primary_site_key.strip():
            raise RuntimeError("В production должны быть заданы execution site keys")
        if not self.continuity_assurance_required:
            raise RuntimeError(
                "В production TELEFLOW_CONTINUITY_ASSURANCE_REQUIRED должен быть true"
            )
        if not self.continuity_require_distinct_signoff:
            raise RuntimeError(
                "В production TELEFLOW_CONTINUITY_REQUIRE_DISTINCT_SIGNOFF должен быть true"
            )
        if not self.continuity_default_require_live_drill:
            raise RuntimeError(
                "В production TELEFLOW_CONTINUITY_DEFAULT_REQUIRE_LIVE_DRILL должен быть true"
            )
        if self.artifact_signature_policy != "require_trusted":
            raise RuntimeError(
                "В production TELEFLOW_ARTIFACT_SIGNATURE_POLICY должен быть require_trusted"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Прочитать settings. Значение возвращается без несвязанных изменений состояния."""
    settings = Settings()
    settings.validate_runtime_security()
    return settings
