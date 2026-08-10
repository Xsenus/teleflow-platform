from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)
from sqlalchemy import func
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import Settings
from app.enums import (
    ContinuityDrillMode,
    ContinuityDrillStatus,
    DeliveryAttemptStatus,
    ExecutionLeaseStatus,
    FailoverRequestStatus,
    InboundUpdateStatus,
    IncidentStatus,
    JobStatus,
    OutboxStatus,
    PrivacyRequestStatus,
    ReadinessStatus,
    SafetySeverity,
)
from app.models import (
    CapacityAssessment,
    CapacityPolicy,
    ContinuityDrill,
    ContinuityPolicy,
    DeliveryAttempt,
    DeliveryJob,
    ExecutionLease,
    ExecutionSite,
    FailoverRequest,
    InboundTelegramUpdate,
    Incident,
    Organization,
    OutboxEvent,
    PrivacyRequest,
    SLOAssessment,
    SLOPolicy,
    WorkerHeartbeat,
    utcnow,
)
from app.security import aware_utc

logger = logging.getLogger(__name__)


def _canonical_sha256(payload: object) -> str:
    """Вернуть a stable SHA-256 digest класса JSON-compatible metric snapshots."""

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _continuity_policy_sha256(policy: ContinuityPolicy) -> str:
    """Воспроизвести the continuity policy fingerprint without importing service layers."""

    return _canonical_sha256(
        {
            "enabled": bool(policy.enabled),
            "require_live_drill": bool(policy.require_live_drill),
            "max_rto_seconds": int(policy.max_rto_seconds),
            "evidence_valid_days": int(policy.evidence_valid_days),
            "require_distinct_signoff": bool(policy.require_distinct_signoff),
        }
    )


def _slo_policy_sha256(policy: SLOPolicy) -> str:
    """Вернуть the canonical SLO policy fingerprint without importing service layers. Observability
    is imported by the outbox/notification path, while the SLO service itself emits
    notifications. Keeping this small canonical encoder local prevents an observability ->
    operations -> notifications cycle.
    """
    payload = {
        "enabled": bool(policy.enabled),
        "evaluation_window_hours": int(policy.evaluation_window_hours),
        "delivery_success_target_bps": int(policy.delivery_success_target_bps),
        "minimum_delivery_sample_size": int(policy.minimum_delivery_sample_size),
        "max_queue_age_seconds": int(policy.max_queue_age_seconds),
        "max_worker_heartbeat_age_seconds": int(policy.max_worker_heartbeat_age_seconds),
        "max_unresolved_delivery_reviews": int(policy.max_unresolved_delivery_reviews),
        "max_open_critical_incidents": int(policy.max_open_critical_incidents),
        "error_budget_warning_percent": int(policy.error_budget_warning_percent),
        "error_budget_critical_percent": int(policy.error_budget_critical_percent),
        "assessment_ttl_minutes": int(policy.assessment_ttl_minutes),
        "gate_publishing": bool(policy.gate_publishing),
        "gate_changes": bool(policy.gate_changes),
        "auto_create_incidents": bool(policy.auto_create_incidents),
        "auto_resolve_incidents": bool(policy.auto_resolve_incidents),
        "suppress_incidents_during_maintenance": bool(policy.suppress_incidents_during_maintenance),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _capacity_policy_sha256(policy: CapacityPolicy) -> str:
    """Воспроизвести the capacity policy fingerprint without importing service layers."""

    return _canonical_sha256(
        {
            "enabled": bool(policy.enabled),
            "max_active_jobs": int(policy.max_active_jobs),
            "max_ready_jobs": int(policy.max_ready_jobs),
            "max_processing_jobs": int(policy.max_processing_jobs),
            "max_active_runs": int(policy.max_active_runs),
            "max_jobs_per_run": int(policy.max_jobs_per_run),
            "max_network_starts_per_minute": int(policy.max_network_starts_per_minute),
            "max_network_starts_per_hour": int(policy.max_network_starts_per_hour),
            "max_estimated_drain_seconds": int(policy.max_estimated_drain_seconds),
            "warning_utilization_percent": int(policy.warning_utilization_percent),
            "admission_block_utilization_percent": int(policy.admission_block_utilization_percent),
            "assessment_ttl_minutes": int(policy.assessment_ttl_minutes),
            "gate_admission": bool(policy.gate_admission),
            "gate_dispatch": bool(policy.gate_dispatch),
        }
    )


HTTP_REQUESTS = Counter(
    "teleflow_http_requests_total",
    "HTTP requests",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "teleflow_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
)
WEBHOOK_UPDATES = Counter(
    "teleflow_webhook_updates_total",
    "Telegram webhook updates",
    ["result", "update_type"],
)
WORKER_HEARTBEAT = Gauge(
    "teleflow_worker_last_heartbeat_unixtime",
    "Last successful worker heartbeat unix time",
    ["worker_id"],
)
DELIVERY_RESULTS = Counter(
    "teleflow_delivery_results_total",
    "Telegram delivery results",
    ["result", "connection_kind"],
)
BUILD_INFO = Info("teleflow_build", "TeleFlow build information")
WORKER_DB_HEARTBEAT_AGE = Gauge(
    "teleflow_worker_heartbeat_age_seconds",
    "Age of the newest worker heartbeat stored in the database",
)
QUEUE_DEPTH = Gauge(
    "teleflow_queue_depth",
    "Persisted queue depth by queue and status",
    ["queue", "status"],
)
OUTBOX_RESULTS = Counter(
    "teleflow_outbox_results_total",
    "Integration outbox results",
    ["result", "event_type"],
)
SLO_LATEST_ASSESSMENTS = Gauge(
    "teleflow_slo_latest_assessments",
    "Organizations by latest operational SLO assessment status",
    ["status"],
)
SLO_ERROR_BUDGET_MAX_PERCENT = Gauge(
    "teleflow_slo_error_budget_max_percent",
    "Maximum error budget consumption among latest SLO assessments",
)
SLO_STALE_ASSESSMENTS = Gauge(
    "teleflow_slo_stale_assessments",
    "Organizations whose latest SLO assessment is missing, expired or policy-stale",
)
CAPACITY_LATEST_ASSESSMENTS = Gauge(
    "teleflow_capacity_latest_assessments",
    "Organizations by latest capacity assessment status",
    ["status"],
)
CAPACITY_QUEUE_UTILIZATION_MAX_PERCENT = Gauge(
    "teleflow_capacity_queue_utilization_max_percent",
    "Maximum projected queue utilization among latest capacity assessments",
)
CAPACITY_ESTIMATED_DRAIN_SECONDS_MAX = Gauge(
    "teleflow_capacity_estimated_drain_seconds_max",
    "Maximum estimated queue drain time among latest capacity assessments",
)
CAPACITY_STALE_ASSESSMENTS = Gauge(
    "teleflow_capacity_stale_assessments",
    "Organizations whose latest capacity evidence is missing, expired or policy-stale",
)
CAPACITY_BACKPRESSURE_ORGANIZATIONS = Gauge(
    "teleflow_capacity_backpressure_organizations",
    "Organizations currently blocked by their latest capacity assessment",
)
OPEN_INCIDENTS = Gauge(
    "teleflow_open_incidents",
    "Open operational incidents by severity",
    ["severity"],
)
EXECUTION_LEASES = Gauge(
    "teleflow_execution_leases",
    "Organization execution leases by status",
    ["status"],
)
EXECUTION_STALE_LEASES = Gauge(
    "teleflow_execution_stale_leases",
    "Active execution leases held by workers after their expiry",
)
EXECUTION_OPEN_FAILOVERS = Gauge(
    "teleflow_execution_open_failovers",
    "Failover requests waiting for independent approval",
)
EXECUTION_UNCERTAIN_ATTEMPTS = Gauge(
    "teleflow_execution_uncertain_attempts",
    "Delivery attempts requiring manual reconciliation",
)
EXECUTION_OFFLINE_SITES = Gauge(
    "teleflow_execution_offline_sites",
    "Enabled execution sites whose worker heartbeat is stale",
)
CONTINUITY_DRILLS = Gauge(
    "teleflow_continuity_drills",
    "Continuity drills by mode and lifecycle status",
    ["mode", "status"],
)
CONTINUITY_COMPLIANCE = Gauge(
    "teleflow_continuity_compliance",
    "Organizations by continuity compliance state",
    ["state"],
)
CONTINUITY_LAST_RTO_SECONDS = Gauge(
    "teleflow_continuity_last_rto_seconds",
    "Maximum RTO of the latest accepted continuity drill per organization",
)
CONTINUITY_STALE_EVIDENCE = Gauge(
    "teleflow_continuity_stale_evidence",
    "Organizations with missing, expired, policy-stale or runtime-incompatible continuity evidence",
)
CONTINUITY_RTO_BREACHES = Gauge(
    "teleflow_continuity_rto_breaches",
    "Organizations whose latest accepted continuity drill exceeds the configured RTO",
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Выполнить операцию dispatch класса MetricsMiddleware. Аргументы интерпретируются в
        контексте модуля, результат возвращается вызывающему коду.
        """
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = self._route_name(request)
            HTTP_REQUESTS.labels(request.method, route, "500").inc()
            HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - started)
            raise
        route = self._route_name(request)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - started)
        return response

    @staticmethod
    def _route_name(request: Request) -> str:
        """Реализовать внутренний этап route name step класса MetricsMiddleware. Вспомогательная
        функция сохраняет детерминированность и тестируемость процесса.
        """
        route = request.scope.get("route")
        return getattr(route, "path", request.url.path)


def initialize_observability(app: FastAPI, settings: Settings) -> None:
    """Выполнить операцию initialize observability. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    BUILD_INFO.info({"version": settings.version, "environment": settings.environment})
    if settings.sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.environment,
                release=settings.version,
                integrations=[FastApiIntegration()],
                send_default_pii=False,
                traces_sample_rate=0.1,
            )
        except Exception as exc:
            logger.warning("Sentry initialization failed: %s", exc)
    if settings.otel_enabled:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create({"service.name": settings.otel_service_name})
            )
            exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            FastAPIInstrumentor.instrument_app(app)
        except Exception as exc:
            logger.warning("OpenTelemetry initialization failed: %s", exc)


def _refresh_database_metrics(request: Request) -> None:
    """Реализовать внутренний этап refresh database metrics step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    try:
        with request.app.state.session_factory() as db:
            heartbeat = (
                db.query(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).first()
            )
            if heartbeat:
                last_seen = aware_utc(heartbeat.last_seen_at) or utcnow()
                WORKER_DB_HEARTBEAT_AGE.set(max((utcnow() - last_seen).total_seconds(), 0))
            else:
                WORKER_DB_HEARTBEAT_AGE.set(float("inf"))

            queue_models: dict[str, tuple[Any, type[Any]]] = {
                "delivery": (DeliveryJob, JobStatus),
                "inbound": (InboundTelegramUpdate, InboundUpdateStatus),
                "outbox": (OutboxEvent, OutboxStatus),
                "privacy": (PrivacyRequest, PrivacyRequestStatus),
            }
            for queue, (model, enum_type) in queue_models.items():
                for status in enum_type:
                    count = db.query(model).filter(model.status == status).count()
                    QUEUE_DEPTH.labels(queue, status.value).set(count)

            latest_created = (
                db.query(
                    SLOAssessment.organization_id.label("organization_id"),
                    func.max(SLOAssessment.created_at).label("latest_created_at"),
                )
                .group_by(SLOAssessment.organization_id)
                .subquery()
            )
            latest_ids = (
                db.query(
                    SLOAssessment.organization_id.label("organization_id"),
                    func.max(SLOAssessment.id).label("latest_id"),
                )
                .join(
                    latest_created,
                    (SLOAssessment.organization_id == latest_created.c.organization_id)
                    & (SLOAssessment.created_at == latest_created.c.latest_created_at),
                )
                .group_by(SLOAssessment.organization_id)
                .subquery()
            )
            latest_assessments = (
                db.query(SLOAssessment)
                .join(latest_ids, SLOAssessment.id == latest_ids.c.latest_id)
                .all()
            )
            for status in ReadinessStatus:
                SLO_LATEST_ASSESSMENTS.labels(status.value).set(
                    sum(item.status == status for item in latest_assessments)
                )
            budgets = [
                item.error_budget_consumed_bps / 100
                for item in latest_assessments
                if item.error_budget_consumed_bps is not None
            ]
            SLO_ERROR_BUDGET_MAX_PERCENT.set(max(budgets, default=0))
            policy_by_organization = {
                item.organization_id: item for item in db.query(SLOPolicy).all()
            }
            now = utcnow()
            stale_count = 0
            for item in latest_assessments:
                policy = policy_by_organization.get(item.organization_id)
                expires_at = aware_utc(item.expires_at)
                if (
                    policy is None
                    or expires_at is None
                    or expires_at <= now
                    or item.policy_sha256 != _slo_policy_sha256(policy)
                ):
                    stale_count += 1
            # A policy without any assessment is also stale and cannot satisfy a gate.
            stale_count += len(
                set(policy_by_organization) - {item.organization_id for item in latest_assessments}
            )
            SLO_STALE_ASSESSMENTS.set(stale_count)

            capacity_latest_created = (
                db.query(
                    CapacityAssessment.organization_id.label("organization_id"),
                    func.max(CapacityAssessment.created_at).label("latest_created_at"),
                )
                .group_by(CapacityAssessment.organization_id)
                .subquery()
            )
            capacity_latest_ids = (
                db.query(
                    CapacityAssessment.organization_id.label("organization_id"),
                    func.max(CapacityAssessment.id).label("latest_id"),
                )
                .join(
                    capacity_latest_created,
                    (
                        CapacityAssessment.organization_id
                        == capacity_latest_created.c.organization_id
                    )
                    & (
                        CapacityAssessment.created_at == capacity_latest_created.c.latest_created_at
                    ),
                )
                .group_by(CapacityAssessment.organization_id)
                .subquery()
            )
            latest_capacity = (
                db.query(CapacityAssessment)
                .join(
                    capacity_latest_ids,
                    CapacityAssessment.id == capacity_latest_ids.c.latest_id,
                )
                .all()
            )
            for status in ReadinessStatus:
                CAPACITY_LATEST_ASSESSMENTS.labels(status.value).set(
                    sum(item.status == status for item in latest_capacity)
                )
            CAPACITY_QUEUE_UTILIZATION_MAX_PERCENT.set(
                max(
                    (item.queue_utilization_percent for item in latest_capacity),
                    default=0,
                )
            )
            CAPACITY_ESTIMATED_DRAIN_SECONDS_MAX.set(
                max(
                    (item.estimated_drain_seconds for item in latest_capacity),
                    default=0,
                )
            )
            capacity_policies = {
                item.organization_id: item for item in db.query(CapacityPolicy).all()
            }
            stale_capacity = 0
            blocked_capacity = 0
            for item in latest_capacity:
                policy = capacity_policies.get(item.organization_id)
                expires_at = aware_utc(item.expires_at)
                stale = bool(
                    policy is None
                    or expires_at is None
                    or expires_at <= now
                    or item.policy_sha256 != _capacity_policy_sha256(policy)
                )
                if stale:
                    stale_capacity += 1
                elif item.status == ReadinessStatus.BLOCKED:
                    blocked_capacity += 1
            stale_capacity += len(
                set(capacity_policies) - {item.organization_id for item in latest_capacity}
            )
            CAPACITY_STALE_ASSESSMENTS.set(stale_capacity)
            CAPACITY_BACKPRESSURE_ORGANIZATIONS.set(blocked_capacity)

            for status in ExecutionLeaseStatus:
                EXECUTION_LEASES.labels(status.value).set(
                    db.query(ExecutionLease).filter(ExecutionLease.status == status).count()
                )
            EXECUTION_STALE_LEASES.set(
                db.query(ExecutionLease)
                .filter(
                    ExecutionLease.status == ExecutionLeaseStatus.ACTIVE,
                    ExecutionLease.holder_worker_id.is_not(None),
                    ExecutionLease.lease_expires_at.is_not(None),
                    ExecutionLease.lease_expires_at <= now,
                )
                .count()
            )
            EXECUTION_OPEN_FAILOVERS.set(
                db.query(FailoverRequest)
                .filter(FailoverRequest.status == FailoverRequestStatus.REQUESTED)
                .count()
            )
            EXECUTION_UNCERTAIN_ATTEMPTS.set(
                db.query(DeliveryAttempt)
                .filter(
                    DeliveryAttempt.status.in_(
                        [
                            DeliveryAttemptStatus.NETWORK_STARTED,
                            DeliveryAttemptStatus.UNCERTAIN,
                        ]
                    )
                )
                .count()
            )
            heartbeat_cutoff = (
                now.timestamp() - request.app.state.settings.execution_site_heartbeat_ttl_seconds
            )
            offline_sites = 0
            for site in db.query(ExecutionSite).filter(ExecutionSite.enabled.is_(True)).all():
                seen = aware_utc(site.last_seen_at)
                if seen is None or seen.timestamp() < heartbeat_cutoff:
                    offline_sites += 1
            EXECUTION_OFFLINE_SITES.set(offline_sites)

            for mode in ContinuityDrillMode:
                for status in ContinuityDrillStatus:
                    CONTINUITY_DRILLS.labels(mode.value, status.value).set(
                        db.query(ContinuityDrill)
                        .filter(
                            ContinuityDrill.mode == mode,
                            ContinuityDrill.status == status,
                        )
                        .count()
                    )

            continuity_states = {"compliant": 0, "blocked": 0, "disabled": 0}
            stale_continuity = 0
            rto_breaches = 0
            latest_rtos: list[int] = []
            policies = {item.organization_id: item for item in db.query(ContinuityPolicy).all()}
            sites_by_organization: dict[str, list[ExecutionSite]] = {}
            for site in db.query(ExecutionSite).filter(ExecutionSite.enabled.is_(True)).all():
                sites_by_organization.setdefault(site.organization_id, []).append(site)
            leases_by_organization = {
                item.organization_id: item for item in db.query(ExecutionLease).all()
            }
            for organization in db.query(Organization).all():
                policy = policies.get(organization.id)
                required = bool(
                    request.app.state.settings.continuity_assurance_required
                    or (policy is not None and policy.enabled)
                )
                if not required:
                    continuity_states["disabled"] += 1
                    continue
                blocked = policy is None
                latest = (
                    db.query(ContinuityDrill)
                    .filter(
                        ContinuityDrill.organization_id == organization.id,
                        ContinuityDrill.status == ContinuityDrillStatus.PASSED,
                    )
                    .order_by(
                        ContinuityDrill.signed_off_at.desc(),
                        ContinuityDrill.created_at.desc(),
                    )
                    .first()
                )
                if policy is None or latest is None:
                    blocked = True
                else:
                    expires_at = aware_utc(latest.expires_at)
                    evidence_valid = bool(
                        latest.evidence_payload
                        and latest.evidence_sha256
                        and _canonical_sha256(latest.evidence_payload) == latest.evidence_sha256
                    )
                    blocked = blocked or any(
                        [
                            expires_at is None or expires_at <= now,
                            latest.policy_sha256 != _continuity_policy_sha256(policy),
                            policy.require_live_drill and latest.mode != ContinuityDrillMode.LIVE,
                            latest.rto_seconds is not None
                            and latest.rto_seconds > policy.max_rto_seconds,
                            not evidence_valid,
                        ]
                    )
                    if latest.rto_seconds is not None:
                        latest_rtos.append(int(latest.rto_seconds))
                        if latest.rto_seconds > policy.max_rto_seconds:
                            rto_breaches += 1

                lease = leases_by_organization.get(organization.id)
                org_sites = sites_by_organization.get(organization.id, [])
                active = next(
                    (
                        site
                        for site in org_sites
                        if lease is not None and site.site_key == lease.active_site_key
                    ),
                    None,
                )
                active_seen = aware_utc(active.last_seen_at) if active is not None else None
                active_fresh = bool(
                    active is not None
                    and active_seen is not None
                    and active_seen.timestamp() >= heartbeat_cutoff
                    and active.schema_current is True
                )
                standby_compatible = False
                for site in org_sites:
                    standby_seen = aware_utc(site.last_seen_at)
                    if (
                        site.site_key != (lease.active_site_key if lease else None)
                        and standby_seen is not None
                        and standby_seen.timestamp() >= heartbeat_cutoff
                        and site.schema_current is True
                        and active is not None
                        and site.runtime_fingerprint == active.runtime_fingerprint
                    ):
                        standby_compatible = True
                        break
                blocked = blocked or not active_fresh or not standby_compatible
                state = "blocked" if blocked else "compliant"
                continuity_states[state] += 1
                if blocked:
                    stale_continuity += 1

            for state, count in continuity_states.items():
                CONTINUITY_COMPLIANCE.labels(state).set(count)
            CONTINUITY_LAST_RTO_SECONDS.set(max(latest_rtos, default=0))
            CONTINUITY_STALE_EVIDENCE.set(stale_continuity)
            CONTINUITY_RTO_BREACHES.set(rto_breaches)

            active_incident_statuses = [
                IncidentStatus.OPEN,
                IncidentStatus.ACKNOWLEDGED,
                IncidentStatus.MITIGATING,
            ]
            for severity in SafetySeverity:
                count = (
                    db.query(Incident)
                    .filter(
                        Incident.status.in_(active_incident_statuses),
                        Incident.severity == severity,
                    )
                    .count()
                )
                OPEN_INCIDENTS.labels(severity.value).set(count)
    except Exception as exc:
        logger.warning("Database metrics refresh failed: %s", exc)


def metrics_response(request: Request) -> Response:
    """Выполнить операцию metrics response. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    _refresh_database_metrics(request)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
