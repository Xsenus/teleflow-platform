from __future__ import annotations

import hashlib

from app.models import Campaign, MessageTemplate


def variant_bucket(campaign_id: str, destination_id: str) -> int:
    """Вернуть a stable 0..99 bucket without relying on Python's randomized hash()."""
    digest = hashlib.sha256(
        f"teleflow-template-variant:{campaign_id}:{destination_id}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") % 100


def select_campaign_template(campaign: Campaign, destination_id: str) -> MessageTemplate:
    """Выполнить операцию select campaign template. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    secondary = campaign.secondary_template
    weight = campaign.secondary_template_weight or 0
    if secondary is not None and weight > 0:
        if variant_bucket(campaign.id, destination_id) < weight:
            return secondary
    return campaign.template


def campaign_variant_label(campaign: Campaign, template_id: str) -> str:
    """Выполнить операцию campaign variant label. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    if campaign.secondary_template_id and template_id == campaign.secondary_template_id:
        return "B"
    return "A"
