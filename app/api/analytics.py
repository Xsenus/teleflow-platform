from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.enums import (
    AIInteractionStatus,
    CandidateStatus,
    ConsentStatus,
    ConversationStatus,
    JobStatus,
    MessageDirection,
    OutboxStatus,
)
from app.models import (
    AIInteraction,
    Campaign,
    CandidateProfile,
    Conversation,
    ConversationMessage,
    DeliveryJob,
    MessageTemplate,
    Organization,
    OutboxEvent,
    User,
    utcnow,
)
from app.schemas import (
    AnalyticsCampaignRow,
    AnalyticsDailyPoint,
    AnalyticsFunnelStage,
    AnalyticsOverview,
    AnalyticsStatusCount,
    AnalyticsTemplateVariantRow,
)
from app.security import aware_utc

router = APIRouter(prefix="/analytics", tags=["analytics"])

_JOB_LABELS = {
    JobStatus.PENDING.value: "Ожидает",
    JobStatus.PROCESSING.value: "Отправляется",
    JobStatus.RETRY.value: "Повтор",
    JobStatus.WAITING_REVIEW.value: "Ручная проверка",
    JobStatus.SENT.value: "Отправлено",
    JobStatus.FAILED.value: "Ошибка",
    JobStatus.CANCELLED.value: "Отменено",
    JobStatus.SKIPPED.value: "Пропущено",
}
_CONVERSATION_LABELS = {
    ConversationStatus.NEW.value: "Новые",
    ConversationStatus.AWAITING_CONSENT.value: "Ожидают согласия",
    ConversationStatus.AI_ACTIVE.value: "AI активен",
    ConversationStatus.HUMAN_HANDOFF.value: "Переданы оператору",
    ConversationStatus.CLOSED.value: "Закрыты",
    ConversationStatus.BLOCKED.value: "Остановлены",
}
_CANDIDATE_LABELS = {
    CandidateStatus.NEW.value: "Новые",
    CandidateStatus.QUALIFYING.value: "Сбор данных",
    CandidateStatus.READY_FOR_REVIEW.value: "Готовы к проверке",
    CandidateStatus.CONTACTED.value: "Связались",
    CandidateStatus.ARCHIVED.value: "Архив",
}


def _resolve_range(
    db: Session,
    user: User,
    date_from: date | None,
    date_to: date | None,
    timezone_name: str | None,
) -> tuple[datetime, datetime, ZoneInfo, str]:
    """Реализовать внутренний этап resolve range step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    organization = db.get(Organization, user.organization_id)
    zone_name = timezone_name or (organization.timezone_name if organization else "UTC")
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Неизвестный часовой пояс") from exc
    today = utcnow().astimezone(zone).date()
    end_date = date_to or today
    start_date = date_from or (end_date - timedelta(days=29))
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="Начальная дата позже конечной")
    if (end_date - start_date).days > 366:
        raise HTTPException(
            status_code=422, detail="Диапазон аналитики не должен превышать 367 дней"
        )
    start_local = datetime.combine(start_date, time.min, tzinfo=zone)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return (
        start_local.astimezone(UTC),
        end_local.astimezone(UTC),
        zone,
        zone_name,
    )


def _status_rows(counter: Counter[str], labels: dict[str, str]) -> list[AnalyticsStatusCount]:
    """Реализовать внутренний этап status rows step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    keys = list(labels)
    keys.extend(sorted(set(counter) - set(keys)))
    return [
        AnalyticsStatusCount(key=key, label=labels.get(key, key), count=int(counter.get(key, 0)))
        for key in keys
        if counter.get(key, 0)
    ]


def _rate(numerator: int, denominator: int) -> float:
    """Реализовать внутренний этап rate step. Вспомогательная функция сохраняет детерминированность
    и тестируемость процесса.
    """
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


def _funnel_stage(
    key: str,
    label: str,
    count: int,
    previous: int | None,
) -> AnalyticsFunnelStage:
    """Реализовать внутренний этап funnel stage step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    conversion = None if previous is None else _rate(count, previous)
    return AnalyticsFunnelStage(
        key=key,
        label=label,
        count=count,
        conversion_from_previous=conversion,
    )


def _load_analytics(
    db: Session,
    user: User,
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
    zone_name: str,
) -> tuple[AnalyticsOverview, list[AnalyticsDailyPoint]]:
    """Реализовать внутренний этап load analytics step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    org_id = user.organization_id

    job_rows = list(
        db.execute(
            select(
                DeliveryJob.status,
                DeliveryJob.campaign_id,
                DeliveryJob.template_id,
                DeliveryJob.created_at,
            ).where(
                DeliveryJob.organization_id == org_id,
                DeliveryJob.created_at >= start,
                DeliveryJob.created_at < end,
            )
        ).all()
    )
    conversation_rows = list(
        db.execute(
            select(
                Conversation.id,
                Conversation.status,
                Conversation.consent_status,
                Conversation.created_at,
            ).where(
                Conversation.organization_id == org_id,
                Conversation.created_at >= start,
                Conversation.created_at < end,
            )
        ).all()
    )
    candidate_rows = list(
        db.execute(
            select(CandidateProfile.status, CandidateProfile.created_at).where(
                CandidateProfile.organization_id == org_id,
                CandidateProfile.created_at >= start,
                CandidateProfile.created_at < end,
            )
        ).all()
    )
    inbound_rows = list(
        db.scalars(
            select(ConversationMessage.created_at).where(
                ConversationMessage.organization_id == org_id,
                ConversationMessage.direction == MessageDirection.INBOUND,
                ConversationMessage.created_at >= start,
                ConversationMessage.created_at < end,
            )
        ).all()
    )
    ai_rows = list(
        db.execute(
            select(AIInteraction.status, AIInteraction.latency_ms).where(
                AIInteraction.organization_id == org_id,
                AIInteraction.created_at >= start,
                AIInteraction.created_at < end,
            )
        ).all()
    )
    outbox_rows = list(
        db.scalars(
            select(OutboxEvent.status).where(
                OutboxEvent.organization_id == org_id,
                OutboxEvent.created_at >= start,
                OutboxEvent.created_at < end,
            )
        ).all()
    )

    job_counter: Counter[str] = Counter(row.status.value for row in job_rows)
    conversation_counter: Counter[str] = Counter(row.status.value for row in conversation_rows)
    candidate_counter: Counter[str] = Counter(row.status.value for row in candidate_rows)

    campaign_ids = {row.campaign_id for row in job_rows}
    campaign_objects = (
        {
            item.id: item
            for item in db.scalars(
                select(Campaign).where(
                    Campaign.organization_id == org_id,
                    Campaign.id.in_(campaign_ids),
                )
            ).all()
        }
        if campaign_ids
        else {}
    )
    campaigns = {campaign_id: item.name for campaign_id, item in campaign_objects.items()}
    campaign_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in job_rows:
        campaign_counts[row.campaign_id][row.status.value] += 1
    campaign_report: list[AnalyticsCampaignRow] = []
    for campaign_id, counts in campaign_counts.items():
        total = sum(counts.values())
        sent = counts[JobStatus.SENT.value]
        failed = counts[JobStatus.FAILED.value]
        waiting = counts[JobStatus.WAITING_REVIEW.value]
        campaign_report.append(
            AnalyticsCampaignRow(
                campaign_id=campaign_id,
                campaign_name=campaigns.get(campaign_id, "Удалённая кампания"),
                total=total,
                sent=sent,
                failed=failed,
                waiting_review=waiting,
                success_rate=_rate(sent, sent + failed + waiting),
            )
        )
    campaign_report.sort(key=lambda item: (item.total, item.sent), reverse=True)

    template_ids = {row.template_id for row in job_rows}
    template_names = (
        {
            item.id: item.name
            for item in db.scalars(
                select(MessageTemplate).where(
                    MessageTemplate.organization_id == org_id,
                    MessageTemplate.id.in_(template_ids),
                )
            ).all()
        }
        if template_ids
        else {}
    )
    template_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in job_rows:
        template_counts[(row.campaign_id, row.template_id)][row.status.value] += 1
    template_variant_report: list[AnalyticsTemplateVariantRow] = []
    for (campaign_id, template_id), counts in template_counts.items():
        total = sum(counts.values())
        sent = counts[JobStatus.SENT.value]
        failed = counts[JobStatus.FAILED.value]
        waiting = counts[JobStatus.WAITING_REVIEW.value]
        campaign = campaign_objects.get(campaign_id)
        variant = "B" if campaign and campaign.secondary_template_id == template_id else "A"
        template_variant_report.append(
            AnalyticsTemplateVariantRow(
                campaign_id=campaign_id,
                campaign_name=campaigns.get(campaign_id, "Удалённая кампания"),
                template_id=template_id,
                template_name=template_names.get(template_id, "Удалённый шаблон"),
                variant=variant,
                total=total,
                sent=sent,
                failed=failed,
                waiting_review=waiting,
                success_rate=_rate(sent, sent + failed + waiting),
            )
        )
    template_variant_report.sort(
        key=lambda item: (item.campaign_name.lower(), item.variant, -item.total)
    )

    delivery_total = len(job_rows)
    delivery_sent = job_counter[JobStatus.SENT.value]
    delivery_failed = job_counter[JobStatus.FAILED.value]
    delivery_waiting = job_counter[JobStatus.WAITING_REVIEW.value]
    consent_granted = sum(
        1 for row in conversation_rows if row.consent_status == ConsentStatus.GRANTED
    )
    candidates_ready = candidate_counter[CandidateStatus.READY_FOR_REVIEW.value]
    candidates_contacted = candidate_counter[CandidateStatus.CONTACTED.value]
    ai_success = sum(1 for row in ai_rows if row.status == AIInteractionStatus.SUCCEEDED)
    latencies = [row.latency_ms for row in ai_rows if row.latency_ms is not None]
    outbox_delivered = sum(1 for status in outbox_rows if status == OutboxStatus.DELIVERED)

    funnel_counts = [
        len(conversation_rows),
        consent_granted,
        len(candidate_rows),
        candidates_ready + candidates_contacted,
        candidates_contacted,
    ]
    funnel_labels = [
        ("conversations", "Новые диалоги"),
        ("consent", "Получено согласие"),
        ("candidates", "Создана анкета"),
        ("ready", "Готовы к проверке"),
        ("contacted", "Связались"),
    ]
    funnel: list[AnalyticsFunnelStage] = []
    previous: int | None = None
    for (key, label), count in zip(funnel_labels, funnel_counts, strict=True):
        funnel.append(_funnel_stage(key, label, count, previous))
        previous = count

    overview = AnalyticsOverview(
        date_from=start,
        date_to=end,
        timezone_name=zone_name,
        delivery_total=delivery_total,
        delivery_sent=delivery_sent,
        delivery_failed=delivery_failed,
        delivery_waiting_review=delivery_waiting,
        delivery_success_rate=_rate(
            delivery_sent, delivery_sent + delivery_failed + delivery_waiting
        ),
        conversations_created=len(conversation_rows),
        conversations_open=sum(
            conversation_counter[key]
            for key in (
                ConversationStatus.NEW.value,
                ConversationStatus.AWAITING_CONSENT.value,
                ConversationStatus.AI_ACTIVE.value,
                ConversationStatus.HUMAN_HANDOFF.value,
            )
        ),
        conversations_handoff=conversation_counter[ConversationStatus.HUMAN_HANDOFF.value],
        consent_granted=consent_granted,
        inbound_messages=len(inbound_rows),
        candidates_created=len(candidate_rows),
        candidates_ready=candidates_ready,
        candidates_contacted=candidates_contacted,
        ai_interactions=len(ai_rows),
        ai_success_rate=_rate(ai_success, len(ai_rows)),
        ai_average_latency_ms=(round(sum(latencies) / len(latencies), 2) if latencies else None),
        outbox_events=len(outbox_rows),
        outbox_delivered=outbox_delivered,
        delivery_statuses=_status_rows(job_counter, _JOB_LABELS),
        conversation_statuses=_status_rows(conversation_counter, _CONVERSATION_LABELS),
        candidate_statuses=_status_rows(candidate_counter, _CANDIDATE_LABELS),
        funnel=funnel,
        campaigns=campaign_report[:20],
        template_variants=template_variant_report[:40],
    )

    daily: dict[str, AnalyticsDailyPoint] = {}
    cursor = start.astimezone(zone).date()
    final_day = (end - timedelta(microseconds=1)).astimezone(zone).date()
    while cursor <= final_day:
        key = cursor.isoformat()
        daily[key] = AnalyticsDailyPoint(date=key)
        cursor += timedelta(days=1)
    for delivery_row in job_rows:
        key = (
            (aware_utc(delivery_row.created_at) or delivery_row.created_at)
            .astimezone(zone)
            .date()
            .isoformat()
        )
        point = daily.get(key)
        if not point:
            continue
        if delivery_row.status == JobStatus.SENT:
            point.delivery_sent += 1
        elif delivery_row.status in {JobStatus.FAILED, JobStatus.WAITING_REVIEW}:
            point.delivery_failed += 1
    for conversation_row in conversation_rows:
        key = (
            (aware_utc(conversation_row.created_at) or conversation_row.created_at)
            .astimezone(zone)
            .date()
            .isoformat()
        )
        if key in daily:
            daily[key].conversations_created += 1
    for created_at in inbound_rows:
        key = (aware_utc(created_at) or created_at).astimezone(zone).date().isoformat()
        if key in daily:
            daily[key].inbound_messages += 1
    for candidate_row in candidate_rows:
        key = (
            (aware_utc(candidate_row.created_at) or candidate_row.created_at)
            .astimezone(zone)
            .date()
            .isoformat()
        )
        if key in daily:
            daily[key].candidates_created += 1
            if candidate_row.status in {
                CandidateStatus.READY_FOR_REVIEW,
                CandidateStatus.CONTACTED,
            }:
                daily[key].candidates_ready += 1
    return overview, list(daily.values())


@router.get("/overview", response_model=AnalyticsOverview)
def overview(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    timezone_name: str | None = Query(default=None, max_length=80),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyticsOverview:
    """Выполнить операцию overview. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    start, end, zone, zone_name = _resolve_range(db, user, date_from, date_to, timezone_name)
    result, _daily = _load_analytics(db, user, start, end, zone, zone_name)
    return result


@router.get("/timeseries", response_model=list[AnalyticsDailyPoint])
def timeseries(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    timezone_name: str | None = Query(default=None, max_length=80),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnalyticsDailyPoint]:
    """Выполнить операцию timeseries. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    start, end, zone, zone_name = _resolve_range(db, user, date_from, date_to, timezone_name)
    _overview, daily = _load_analytics(db, user, start, end, zone, zone_name)
    return daily


@router.get("/export.csv")
def export_csv(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    timezone_name: str | None = Query(default=None, max_length=80),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Выполнить операцию export csv. Аргументы интерпретируются в контексте модуля, результат
    возвращается вызывающему коду.
    """
    start, end, zone, zone_name = _resolve_range(db, user, date_from, date_to, timezone_name)
    overview_data, daily = _load_analytics(db, user, start, end, zone, zone_name)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "date",
            "delivery_sent",
            "delivery_failed",
            "inbound_messages",
            "conversations_created",
            "candidates_created",
            "candidates_ready",
        ]
    )
    for point in daily:
        writer.writerow(
            [
                point.date,
                point.delivery_sent,
                point.delivery_failed,
                point.inbound_messages,
                point.conversations_created,
                point.candidates_created,
                point.candidates_ready,
            ]
        )
    writer.writerow([])
    writer.writerow(
        [
            "campaign_id",
            "campaign_name",
            "template_id",
            "template_name",
            "variant",
            "total",
            "sent",
            "failed",
            "waiting_review",
            "success_rate",
        ]
    )
    for item in overview_data.template_variants:
        writer.writerow(
            [
                item.campaign_id,
                item.campaign_name,
                item.template_id,
                item.template_name,
                item.variant,
                item.total,
                item.sent,
                item.failed,
                item.waiting_review,
                item.success_rate,
            ]
        )
    filename = (
        f"teleflow-analytics-{start.astimezone(zone).date().isoformat()}-"
        f"{(end - timedelta(microseconds=1)).astimezone(zone).date().isoformat()}.csv"
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-TeleFlow-Delivery-Success-Rate": str(overview_data.delivery_success_rate),
    }
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
