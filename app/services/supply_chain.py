from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ArtifactSignatureStatus,
    ChangeRequestType,
    DependencyReportKind,
    ReadinessStatus,
    ReleaseTransparencyEventType,
)
from app.models import (
    ChangeRequest,
    DependencyPolicy,
    Organization,
    ReleaseAttestation,
    ReleaseDependencyAssessment,
    ReleaseTransparencyEvent,
    User,
    utcnow,
)
from app.services.artifact_signing import (
    ArtifactSigningError,
    get_default_signing_key,
    sign_bytes,
    verify_signature,
)
from app.services.crypto import SecretCipher
from app.services.release_trust import canonical_release_payload, verify_release_attestation

DEPENDENCY_REPORT_PURPOSE = "teleflow-dependency-assessment-v1"
DEPENDENCY_REPORT_SCHEMA = 1
TRANSPARENCY_ENTRY_PURPOSE = "teleflow-release-transparency-entry-v1"
TRANSPARENCY_CHAIN_DOMAIN = b"TeleFlow-Release-Transparency-v1\n"
ZERO_HASH = "0" * 64
_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "unknown"}


class SupplyChainError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    """Преобразовать data for canonical json using the project's canonical representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    """Вычислить sha256 json. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _aware(value: datetime) -> datetime:
    """Реализовать внутренний этап aware step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    """Реализовать внутренний этап parse timestamp step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    if not isinstance(value, str) or not value.strip():
        raise SupplyChainError(f"Поле {field} должно содержать ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SupplyChainError(f"Поле {field} содержит некорректное время") from exc
    if parsed.tzinfo is None:
        raise SupplyChainError(f"Поле {field} должно содержать часовой пояс")
    return parsed.astimezone(UTC)


def _lock_organization(db: Session, organization_id: str) -> Organization:
    """Реализовать внутренний этап lock organization step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise SupplyChainError("Организация не найдена")
    return organization


def dependency_policy_payload(policy: DependencyPolicy) -> dict[str, Any]:
    """Выполнить операцию dependency policy payload. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    return {
        "schema_version": 1,
        "require_exact_pins": bool(policy.require_exact_pins),
        "allow_prerelease": bool(policy.allow_prerelease),
        "require_vulnerability_scan": bool(policy.require_vulnerability_scan),
        "require_trusted_report": bool(policy.require_trusted_report),
        "max_critical": int(policy.max_critical),
        "max_high": int(policy.max_high),
        "max_medium": int(policy.max_medium),
        "report_ttl_hours": int(policy.report_ttl_hours),
        "denied_packages": sorted(
            {
                canonicalize_name(str(item))
                for item in (policy.denied_packages or [])
                if str(item).strip()
            }
        ),
    }


def get_or_create_dependency_policy(
    db: Session,
    *,
    organization_id: str,
    settings: Settings,
) -> DependencyPolicy:
    """Прочитать or create dependency policy. Значение возвращается без несвязанных изменений
    состояния.
    """
    policy = db.scalar(
        select(DependencyPolicy).where(DependencyPolicy.organization_id == organization_id)
    )
    if policy is not None:
        return policy
    # Serialize first-time policy creation per tenant. Without this lock two
    # concurrent first reads could race on the unique organization constraint.
    _lock_organization(db, organization_id)
    policy = db.scalar(
        select(DependencyPolicy).where(DependencyPolicy.organization_id == organization_id)
    )
    if policy is not None:
        return policy
    policy = DependencyPolicy(
        organization_id=organization_id,
        require_exact_pins=False,
        allow_prerelease=False,
        require_vulnerability_scan=settings.dependency_require_vulnerability_scan,
        require_trusted_report=settings.dependency_require_trusted_report,
        max_critical=0,
        max_high=0,
        max_medium=10,
        report_ttl_hours=settings.dependency_assessment_ttl_hours,
        denied_packages=[],
    )
    db.add(policy)
    db.flush()
    return policy


def update_dependency_policy(
    db: Session,
    *,
    policy: DependencyPolicy,
    user: User,
    settings: Settings,
    values: dict[str, Any],
) -> DependencyPolicy:
    """Обновить dependency policy. Переход применяется только после проверки его предусловий."""
    requested_ttl = values.get("report_ttl_hours")
    if requested_ttl is not None and requested_ttl > settings.dependency_assessment_ttl_hours:
        raise SupplyChainError(
            "TTL dependency report не может превышать серверный лимит "
            f"{settings.dependency_assessment_ttl_hours} ч."
        )
    if settings.is_production:
        if values.get("require_vulnerability_scan") is False:
            raise SupplyChainError("В production нельзя отключить vulnerability scan")
        if values.get("require_trusted_report") is False:
            raise SupplyChainError("В production нельзя отключить доверенную подпись отчёта")
        if values.get("max_critical", policy.max_critical) > 0:
            raise SupplyChainError("В production critical-уязвимости должны блокировать релиз")
        if values.get("max_high", policy.max_high) > 0:
            raise SupplyChainError("В production high-уязвимости должны блокировать релиз")
    for field in (
        "require_exact_pins",
        "allow_prerelease",
        "require_vulnerability_scan",
        "require_trusted_report",
        "max_critical",
        "max_high",
        "max_medium",
        "report_ttl_hours",
    ):
        if field in values and values[field] is not None:
            setattr(policy, field, values[field])
    if "denied_packages" in values and values["denied_packages"] is not None:
        normalized_values: set[str] = set()
        for item in values["denied_packages"]:
            raw = str(item).strip()
            if not raw:
                continue
            if len(raw) > 160:
                raise SupplyChainError("Имя запрещённого пакета не должно превышать 160 символов")
            normalized_values.add(canonicalize_name(raw))
        normalized = sorted(normalized_values)
        policy.denied_packages = normalized[:500]
    policy.updated_by_id = user.id
    db.flush()
    return policy


def _requirement_inventory(
    attestation: ReleaseAttestation,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Реализовать внутренний этап requirement inventory step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    components: list[dict[str, Any]] = []
    unpinned: list[str] = []
    prerelease: list[str] = []
    raw_dependencies = attestation.payload.get("dependencies") or []
    if not isinstance(raw_dependencies, list):
        raise SupplyChainError("Release attestation содержит некорректный dependency inventory")
    seen: set[tuple[str, str]] = set()
    for raw in raw_dependencies:
        text = str(raw).strip()
        if not text:
            continue
        try:
            requirement = Requirement(text)
        except InvalidRequirement as exc:
            raise SupplyChainError(f"Некорректная зависимость в attestation: {text}") from exc
        name = canonicalize_name(requirement.name)
        exact_version: str | None = None
        specifiers = list(requirement.specifier)
        if (
            len(specifiers) == 1
            and specifiers[0].operator in {"==", "==="}
            and "*" not in specifiers[0].version
        ):
            exact_version = specifiers[0].version
        else:
            unpinned.append(text)
        if exact_version:
            try:
                if Version(exact_version).is_prerelease:
                    prerelease.append(text)
            except InvalidVersion:
                prerelease.append(text)
        key = (name, exact_version or str(requirement.specifier) or "unresolved")
        if key in seen:
            continue
        seen.add(key)
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": exact_version or "unresolved",
            "properties": [
                {"name": "teleflow:requirement", "value": text},
                {"name": "teleflow:direct", "value": "true"},
            ],
        }
        if exact_version:
            component["purl"] = f"pkg:pypi/{name}@{exact_version}"
        components.append(component)
    components.sort(key=lambda item: (item["name"], item["version"]))
    return components, sorted(unpinned), sorted(prerelease)


def build_release_sbom(attestation: ReleaseAttestation) -> dict[str, Any]:
    """Создать release sbom. Перед сохранением или возвратом нового значения проверяются связанные
    инварианты.
    """
    components, _, _ = _requirement_inventory(attestation)
    serial = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"teleflow:{attestation.organization_id}:{attestation.payload_sha256}",
    )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "teleflow-platform",
                "version": attestation.version,
            },
            "properties": [
                {
                    "name": "teleflow:release-attestation-sha256",
                    "value": attestation.payload_sha256,
                },
                {
                    "name": "teleflow:inventory-scope",
                    "value": "declared-direct-runtime-dependencies",
                },
            ],
        },
        "components": components,
    }


def build_inventory_report(
    attestation: ReleaseAttestation,
    *,
    scanner_name: str = "teleflow-inventory",
    scanner_version: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Создать a deterministic, scanner-neutral inventory-only report. This report proves which
    release attestation was inspected. It does not claim that a vulnerability database was
    queried and therefore cannot satisfy a policy that requires ``vulnerability_scan``.
    """

    timestamp = generated_at or utcnow()
    sbom = build_release_sbom(attestation)
    return {
        "schema_version": DEPENDENCY_REPORT_SCHEMA,
        "kind": DependencyReportKind.INVENTORY_ONLY.value,
        "release_payload_sha256": attestation.payload_sha256,
        "sbom_sha256": sha256_json(sbom),
        "generated_at": _aware(timestamp).isoformat(),
        "scanner": {
            "name": scanner_name.strip()[:120],
            "version": (scanner_version or "").strip()[:80] or None,
        },
        "findings": [],
    }


def normalize_dependency_report(
    report: dict[str, Any],
    *,
    attestation: ReleaseAttestation,
) -> dict[str, Any]:
    """Выполнить операцию normalize dependency report. Аргументы интерпретируются в контексте
    модуля, результат возвращается вызывающему коду.
    """
    if not isinstance(report, dict):
        raise SupplyChainError("Dependency report должен быть JSON-объектом")
    allowed_top = {
        "schema_version",
        "kind",
        "release_payload_sha256",
        "sbom_sha256",
        "generated_at",
        "scanner",
        "findings",
    }
    unexpected = set(report) - allowed_top
    if unexpected:
        raise SupplyChainError(
            "Dependency report содержит неподдерживаемые поля: " + ", ".join(sorted(unexpected))
        )
    if int(report.get("schema_version") or 0) != DEPENDENCY_REPORT_SCHEMA:
        raise SupplyChainError("Неподдерживаемая версия dependency report")
    try:
        kind = DependencyReportKind(str(report.get("kind") or ""))
    except ValueError as exc:
        raise SupplyChainError("Некорректный тип dependency report") from exc
    release_digest = str(report.get("release_payload_sha256") or "")
    if not hmac.compare_digest(release_digest, attestation.payload_sha256):
        raise SupplyChainError("Dependency report относится к другой release attestation")
    expected_sbom_sha = sha256_json(build_release_sbom(attestation))
    supplied_sbom_sha = str(report.get("sbom_sha256") or "").strip().lower()
    if len(supplied_sbom_sha) != 64 or any(
        char not in "0123456789abcdef" for char in supplied_sbom_sha
    ):
        raise SupplyChainError("Dependency report не содержит корректный SHA-256 SBOM")
    if not hmac.compare_digest(supplied_sbom_sha, expected_sbom_sha):
        raise SupplyChainError("Dependency report относится к другому SBOM")
    generated_at = _parse_timestamp(report.get("generated_at"), field="generated_at")
    if generated_at > utcnow() + timedelta(minutes=5):
        raise SupplyChainError("Dependency report датирован будущим временем")
    scanner = report.get("scanner")
    if not isinstance(scanner, dict):
        raise SupplyChainError("Поле scanner должно быть объектом")
    scanner_name = str(scanner.get("name") or "").strip()
    scanner_version = str(scanner.get("version") or "").strip()
    if len(scanner_name) < 2 or len(scanner_name) > 120:
        raise SupplyChainError("Некорректное название vulnerability scanner")
    if len(scanner_version) > 80:
        raise SupplyChainError("Версия vulnerability scanner слишком длинная")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise SupplyChainError("Поле findings должно быть массивом")
    if len(findings) > 5000:
        raise SupplyChainError("Dependency report содержит слишком много findings")
    normalized_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            raise SupplyChainError(f"Finding #{index} должен быть объектом")
        allowed = {
            "id",
            "package",
            "installed_version",
            "severity",
            "fixed_versions",
            "aliases",
            "url",
        }
        extra = set(finding) - allowed
        if extra:
            raise SupplyChainError(
                f"Finding #{index} содержит неподдерживаемые поля: " + ", ".join(sorted(extra))
            )
        advisory_id = str(finding.get("id") or "").strip()
        package = canonicalize_name(str(finding.get("package") or "").strip())
        installed_version = str(finding.get("installed_version") or "").strip()
        severity = str(finding.get("severity") or "unknown").strip().lower()
        if not advisory_id or len(advisory_id) > 160:
            raise SupplyChainError(f"Finding #{index}: некорректный advisory id")
        if not package or len(package) > 160:
            raise SupplyChainError(f"Finding #{index}: некорректное имя пакета")
        if len(installed_version) > 120:
            raise SupplyChainError(f"Finding #{index}: версия пакета слишком длинная")
        if severity not in _ALLOWED_SEVERITIES:
            raise SupplyChainError(f"Finding #{index}: неизвестный severity {severity}")
        fixed_versions = finding.get("fixed_versions") or []
        aliases = finding.get("aliases") or []
        if not isinstance(fixed_versions, list) or not isinstance(aliases, list):
            raise SupplyChainError(
                f"Finding #{index}: fixed_versions/aliases должны быть массивами"
            )
        normalized_fixed_versions = sorted(
            {str(item).strip()[:160] for item in fixed_versions if str(item).strip()}
        )[:100]
        normalized_aliases = sorted(
            {str(item).strip()[:160] for item in aliases if str(item).strip()}
        )[:100]
        normalized_findings.append(
            {
                "id": advisory_id,
                "package": package,
                "installed_version": installed_version,
                "severity": severity,
                "fixed_versions": normalized_fixed_versions,
                "aliases": normalized_aliases,
                "url": (str(finding.get("url") or "").strip()[:1000] or None),
            }
        )
    normalized_findings.sort(key=lambda item: (item["severity"], item["package"], item["id"]))
    return {
        "schema_version": DEPENDENCY_REPORT_SCHEMA,
        "kind": kind.value,
        "release_payload_sha256": attestation.payload_sha256,
        "sbom_sha256": expected_sbom_sha,
        "generated_at": generated_at.isoformat(),
        "scanner": {"name": scanner_name, "version": scanner_version or None},
        "findings": normalized_findings,
    }


def _evaluate_report(
    *,
    attestation: ReleaseAttestation,
    report: dict[str, Any],
    policy: DependencyPolicy,
    signature_status: ArtifactSignatureStatus,
    attestation_trusted: bool,
    max_ttl_hours: int,
) -> dict[str, Any]:
    """Реализовать внутренний этап evaluate report step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    components, unpinned, prerelease = _requirement_inventory(attestation)
    direct_names = {item["name"] for item in components}
    policy_payload = dependency_policy_payload(policy)
    denied = set(policy_payload["denied_packages"])
    findings = report["findings"]
    counts = {severity: 0 for severity in _ALLOWED_SEVERITIES}
    finding_packages: set[str] = set()
    for finding in findings:
        counts[finding["severity"]] += 1
        finding_packages.add(finding["package"])
    denied_hits = sorted((direct_names | finding_packages) & denied)
    blockers: list[str] = []
    warnings: list[str] = []
    if not attestation_trusted:
        blockers.append("Release attestation не подтверждён доверенным активным ключом.")
    if policy.require_trusted_report and signature_status != ArtifactSignatureStatus.VALID_TRUSTED:
        blockers.append(
            "Dependency report должен быть подписан доверенным активным Ed25519-ключом."
        )
    elif signature_status == ArtifactSignatureStatus.INVALID:
        blockers.append("Подпись dependency report недействительна.")
    elif signature_status == ArtifactSignatureStatus.REVOKED:
        blockers.append("Dependency report подписан отозванным ключом.")
    elif signature_status == ArtifactSignatureStatus.UNSIGNED:
        warnings.append("Dependency report не подписан.")
    elif signature_status == ArtifactSignatureStatus.VALID_UNTRUSTED:
        warnings.append("Подпись dependency report корректна, но ключ не отмечен доверенным.")
    if (
        policy.require_vulnerability_scan
        and report["kind"] != DependencyReportKind.VULNERABILITY_SCAN.value
    ):
        blockers.append(
            "Политика требует результат vulnerability scan, inventory-only отчёт недостаточен."
        )
    if policy.require_exact_pins and unpinned:
        blockers.append(f"Обнаружено непинованных runtime-зависимостей: {len(unpinned)}.")
    elif unpinned:
        warnings.append(f"Runtime dependency inventory содержит диапазоны версий: {len(unpinned)}.")
    if not policy.allow_prerelease and prerelease:
        blockers.append(f"Обнаружены prerelease runtime-зависимости: {len(prerelease)}.")
    if denied_hits:
        blockers.append("Обнаружены запрещённые пакеты: " + ", ".join(denied_hits[:20]))
    if counts["critical"] > policy.max_critical:
        blockers.append(
            f"Critical findings: {counts['critical']}, допустимо: {policy.max_critical}."
        )
    if counts["high"] > policy.max_high:
        blockers.append(f"High findings: {counts['high']}, допустимо: {policy.max_high}.")
    if counts["medium"] > policy.max_medium:
        blockers.append(f"Medium findings: {counts['medium']}, допустимо: {policy.max_medium}.")
    if counts["unknown"]:
        warnings.append(f"Findings с неизвестной критичностью: {counts['unknown']}.")
    undeclared = sorted(finding_packages - direct_names)
    if undeclared:
        warnings.append(
            "Scanner сообщил о транзитивных/необъявленных пакетах: " + ", ".join(undeclared[:20])
        )
    generated_at = _parse_timestamp(report["generated_at"], field="generated_at")
    effective_ttl_hours = min(int(policy.report_ttl_hours), int(max_ttl_hours))
    expires_at = generated_at + timedelta(hours=effective_ttl_hours)
    if expires_at <= utcnow():
        blockers.append("Dependency report просрочен относительно текущей политики.")
    status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    return {
        "counts": counts,
        "unpinned": unpinned,
        "prerelease": prerelease,
        "denied_hits": denied_hits,
        "blockers": blockers,
        "warnings": warnings,
        "status": status,
        "expires_at": expires_at,
        "policy_payload": policy_payload,
        "effective_ttl_hours": effective_ttl_hours,
    }


def create_dependency_assessment(
    db: Session,
    *,
    organization_id: str,
    attestation: ReleaseAttestation,
    user: User,
    settings: Settings,
    cipher: SecretCipher,
    report: dict[str, Any],
    signature: dict[str, Any] | None,
    sign_with_default_key: bool,
) -> ReleaseDependencyAssessment:
    """Создать dependency assessment. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    if attestation.organization_id != organization_id:
        raise SupplyChainError("Release attestation относится к другой организации")
    prepared_report = dict(report)
    if not prepared_report.get("sbom_sha256") and sign_with_default_key and not signature:
        # Local UI/CLI reports are bound to the server-generated CycloneDX
        # document before signing. Imported signatures must already contain
        # this digest; otherwise adding it would invalidate the external
        # signature and hide an incomplete report.
        prepared_report["sbom_sha256"] = sha256_json(build_release_sbom(attestation))
    report = prepared_report
    try:
        raw_size = len(canonical_json(report))
    except (TypeError, ValueError) as exc:
        raise SupplyChainError("Dependency report должен содержать корректный JSON") from exc
    if raw_size > settings.dependency_report_max_bytes:
        raise SupplyChainError(
            f"Dependency report превышает лимит {settings.dependency_report_max_bytes} байт"
        )
    normalized = normalize_dependency_report(report, attestation=attestation)
    data = canonical_json(normalized)
    if len(data) > settings.dependency_report_max_bytes:
        raise SupplyChainError("Dependency report превышает допустимый размер")
    report_sha = hashlib.sha256(data).hexdigest()
    envelope = signature or {}
    if signature and sign_with_default_key:
        raise SupplyChainError(
            "Нельзя одновременно импортировать подпись и подписывать отчёт локально"
        )
    if sign_with_default_key:
        key = get_default_signing_key(db, organization_id=organization_id, require_private=True)
        if key is None:
            raise SupplyChainError("Сначала создайте основной Ed25519-ключ подписи")
        try:
            envelope = sign_bytes(
                data,
                key=key,
                cipher=cipher,
                purpose=DEPENDENCY_REPORT_PURPOSE,
            )
        except ArtifactSigningError as exc:
            raise SupplyChainError(str(exc)) from exc
    verification = verify_signature(
        db,
        organization_id=organization_id,
        data=data,
        envelope=envelope,
        expected_purpose=DEPENDENCY_REPORT_PURPOSE,
    )
    if signature and not verification.cryptographically_valid:
        raise SupplyChainError(verification.error or "Недействительная подпись dependency report")
    policy = get_or_create_dependency_policy(db, organization_id=organization_id, settings=settings)
    attestation_trusted = verify_release_attestation(db, attestation)
    evaluation = _evaluate_report(
        attestation=attestation,
        report=normalized,
        policy=policy,
        signature_status=verification.status,
        attestation_trusted=attestation_trusted,
        max_ttl_hours=settings.dependency_assessment_ttl_hours,
    )
    policy_payload = evaluation["policy_payload"]
    policy_sha = sha256_json(policy_payload)
    existing = db.scalar(
        select(ReleaseDependencyAssessment).where(
            ReleaseDependencyAssessment.organization_id == organization_id,
            ReleaseDependencyAssessment.release_attestation_id == attestation.id,
            ReleaseDependencyAssessment.report_sha256 == report_sha,
            ReleaseDependencyAssessment.policy_sha256 == policy_sha,
        )
    )
    if existing is not None:
        return existing
    sbom = build_release_sbom(attestation)
    counts = evaluation["counts"]
    item = ReleaseDependencyAssessment(
        organization_id=organization_id,
        release_attestation_id=attestation.id,
        report_kind=DependencyReportKind(normalized["kind"]),
        scanner_name=normalized["scanner"]["name"],
        scanner_version=normalized["scanner"].get("version"),
        report_payload=normalized,
        report_sha256=report_sha,
        signature_status=verification.status,
        signature_info=envelope,
        signer_fingerprint=verification.fingerprint,
        sbom_payload=sbom,
        sbom_sha256=sha256_json(sbom),
        policy_snapshot=policy_payload,
        policy_sha256=policy_sha,
        attestation_payload_sha256=attestation.payload_sha256,
        critical_count=counts["critical"],
        high_count=counts["high"],
        medium_count=counts["medium"],
        low_count=counts["low"],
        unknown_count=counts["unknown"],
        unpinned_count=len(evaluation["unpinned"]),
        prerelease_count=len(evaluation["prerelease"]),
        denied_count=len(evaluation["denied_hits"]),
        status=evaluation["status"],
        blockers=evaluation["blockers"],
        warnings=evaluation["warnings"],
        verified_at=utcnow() if verification.cryptographically_valid else None,
        expires_at=evaluation["expires_at"],
        created_by_id=user.id,
    )
    db.add(item)
    db.flush()
    return item


def verify_dependency_assessment(
    db: Session,
    *,
    item: ReleaseDependencyAssessment,
    settings: Settings,
) -> bool:
    """Проверить an assessment without rewriting its immutable evidence. The imported report, SBOM
    and policy snapshot are evidence. Verification may refresh derived status fields and the
    current signature state, but it must never silently replace hashes or snapshots after
    tampering.
    """

    attestation = db.scalar(
        select(ReleaseAttestation).where(
            ReleaseAttestation.id == item.release_attestation_id,
            ReleaseAttestation.organization_id == item.organization_id,
        )
    )
    integrity_blockers: list[str] = []
    if attestation is None:
        item.status = ReadinessStatus.BLOCKED
        item.blockers = ["Связанная release attestation не найдена."]
        item.warnings = []
        item.verified_at = None
        db.flush()
        return False

    try:
        normalized = normalize_dependency_report(item.report_payload, attestation=attestation)
    except SupplyChainError as exc:
        item.status = ReadinessStatus.BLOCKED
        item.blockers = [str(exc)]
        item.warnings = []
        item.verified_at = None
        db.flush()
        return False

    data = canonical_json(normalized)
    actual_report_sha = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual_report_sha, item.report_sha256):
        integrity_blockers.append("SHA-256 dependency report не совпадает с сохранённым evidence.")

    actual_policy_sha = sha256_json(item.policy_snapshot or {})
    if not hmac.compare_digest(actual_policy_sha, item.policy_sha256):
        integrity_blockers.append("SHA-256 снимка dependency policy не совпадает.")

    actual_sbom_sha = sha256_json(item.sbom_payload or {})
    if not hmac.compare_digest(actual_sbom_sha, item.sbom_sha256):
        integrity_blockers.append("SHA-256 SBOM не совпадает с сохранённым evidence.")

    if not hmac.compare_digest(
        item.attestation_payload_sha256 or "", attestation.payload_sha256 or ""
    ):
        integrity_blockers.append("Release attestation изменилась после создания assessment.")

    verification = verify_signature(
        db,
        organization_id=item.organization_id,
        data=data,
        envelope=item.signature_info,
        expected_purpose=DEPENDENCY_REPORT_PURPOSE,
    )
    policy = get_or_create_dependency_policy(
        db, organization_id=item.organization_id, settings=settings
    )
    current_policy_payload = dependency_policy_payload(policy)
    current_policy_sha = sha256_json(current_policy_payload)
    if not hmac.compare_digest(current_policy_sha, item.policy_sha256):
        integrity_blockers.append("Dependency policy изменилась; требуется новый assessment.")

    attestation_trusted = verify_release_attestation(db, attestation)
    evaluation = _evaluate_report(
        attestation=attestation,
        report=normalized,
        policy=policy,
        signature_status=verification.status,
        attestation_trusted=attestation_trusted,
        max_ttl_hours=settings.dependency_assessment_ttl_hours,
    )
    expected_sbom = build_release_sbom(attestation)
    if not hmac.compare_digest(sha256_json(expected_sbom), item.sbom_sha256):
        integrity_blockers.append("SBOM не соответствует текущему release attestation.")

    counts = evaluation["counts"]
    item.signature_status = verification.status
    item.signer_fingerprint = verification.fingerprint
    item.critical_count = counts["critical"]
    item.high_count = counts["high"]
    item.medium_count = counts["medium"]
    item.low_count = counts["low"]
    item.unknown_count = counts["unknown"]
    item.unpinned_count = len(evaluation["unpinned"])
    item.prerelease_count = len(evaluation["prerelease"])
    item.denied_count = len(evaluation["denied_hits"])
    item.expires_at = evaluation["expires_at"]

    blockers = list(dict.fromkeys([*integrity_blockers, *evaluation["blockers"]]))
    warnings = list(dict.fromkeys(evaluation["warnings"]))
    item.status = (
        ReadinessStatus.BLOCKED
        if blockers
        else ReadinessStatus.WARNING
        if warnings
        else ReadinessStatus.PASSED
    )
    item.blockers = blockers
    item.warnings = warnings
    item.verified_at = (
        utcnow() if verification.cryptographically_valid and not integrity_blockers else None
    )
    db.flush()
    return item.status != ReadinessStatus.BLOCKED


def _transparency_payload(
    *,
    sequence: int,
    event_type: ReleaseTransparencyEventType,
    attestation: ReleaseAttestation,
    user: User,
    previous_hash: str,
    created_at: datetime,
    reason: str | None,
) -> dict[str, Any]:
    """Реализовать внутренний этап transparency payload step. Вспомогательная функция сохраняет
    детерминированность и тестируемость процесса.
    """
    return {
        "schema_version": 1,
        "sequence": sequence,
        "event_type": event_type.value,
        "release_attestation_id": attestation.id,
        "release_payload_sha256": attestation.payload_sha256,
        "version": attestation.version,
        "source_commit": attestation.source_commit,
        "previous_hash": previous_hash,
        "created_by_id": user.id,
        "created_at": _aware(created_at).isoformat(),
        "reason": (reason or "").strip() or None,
    }


def _transparency_hash(payload: dict[str, Any]) -> str:
    """Вычислить transparency hash. Канонический ввод обеспечивает детерминированное сравнение
    целостности между процессами.
    """
    return hashlib.sha256(TRANSPARENCY_CHAIN_DOMAIN + canonical_json(payload)).hexdigest()


def _transparency_signing_bytes(payload: dict[str, Any]) -> bytes:
    """Реализовать внутренний этап transparency signing bytes step. Вспомогательная функция
    сохраняет детерминированность и тестируемость процесса.
    """
    return TRANSPARENCY_CHAIN_DOMAIN + canonical_json(payload)


def current_release_state(
    db: Session,
    *,
    organization_id: str,
    attestation_id: str,
) -> ReleaseTransparencyEventType | None:
    """Выполнить операцию current release state. Аргументы интерпретируются в контексте модуля,
    результат возвращается вызывающему коду.
    """
    event = db.scalar(
        select(ReleaseTransparencyEvent)
        .where(
            ReleaseTransparencyEvent.organization_id == organization_id,
            ReleaseTransparencyEvent.release_attestation_id == attestation_id,
        )
        .order_by(ReleaseTransparencyEvent.sequence.desc())
        .limit(1)
    )
    return event.event_type if event else None


def append_transparency_event(
    db: Session,
    *,
    organization_id: str,
    attestation: ReleaseAttestation,
    user: User,
    cipher: SecretCipher,
    event_type: ReleaseTransparencyEventType,
    reason: str | None = None,
) -> ReleaseTransparencyEvent:
    """Создать transparency event. Перед сохранением или возвратом нового значения проверяются
    связанные инварианты.
    """
    if attestation.organization_id != organization_id:
        raise SupplyChainError("Release attestation относится к другой организации")
    _lock_organization(db, organization_id)
    trusted = verify_release_attestation(db, attestation)
    if not trusted or attestation.signature_status != ArtifactSignatureStatus.VALID_TRUSTED:
        raise SupplyChainError(
            "В transparency log можно публиковать только доверенный release attestation"
        )
    state = current_release_state(
        db, organization_id=organization_id, attestation_id=attestation.id
    )
    if event_type == ReleaseTransparencyEventType.PUBLISHED:
        if state == ReleaseTransparencyEventType.PUBLISHED:
            raise SupplyChainError("Этот release attestation уже опубликован")
        active_same_version = db.scalars(
            select(ReleaseTransparencyEvent)
            .join(
                ReleaseAttestation,
                ReleaseAttestation.id == ReleaseTransparencyEvent.release_attestation_id,
            )
            .where(
                ReleaseTransparencyEvent.organization_id == organization_id,
                ReleaseAttestation.version == attestation.version,
            )
            .order_by(ReleaseTransparencyEvent.sequence.desc())
        ).all()
        latest_by_attestation: dict[str, ReleaseTransparencyEvent] = {}
        for event in active_same_version:
            latest_by_attestation.setdefault(event.release_attestation_id, event)
        if any(
            event.event_type == ReleaseTransparencyEventType.PUBLISHED
            and event.release_attestation_id != attestation.id
            for event in latest_by_attestation.values()
        ):
            raise SupplyChainError(
                "Для этой версии уже опубликован другой attestation; сначала отзовите его"
            )
    elif state != ReleaseTransparencyEventType.PUBLISHED:
        raise SupplyChainError("Отозвать можно только опубликованный release attestation")
    if event_type == ReleaseTransparencyEventType.WITHDRAWN and len((reason or "").strip()) < 5:
        raise SupplyChainError("Для отзыва укажите причину не короче 5 символов")
    latest = db.scalar(
        select(ReleaseTransparencyEvent)
        .where(ReleaseTransparencyEvent.organization_id == organization_id)
        .order_by(ReleaseTransparencyEvent.sequence.desc())
        .limit(1)
    )
    sequence = (latest.sequence if latest else 0) + 1
    previous_hash = latest.entry_hash if latest else ZERO_HASH
    created_at = utcnow()
    payload = _transparency_payload(
        sequence=sequence,
        event_type=event_type,
        attestation=attestation,
        user=user,
        previous_hash=previous_hash,
        created_at=created_at,
        reason=reason,
    )
    key = get_default_signing_key(db, organization_id=organization_id, require_private=True)
    if key is None:
        raise SupplyChainError(
            "Для transparency log требуется основной Ed25519-ключ с приватной частью"
        )
    try:
        signature = sign_bytes(
            _transparency_signing_bytes(payload),
            key=key,
            cipher=cipher,
            purpose=TRANSPARENCY_ENTRY_PURPOSE,
            created_at=created_at,
        )
    except ArtifactSigningError as exc:
        raise SupplyChainError(str(exc)) from exc
    verification = verify_signature(
        db,
        organization_id=organization_id,
        data=_transparency_signing_bytes(payload),
        envelope=signature,
        expected_purpose=TRANSPARENCY_ENTRY_PURPOSE,
    )
    if verification.status != ArtifactSignatureStatus.VALID_TRUSTED:
        raise SupplyChainError(
            verification.error or "Transparency entry не подтверждён доверенным активным ключом"
        )
    item = ReleaseTransparencyEvent(
        organization_id=organization_id,
        release_attestation_id=attestation.id,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        previous_hash=previous_hash,
        entry_hash=_transparency_hash(payload),
        signature_status=verification.status,
        signature_info=signature,
        signer_fingerprint=verification.fingerprint,
        created_by_id=user.id,
        created_at=created_at,
    )
    db.add(item)
    db.flush()
    return item


def verify_transparency_chain(
    db: Session,
    *,
    organization_id: str,
) -> dict[str, Any]:
    """Проверить transparency chain. Некорректные данные или состояние отклоняются до побочного
    эффекта.
    """
    entries = list(
        db.scalars(
            select(ReleaseTransparencyEvent)
            .where(ReleaseTransparencyEvent.organization_id == organization_id)
            .order_by(ReleaseTransparencyEvent.sequence.asc())
        ).all()
    )
    attestation_ids = {entry.release_attestation_id for entry in entries}
    attestations = (
        {
            item.id: item
            for item in db.scalars(
                select(ReleaseAttestation).where(
                    ReleaseAttestation.organization_id == organization_id,
                    ReleaseAttestation.id.in_(attestation_ids),
                )
            ).all()
        }
        if attestation_ids
        else {}
    )
    errors: list[str] = []
    expected_sequence = 1
    previous_hash = ZERO_HASH
    for entry in entries:
        if entry.sequence != expected_sequence:
            errors.append(f"Ожидалась sequence {expected_sequence}, обнаружена {entry.sequence}.")
        if not hmac.compare_digest(entry.previous_hash or "", previous_hash):
            errors.append(f"Sequence {entry.sequence}: previous_hash не совпадает.")
        payload = entry.payload or {}
        if payload.get("schema_version") != 1:
            errors.append(
                f"Sequence {entry.sequence}: schema_version transparency entry не поддерживается."
            )
        if payload.get("sequence") != entry.sequence:
            errors.append(f"Sequence {entry.sequence}: payload sequence изменён.")
        if payload.get("event_type") != entry.event_type.value:
            errors.append(f"Sequence {entry.sequence}: payload event_type изменён.")
        if payload.get("release_attestation_id") != entry.release_attestation_id:
            errors.append(f"Sequence {entry.sequence}: attestation id изменён.")
        if payload.get("previous_hash") != (entry.previous_hash or ""):
            errors.append(f"Sequence {entry.sequence}: payload previous_hash изменён.")
        if payload.get("created_by_id") != entry.created_by_id:
            errors.append(f"Sequence {entry.sequence}: created_by_id не совпадает.")
        try:
            payload_created_at = _parse_timestamp(payload.get("created_at"), field="created_at")
        except SupplyChainError:
            errors.append(f"Sequence {entry.sequence}: created_at некорректен.")
        else:
            if payload_created_at != _aware(entry.created_at):
                errors.append(f"Sequence {entry.sequence}: created_at не совпадает.")
        expected_hash = _transparency_hash(payload)
        if not hmac.compare_digest(expected_hash, entry.entry_hash):
            errors.append(f"Sequence {entry.sequence}: entry_hash не совпадает.")
        signature = verify_signature(
            db,
            organization_id=organization_id,
            data=_transparency_signing_bytes(payload),
            envelope=entry.signature_info or {},
            expected_purpose=TRANSPARENCY_ENTRY_PURPOSE,
        )
        if signature.status != ArtifactSignatureStatus.VALID_TRUSTED:
            errors.append(
                f"Sequence {entry.sequence}: подпись transparency entry недействительна или не доверена."
            )
        if entry.signature_status != signature.status:
            errors.append(f"Sequence {entry.sequence}: сохранённый статус подписи не совпадает.")
        if (entry.signer_fingerprint or "") != (signature.fingerprint or ""):
            errors.append(f"Sequence {entry.sequence}: fingerprint подписанта не совпадает.")
        attestation = attestations.get(entry.release_attestation_id)
        if attestation is None:
            errors.append(f"Sequence {entry.sequence}: release attestation не найден.")
        else:
            actual_attestation_digest = hashlib.sha256(
                canonical_release_payload(attestation.payload or {})
            ).hexdigest()
            if not hmac.compare_digest(actual_attestation_digest, attestation.payload_sha256 or ""):
                errors.append(
                    f"Sequence {entry.sequence}: сохранённый digest release attestation не соответствует payload."
                )
            if not hmac.compare_digest(
                str(payload.get("release_payload_sha256") or ""),
                actual_attestation_digest,
            ):
                errors.append(
                    f"Sequence {entry.sequence}: digest release attestation не совпадает."
                )
            if payload.get("version") != attestation.version:
                errors.append(
                    f"Sequence {entry.sequence}: версия release attestation не совпадает."
                )
            if payload.get("source_commit") != attestation.source_commit:
                errors.append(
                    f"Sequence {entry.sequence}: source commit release attestation не совпадает."
                )
        previous_hash = entry.entry_hash
        expected_sequence += 1
    active: dict[str, str] = {}
    for entry in entries:
        active[entry.release_attestation_id] = entry.event_type.value
    return {
        "valid": not errors,
        "entry_count": len(entries),
        "last_sequence": entries[-1].sequence if entries else 0,
        "last_hash": entries[-1].entry_hash if entries else ZERO_HASH,
        "active_release_count": sum(
            1 for state in active.values() if state == ReleaseTransparencyEventType.PUBLISHED.value
        ),
        "errors": errors,
    }


def validate_change_release_transparency(
    db: Session,
    *,
    change: ChangeRequest,
    settings: Settings,
) -> tuple[bool, str]:
    """Проверить change release transparency. Некорректные данные или состояние отклоняются до
    побочного эффекта.
    """
    if change.change_type != ChangeRequestType.UPGRADE:
        return True, "Для этого типа изменения transparency log не требуется."
    if not settings.require_release_transparency:
        return True, "Проверка release transparency отключена для этой среды."
    if not change.release_attestation_id:
        return False, "Для обновления отсутствует release attestation."
    chain = verify_transparency_chain(db, organization_id=change.organization_id)
    if not chain["valid"]:
        return False, "Нарушена целостность release transparency chain."
    state = current_release_state(
        db,
        organization_id=change.organization_id,
        attestation_id=change.release_attestation_id,
    )
    if state != ReleaseTransparencyEventType.PUBLISHED:
        return False, "Целевая сборка не опубликована в release transparency log."
    return True, "Целевая сборка опубликована, transparency chain подтверждена."


def validate_change_dependency_assurance(
    db: Session,
    *,
    change: ChangeRequest,
    settings: Settings,
) -> tuple[bool, str, str | None]:
    """Проверить change dependency assurance. Некорректные данные или состояние отклоняются до
    побочного эффекта.
    """
    if change.change_type != ChangeRequestType.UPGRADE:
        return True, "Для этого типа изменения dependency assessment не требуется.", None
    if not settings.require_dependency_assessment:
        return True, "Dependency assurance отключён для этой среды.", None
    if not change.release_attestation_id:
        return False, "Для обновления отсутствует release attestation.", None
    if not change.release_dependency_assessment_id:
        return (
            False,
            "Change request не привязан к конкретному dependency assessment.",
            None,
        )
    policy = get_or_create_dependency_policy(
        db, organization_id=change.organization_id, settings=settings
    )
    item = db.scalar(
        select(ReleaseDependencyAssessment).where(
            ReleaseDependencyAssessment.id == change.release_dependency_assessment_id,
            ReleaseDependencyAssessment.organization_id == change.organization_id,
            ReleaseDependencyAssessment.release_attestation_id == change.release_attestation_id,
        )
    )
    if item is None:
        return (
            False,
            "Закреплённый dependency assessment не найден или относится к другой сборке.",
            change.release_dependency_assessment_id,
        )
    verify_dependency_assessment(db, item=item, settings=settings)
    current_policy_sha = sha256_json(dependency_policy_payload(policy))
    if not hmac.compare_digest(item.policy_sha256, current_policy_sha):
        return False, "Dependency assessment создан по устаревшей политике.", item.id
    if item.expires_at <= utcnow():
        return False, "Dependency assessment просрочен.", item.id
    if (
        settings.dependency_require_trusted_report
        and item.signature_status != ArtifactSignatureStatus.VALID_TRUSTED
    ):
        return False, "Dependency assessment не подписан доверенным ключом.", item.id
    if (
        settings.dependency_require_vulnerability_scan
        and item.report_kind != DependencyReportKind.VULNERABILITY_SCAN
    ):
        return (
            False,
            "Для production требуется vulnerability scan, inventory-only отчёт недостаточен.",
            item.id,
        )
    if item.status == ReadinessStatus.BLOCKED:
        return (
            False,
            item.blockers[0] if item.blockers else "Dependency assessment заблокирован.",
            item.id,
        )
    return True, "Dependency assessment актуален и соответствует политике.", item.id
