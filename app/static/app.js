const API = "/api/v1";

const state = {
  user: null,
  route: "dashboard",
  data: {
    connections: [],
    destinations: [],
    templates: [],
    media: [],
    campaigns: [],
    approvalRequests: [],
    jobs: [],
    notifications: [],
    audit: [],
    auditVerification: null,
    users: [],
    businessConnections: [],
    conversations: [],
    candidates: [],
    policies: [],
    flows: [],
    providers: [],
    knowledge: [],
    integrations: [],
    outbox: [],
    apiKeys: [],
    privacyRequests: [],
    organization: null,
    pilotOverview: null,
    pilotStage: null,
    stageAssessments: [],
    pilotCanaries: [],
    supportBundles: [],
    readinessReports: [],
    blackouts: [],
    commissioningChecks: [],
    pilotPrograms: [],
    configurationBundles: [],
    signingKeys: [],
    recoveryStatus: null,
    recoveryBackups: [],
    recoveryDrills: [],
    changes: [],
    releaseAttestations: [],
    dependencyPolicy: null,
    dependencyAssessments: [],
    transparencyEvents: [],
    transparencyVerification: null,
    operationsOverview: null,
    sloAssessments: [],
    incidents: [],
    executionOverview: null,
    failovers: [],
    deliveryAttempts: [],
    continuityOverview: null,
    continuityDrills: [],
    continuityExecution: null,
    capacityOverview: null,
    capacityAssessments: [],
  },
  refreshing: null,
  analyticsRange: null,
  flowDraft: null,
  flowDragNodeId: null,
  notificationCounts: { unread: 0, critical_unacknowledged: 0 },
};

const root = document.getElementById("root");
const modalRoot = document.getElementById("modal-root");
const toastContainer = document.getElementById("toast-container");
let generatedFieldId = 0;

/**
 * Связать визуальные подписи динамических форм с их полями.
 *
 * Шаблоны SPA создаются строками, поэтому эта нормализация выполняется после
 * каждого рендера страницы или модального окна и сохраняет уже заданные id.
 */
function ensureAccessibleFormLabels(container) {
  container.querySelectorAll(".form-group").forEach((group) => {
    const label = group.querySelector(":scope > label:not(.checkbox-row)");
    if (!label || label.htmlFor) return;
    if (!label.textContent.trim()) {
      label.setAttribute("aria-hidden", "true");
      return;
    }
    const control = group.querySelector(
      ":scope > input:not([type='hidden']), :scope > select, :scope > textarea",
    );
    if (!control) return;
    if (!control.id) {
      generatedFieldId += 1;
      control.id = `teleflow-field-${generatedFieldId}`;
    }
    label.htmlFor = control.id;
  });
}

const routeMeta = {
  dashboard: ["Обзор", "Состояние платформы и безопасной очереди"],
  analytics: ["Аналитика", "Доставка, диалоги, кандидаты и эффективность автоматизации"],
  connections: ["Telegram-подключения", "Bot API и пользовательские MTProto-сессии"],
  destinations: ["Группы и каналы", "Только заранее разрешённые места публикации"],
  media: ["Медиафайлы", "Изображения и документы для шаблонов"],
  templates: ["Шаблоны", "Контент сообщений с версионированием"],
  campaigns: ["Кампании", "Предпросмотр, утверждение и расписание"],
  pilot: ["Production Pilot", "Сертификация масштаба, canary-проверки и безопасная диагностика"],
  commissioning: ["Ввод в эксплуатацию", "Инфраструктурные проверки, этапы live-пилота и перенос безопасной конфигурации"],
  artifactTrust: ["Подписи и доверие", "Ed25519-ключи, происхождение архивов и автономная проверка артефактов"],
  recovery: ["Восстановление", "Подписанные резервные копии, RPO/RTO и изолированные restore drill"],
  changes: ["Изменения", "Maintenance mode, независимое одобрение и pre/post-deploy проверки"],
  operations: ["Надёжность и инциденты", "Операционные SLO, error budget и управляемое реагирование"],
  capacity: ["Нагрузка и лимиты", "Admission control, backpressure, прогноз очереди и сетевой бюджет"],
  execution: ["Active / Standby", "Epoch-fencing, журнал сетевых попыток и управляемое переключение площадок"],
  continuity: ["Непрерывность", "Simulation, live failover/failback, RTO и проверяемое evidence"],
  releases: ["Доверие к релизам", "Подписанный provenance, состав зависимостей и допуск сборок к обновлению"],
  supplyChain: ["Поставка и зависимости", "Transparency log, CycloneDX SBOM и подписанные vulnerability assessments"],
  jobs: ["Очередь отправки", "Доставка, повторы и ручная проверка"],
  notifications: ["Уведомления", "Критические события, подтверждения и операторский контроль"],
  business: ["Telegram Business", "Webhook, подключённые профили и входящие обновления"],
  conversations: ["Диалоги", "Входящие обращения, AI и передача оператору"],
  candidates: ["Кандидаты", "Структурированные анкеты и контролируемый доступ к контактам"],
  automation: ["Автоматизация", "Политики, база знаний и AI-провайдеры"],
  flows: ["Сценарии", "Визуальный сбор анкеты и безопасная передача оператору"],
  integrations: ["Интеграции", "Webhook, Google Sheets, CSV и надёжный outbox"],
  privacy: ["Данные и приватность", "Экспорт, удаление и сроки хранения"],
  audit: ["Журнал аудита", "Безопасность и действия операторов"],
  organization: ["Организация", "Настройки, часовой пояс и хранение данных"],
  apiKeys: ["API-ключи", "Сервисный доступ с минимальными scopes"],
  security: ["Безопасность", "Пароль и двухфакторная аутентификация"],
  users: ["Пользователи", "Роли и доступ к панели"],
};

const navItems = [
  ["dashboard", "▦", "Обзор", "Работа"],
  ["analytics", "⌁", "Аналитика", "Работа"],
  ["connections", "◉", "Подключения", "Работа"],
  ["business", "↯", "Telegram Business", "Работа"],
  ["conversations", "◌", "Диалоги", "Работа"],
  ["candidates", "♧", "Кандидаты", "Работа"],
  ["destinations", "⌖", "Группы", "Публикации"],
  ["media", "▧", "Медиа", "Публикации"],
  ["templates", "≡", "Шаблоны", "Публикации"],
  ["campaigns", "▷", "Кампании", "Публикации"],
  ["pilot", "◫", "Production Pilot", "Контроль"],
  ["commissioning", "◎", "Ввод в эксплуатацию", "Контроль"],
  ["artifactTrust", "⌘", "Подписи и доверие", "Контроль"],
  ["recovery", "⟲", "Восстановление", "Контроль"],
  ["changes", "∆", "Изменения", "Контроль", ["owner", "admin"]],
  ["operations", "◬", "Надёжность и инциденты", "Контроль"],
  ["capacity", "◫", "Нагрузка и лимиты", "Контроль"],
  ["execution", "⇆", "Active / Standby", "Контроль"],
  ["continuity", "∞", "Непрерывность", "Контроль"],
  ["releases", "◉", "Доверие к релизам", "Контроль", ["owner", "admin"]],
  ["supplyChain", "⛓", "Поставка и зависимости", "Контроль", ["owner", "admin"]],
  ["automation", "✦", "Автоматизация", "Автоматизация"],
  ["flows", "◇", "Сценарии", "Автоматизация"],
  ["integrations", "⇥", "Интеграции", "Автоматизация", ["owner", "admin"]],
  ["jobs", "⇄", "Очередь", "Контроль"],
  ["notifications", "♢", "Уведомления", "Контроль"],
  ["privacy", "◈", "Приватность", "Контроль", ["owner", "admin"]],
  ["audit", "☷", "Аудит", "Контроль", ["owner", "admin"]],
  ["organization", "⌂", "Организация", "Система", ["owner", "admin"]],
  ["apiKeys", "⌁", "API-ключи", "Система", ["owner"]],
  ["security", "◇", "Безопасность", "Система"],
  ["users", "♙", "Пользователи", "Система", ["owner"]],
];

/**
 * Выполнить escapehtml, явно сохраняя побочные эффекты UI или worker.
 */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/**
 * Выполнить attr, явно сохраняя побочные эффекты UI или worker.
 */
function attr(value) {
  return escapeHtml(value);
}

/**
 * Выполнить cookie, явно сохраняя побочные эффекты UI или worker.
 */
function cookie(name) {
  const prefix = `${name}=`;
  const found = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(prefix));
  return found ? decodeURIComponent(found.slice(prefix.length)) : null;
}

/**
 * Выполнить detailmessage, явно сохраняя побочные эффекты UI или worker.
 */
function detailMessage(detail) {
  if (!detail) return "Неизвестная ошибка";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  if (detail.message) return detail.message;
  if (detail.blockers) return detail.blockers.join("; ");
  return JSON.stringify(detail);
}

/**
 * Выполнить api, явно сохраняя побочные эффекты UI или worker.
 */
async function api(path, options = {}, retry = true) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  const bodyIsForm = options.body instanceof FormData;
  if (options.body && !bodyIsForm && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = cookie("teleflow_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(`${API}${path}`, {
    credentials: "include",
    ...options,
    method,
    headers,
  });
  if (response.status === 401 && retry && !path.startsWith("/auth/login") && !path.startsWith("/auth/refresh")) {
    const refreshed = await refreshSession();
    if (refreshed) return api(path, options, false);
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(detailMessage(payload?.detail ?? payload));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

/**
 * Выполнить refreshsession, явно сохраняя побочные эффекты UI или worker.
 */
async function refreshSession() {
  if (state.refreshing) return state.refreshing;
  state.refreshing = (async () => {
    try {
      const result = await api("/auth/refresh", { method: "POST" }, false);
      state.user = result.user;
      return true;
    } catch {
      state.user = null;
      return false;
    } finally {
      state.refreshing = null;
    }
  })();
  return state.refreshing;
}

/**
 * Выполнить toast, явно сохраняя побочные эффекты UI или worker.
 */
function toast(title, message = "", type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.innerHTML = `<div>${type === "error" ? "!" : type === "warning" ? "△" : "✓"}</div><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
  toastContainer.appendChild(item);
  window.setTimeout(() => item.remove(), 5200);
}

/**
 * Выполнить formatdate, явно сохраняя побочные эффекты UI или worker.
 */
function formatDate(value, withTime = true) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    ...(withTime ? { timeStyle: "short" } : {}),
  }).format(date);
}

/**
 * Выполнить formatpercentbps, явно сохраняя побочные эффекты UI или worker.
 */
function formatPercentBps(value, { budget = false } = {}) {
  if (value === null || value === undefined) return "—";
  const percent = Number(value) / 100;
  if (!Number.isFinite(percent)) return "—";
  return `${percent.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%${budget ? " бюджета" : ""}`;
}

/**
 * Выполнить formatbytes, явно сохраняя побочные эффекты UI или worker.
 */
function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = Number(bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

/**
 * Выполнить truncate, явно сохраняя побочные эффекты UI или worker.
 */
function truncate(value, max = 90) {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * Выполнить initials, явно сохраняя побочные эффекты UI или worker.
 */
function initials(name) {
  return String(name || "U").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

/**
 * Выполнить rolelabel, явно сохраняя побочные эффекты UI или worker.
 */
function roleLabel(role) {
  return ({ owner: "Владелец", admin: "Администратор", operator: "Оператор", viewer: "Наблюдатель" })[role] || role;
}

/**
 * Выполнить can, явно сохраняя побочные эффекты UI или worker.
 */
function can(...roles) {
  return Boolean(state.user && roles.includes(state.user.role));
}

/**
 * Выполнить badge, явно сохраняя побочные эффекты UI или worker.
 */
function badge(value, map = {}) {
  const item = map[value] || { label: value || "—", cls: "muted" };
  return `<span class="badge badge-${item.cls}">${escapeHtml(item.label)}</span>`;
}

const connectionStatuses = {
  active: { label: "Активно", cls: "success" },
  paused: { label: "Пауза", cls: "warning" },
  auth_pending: { label: "Ожидает вход", cls: "warning" },
  draft: { label: "Черновик", cls: "muted" },
  error: { label: "Ошибка", cls: "danger" },
  revoked: { label: "Отозвано", cls: "danger" },
};
const permissionStatuses = {
  confirmed: { label: "Разрешено", cls: "success" },
  unverified: { label: "Не проверено", cls: "warning" },
  denied: { label: "Запрещено", cls: "danger" },
};
const campaignStatuses = {
  draft: { label: "Черновик", cls: "muted" },
  scheduled: { label: "Запланировано", cls: "info" },
  running: { label: "Выполняется", cls: "success" },
  paused: { label: "Пауза", cls: "warning" },
  completed: { label: "Завершено", cls: "success" },
  cancelled: { label: "Отменено", cls: "danger" },
  failed: { label: "Ошибка", cls: "danger" },
};
const approvalStatuses = {
  pending: { label: "Ожидает решения", cls: "warning" },
  approved: { label: "Утверждено", cls: "success" },
  rejected: { label: "Отклонено", cls: "danger" },
  cancelled: { label: "Отменено", cls: "muted" },
  expired: { label: "Истекло", cls: "danger" },
};
const readinessStatuses = {
  passed: { label: "Готово", cls: "success" },
  warning: { label: "С предупреждениями", cls: "warning" },
  blocked: { label: "Заблокировано", cls: "danger" },
};
const readinessCheckStatuses = {
  passed: { label: "Пройдено", cls: "success" },
  warning: { label: "Предупреждение", cls: "warning" },
  blocked: { label: "Блокирует", cls: "danger" },
};
const incidentStatuses = {
  open: { label: "Открыт", cls: "danger" },
  acknowledged: { label: "Подтверждён", cls: "warning" },
  mitigating: { label: "Устраняется", cls: "info" },
  resolved: { label: "Разрешён", cls: "success" },
  closed: { label: "Закрыт", cls: "muted" },
};
const incidentSources = {
  manual: "Ручной",
  slo: "SLO",
  delivery: "Доставка",
  worker: "Worker",
  change: "Изменение",
  security: "Безопасность",
};
const severityStatuses = {
  info: { label: "Информация", cls: "info" },
  warning: { label: "Предупреждение", cls: "warning" },
  critical: { label: "Критично", cls: "danger" },
};
const executionLeaseStatuses = {
  active: { label: "Active", cls: "success" },
  draining: { label: "Draining", cls: "warning" },
  frozen: { label: "Frozen", cls: "danger" },
};
const failoverStatuses = {
  requested: { label: "Ожидает переключения", cls: "warning" },
  completed: { label: "Завершено", cls: "success" },
  cancelled: { label: "Отменено", cls: "muted" },
  failed: { label: "Ошибка", cls: "danger" },
};
const continuityDrillStatuses = {
  draft: { label: "Черновик", cls: "muted" },
  running: { label: "Failover выполняется", cls: "info" },
  awaiting_failback: { label: "Ожидает failback", cls: "warning" },
  awaiting_signoff: { label: "Ожидает приёмки", cls: "warning" },
  passed: { label: "Принято", cls: "success" },
  failed: { label: "Ошибка", cls: "danger" },
  cancelled: { label: "Отменено", cls: "muted" },
  invalidated: { label: "Устарело", cls: "danger" },
};
const continuityModes = { simulation: "Simulation", live: "Live failover/failback" };
const continuityEventLabels = {
  created: "Учение создано",
  simulation_completed: "Simulation завершена",
  failover_requested: "Запрошен failover",
  target_active: "Standby стала active",
  failback_requested: "Запрошен failback",
  primary_restored: "Primary восстановлена",
  signed_off: "Evidence принято",
  cancelled: "Учение отменено",
  invalidated: "Evidence аннулировано",
  failed: "Учение завершилось ошибкой",
};

const deliveryAttemptStatuses = {
  prepared: { label: "Подготовлено", cls: "muted" },
  network_started: { label: "Сеть начата", cls: "warning" },
  sent: { label: "Отправлено", cls: "success" },
  failed: { label: "Ошибка", cls: "danger" },
  uncertain: { label: "Требует сверки", cls: "danger" },
  abandoned: { label: "Без сетевого вызова", cls: "muted" },
  reconciled_not_sent: { label: "Сверено: не отправлено", cls: "info" },
  reconciled_skipped: { label: "Сверено: пропущено", cls: "muted" },
};
const pilotProgramStatuses = {
  draft: { label: "Черновик", cls: "muted" },
  active: { label: "Выполняется", cls: "info" },
  paused: { label: "Приостановлено", cls: "warning" },
  completed: { label: "Принято", cls: "success" },
  cancelled: { label: "Отменено", cls: "danger" },
};
const pilotStageStatuses = {
  pending: { label: "Не готов", cls: "muted" },
  ready: { label: "Готов", cls: "success" },
  running: { label: "Выполняется", cls: "info" },
  awaiting_signoff: { label: "Ожидает приёмки", cls: "warning" },
  passed: { label: "Принят", cls: "success" },
  failed: { label: "Не принят", cls: "danger" },
  skipped: { label: "Пропущен", cls: "muted" },
};
const bundleKinds = { export: "Экспорт", import: "Импорт" };
const blackoutScopes = {
  organization: "Вся организация",
  connection: "Telegram-подключение",
  destination: "Отдельная группа",
};
const blackoutKinds = {
  one_time: "Разовое окно",
  weekly: "Еженедельно",
};
const pilotStageOrder = ["local", "service", "five", "twenty", "fifty", "hundred"];
const pilotStageLabels = {
  local: "Локальный fake mode",
  service: "1 служебная группа",
  five: "До 5 групп",
  twenty: "До 20 групп",
  fifty: "До 50 групп",
  hundred: "До 100 групп",
};
const pilotCanaryStatuses = {
  pending: { label: "Выполняется", cls: "info" },
  sent: { label: "Отправлено", cls: "success" },
  failed: { label: "Ошибка", cls: "danger" },
  blocked: { label: "Заблокировано", cls: "warning" },
};
const supportBundleStatuses = {
  ready: { label: "Готов", cls: "success" },
  failed: { label: "Ошибка", cls: "danger" },
  expired: { label: "Истёк", cls: "warning" },
  deleted: { label: "Удалён", cls: "muted" },
};
const transparencyEventStatuses = {
  published: { label: "Опубликован", cls: "success" },
  withdrawn: { label: "Отозван", cls: "danger" },
};
const dependencyReportKinds = {
  inventory_only: "Инвентаризация",
  vulnerability_scan: "Сканирование уязвимостей",
};
const artifactSignatureStatuses = {
  unsigned: { label: "Без подписи", cls: "warning" },
  valid_trusted: { label: "Подпись доверена", cls: "success" },
  valid_untrusted: { label: "Подпись не доверена", cls: "warning" },
  invalid: { label: "Подпись повреждена", cls: "danger" },
  revoked: { label: "Ключ отозван", cls: "danger" },
};
const artifactKeyStatuses = {
  active: { label: "Активен", cls: "success" },
  revoked: { label: "Отозван", cls: "danger" },
};
const recoveryBackupStatuses = {
  registered: { label: "Receipt зарегистрирован", cls: "info" },
  verified: { label: "Архив проверен", cls: "success" },
  invalid: { label: "Недействителен", cls: "danger" },
  expired: { label: "Истёк", cls: "warning" },
  deleted: { label: "Удалён", cls: "muted" },
};
const recoveryDrillStatuses = {
  passed: { label: "Пройден", cls: "success" },
  failed: { label: "Не пройден", cls: "danger" },
  blocked: { label: "Заблокирован", cls: "warning" },
};
const recoveryDrillModes = {
  sqlite_isolated: "SQLite · изолированная проверка",
  postgresql_isolated: "PostgreSQL · изолированное восстановление",
  metadata_only: "Проверка архива и метаданных",
};
const notificationStatuses = {
  unread: { label: "Новое", cls: "warning" },
  read: { label: "Прочитано", cls: "info" },
  acknowledged: { label: "Подтверждено", cls: "success" },
};

const jobStatuses = {
  held: { label: "Удерживается", cls: "muted" }, pending: { label: "Ожидает", cls: "info" },
  processing: { label: "Отправляется", cls: "info" }, retry: { label: "Повтор", cls: "warning" },
  waiting_review: { label: "Ручная сверка", cls: "danger" }, sent: { label: "Отправлено", cls: "success" },
  failed: { label: "Ошибка", cls: "danger" }, cancelled: { label: "Отменено", cls: "muted" },
  skipped: { label: "Пропущено", cls: "warning" },
};
const runStatuses = {
  queued: { label: "В очереди", cls: "info" }, running: { label: "Выполняется", cls: "success" },
  awaiting_checkpoint: { label: "Ожидает checkpoint", cls: "warning" },
  completed: { label: "Завершён", cls: "success" }, partially_failed: { label: "Частичные ошибки", cls: "warning" },
  failed: { label: "Ошибка", cls: "danger" }, cancelled: { label: "Отменён", cls: "muted" },
};
const preflightStatuses = {
  passed: { label: "Проверка пройдена", cls: "success" }, warning: { label: "Есть предупреждения", cls: "warning" },
  blocked: { label: "Запуск заблокирован", cls: "danger" }, expired: { label: "Проверка истекла", cls: "muted" },
};

const conversationStatuses = {
  new: { label: "Новый", cls: "info" },
  awaiting_consent: { label: "Ожидает согласия", cls: "warning" },
  ai_active: { label: "AI отвечает", cls: "success" },
  human_handoff: { label: "Нужен оператор", cls: "danger" },
  closed: { label: "Закрыт", cls: "muted" },
  blocked: { label: "Заблокирован", cls: "danger" },
};
const consentStatuses = {
  unknown: { label: "Не запрошено", cls: "muted" },
  requested: { label: "Запрошено", cls: "warning" },
  granted: { label: "Получено", cls: "success" },
  declined: { label: "Отклонено", cls: "danger" },
  revoked: { label: "Отозвано", cls: "danger" },
};
const candidateStatuses = {
  new: { label: "Новый", cls: "info" },
  qualifying: { label: "Сбор данных", cls: "warning" },
  ready_for_review: { label: "Готов к проверке", cls: "success" },
  contacted: { label: "Связались", cls: "info" },
  archived: { label: "Архив", cls: "muted" },
};
const businessStatuses = {
  active: { label: "Подключено", cls: "success" },
  disconnected: { label: "Отключено", cls: "warning" },
  revoked: { label: "Отозвано", cls: "danger" },
};
const outboxStatuses = {
  pending: { label: "Ожидает", cls: "info" }, processing: { label: "Отправляется", cls: "info" },
  retry: { label: "Повтор", cls: "warning" }, delivered: { label: "Доставлено", cls: "success" },
  failed: { label: "Ошибка", cls: "danger" }, dead: { label: "Остановлено", cls: "danger" },
};
const privacyStatuses = {
  pending: { label: "Ожидает", cls: "info" }, processing: { label: "Выполняется", cls: "warning" },
  completed: { label: "Завершено", cls: "success" }, failed: { label: "Ошибка", cls: "danger" },
};
const apiKeyStatuses = {
  active: { label: "Активен", cls: "success" }, revoked: { label: "Отозван", cls: "danger" },
  expired: { label: "Истёк", cls: "warning" },
};
const integrationKinds = { webhook: "Webhook", google_sheets: "Google Sheets", csv_export: "CSV-экспорт" };

/**
 * Выполнить contactname, явно сохраняя побочные эффекты UI или worker.
 */
function contactName(item) {
  const full = [item.first_name, item.last_name].filter(Boolean).join(" ").trim();
  return full || (item.username ? `@${item.username}` : `Telegram ${item.telegram_user_id || item.telegram_chat_id || "—"}`);
}

/**
 * Выполнить downloadfile, явно сохраняя побочные эффекты UI или worker.
 */
async function downloadFile(path, fallbackName) {
  const response = await fetch(`${API}${path}`, { credentials: "include" });
  if (!response.ok) {
    let message = "Не удалось скачать файл";
    try { message = detailMessage((await response.json()).detail); } catch { /* response is not JSON */ }
    throw new Error(message);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * Выполнить canonicaljson, явно сохраняя побочные эффекты UI или worker.
 */
function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

/**
 * Выполнить sha256hex, явно сохраняя побочные эффекты UI или worker.
 */
async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Выполнить button, явно сохраняя побочные эффекты UI или worker.
 */
function button(label, action, id = "", cls = "secondary", disabled = false, extra = "") {
  return `<button class="btn btn-${cls} btn-sm" data-action="${attr(action)}" data-id="${attr(id)}" ${disabled ? "disabled" : ""} ${extra}>${label}</button>`;
}

/**
 * Выполнить emptystate, явно сохраняя побочные эффекты UI или worker.
 */
function emptyState(icon, title, text, actionHtml = "") {
  return `<div class="empty"><div class="empty-icon">${icon}</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p>${actionHtml}</div>`;
}

/**
 * Выполнить openmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openModal(title, body, { large = false } = {}) {
  modalRoot.innerHTML = `<div class="modal-backdrop" data-action="modal-backdrop"><section class="modal ${large ? "modal-lg" : ""}" role="dialog" aria-modal="true" aria-label="${attr(title)}"><header class="modal-header"><h3>${escapeHtml(title)}</h3><button class="modal-close" type="button" data-action="close-modal" aria-label="Закрыть">×</button></header><div class="modal-body">${body}</div></section></div>`;
  ensureAccessibleFormLabels(modalRoot);
  const first = modalRoot.querySelector("input, select, textarea, button");
  if (first) window.setTimeout(() => first.focus(), 0);
}

/**
 * Выполнить closemodal, явно сохраняя побочные эффекты UI или worker.
 */
function closeModal() {
  modalRoot.innerHTML = "";
  state.flowDraft = null;
}

/**
 * Выполнить loadingpage, явно сохраняя побочные эффекты UI или worker.
 */
function loadingPage() {
  return `<div class="loading-block"><div class="spinner"></div><span>Загрузка данных</span></div>`;
}

/**
 * Выполнить renderlogin, явно сохраняя побочные эффекты UI или worker.
 */
function renderLogin() {
  root.innerHTML = `
    <main class="login-page">
      <section class="login-hero">
        <div class="brand"><div class="brand-mark">TF</div><strong>TeleFlow Platform</strong></div>
        <h1>Контролируемые публикации в Telegram</h1>
        <p>Единая панель для официального Bot API и пользовательских MTProto-сессий. Каждая группа подтверждается вручную, а ограничения Telegram останавливают очередь вместо попыток обхода.</p>
        <div class="safety-points">
          <div class="safety-point"><strong>Разрешённые группы</strong>Никакого автоматического вступления или рассылки в личные сообщения.</div>
          <div class="safety-point"><strong>Шифрование секретов</strong>Токены и MTProto-сессии хранятся в зашифрованном виде.</div>
          <div class="safety-point"><strong>Ручное утверждение</strong>Полный предпросмотр маршрута до создания заданий.</div>
          <div class="safety-point"><strong>Fail-safe очередь</strong>FloodWait и антиспам переводят систему в безопасную паузу.</div>
        </div>
      </section>
      <section class="login-side">
        <form class="login-card" id="login-form">
          <h2>Вход в панель</h2>
          <p>Используйте учётную запись администратора.</p>
          <div class="form-group"><label for="login-email">Email</label><input class="input" id="login-email" name="email" type="email" autocomplete="username" required value="admin@example.com"></div>
          <div class="form-group mt-14"><label for="login-password">Пароль</label><input class="input" id="login-password" name="password" type="password" autocomplete="current-password" required></div>
          <div class="form-group mt-14"><label for="login-totp">Код 2FA <span class="cell-sub">при включённой защите</span></label><input class="input" id="login-totp" name="totp_code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}"></div>
          <button class="btn btn-primary w-full mt-22" type="submit">Войти</button>
          <div id="login-error" class="alert alert-danger hidden mt-14"></div>
        </form>
      </section>
    </main>`;
  document.getElementById("login-form").addEventListener("submit", loginSubmit);
}

/**
 * Выполнить loginsubmit, явно сохраняя побочные эффекты UI или worker.
 */
async function loginSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const buttonEl = form.querySelector("button[type=submit]");
  const errorEl = document.getElementById("login-error");
  buttonEl.disabled = true;
  errorEl.classList.add("hidden");
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.totp_code) delete data.totp_code;
  try {
    const result = await api("/auth/login", { method: "POST", body: JSON.stringify(data) }, false);
    state.user = result.user;
    state.data.organization = result.organization || null;
    await refreshChromeState();
    location.hash = "#/dashboard";
    renderShell();
    await navigate("dashboard");
  } catch (error) {
    errorEl.textContent = error.message;
    errorEl.classList.remove("hidden");
  } finally {
    buttonEl.disabled = false;
  }
}

/**
 * Выполнить filterednav, явно сохраняя побочные эффекты UI или worker.
 */
function filteredNav() {
  return navItems.filter((item) => !item[4] || item[4].includes(state.user.role));
}

/**
 * Выполнить updatechromeindicators, явно сохраняя побочные эффекты UI или worker.
 */
function updateChromeIndicators() {
  const unread = Number(state.notificationCounts.unread || 0);
  const critical = Number(state.notificationCounts.critical_unacknowledged || 0);
  const buttonEl = document.querySelector(".notification-button");
  if (buttonEl) {
    buttonEl.classList.toggle("has-critical", critical > 0);
    buttonEl.innerHTML = `♢${unread ? `<span>${unread > 99 ? "99+" : unread}</span>` : ""}`;
  }
  const navEl = document.querySelector('.nav-link[data-route="notifications"]');
  if (navEl) {
    navEl.querySelector(".nav-count")?.remove();
    if (unread) navEl.insertAdjacentHTML("beforeend", `<span class="nav-count">${unread > 99 ? "99+" : unread}</span>`);
  }
}

/**
 * Выполнить refreshchromestate, явно сохраняя побочные эффекты UI или worker.
 */
async function refreshChromeState() {
  if (!state.user) return;
  const tasks = [];
  if (!state.data.organization) tasks.push(api("/organization").then((value) => { state.data.organization = value; }));
  tasks.push(api("/notifications/counts").then((value) => { state.notificationCounts = value; }));
  try { await Promise.all(tasks); } catch { /* chrome metadata must not block the current page */ }
  updateChromeIndicators();
}

/**
 * Выполнить rendershell, явно сохраняя побочные эффекты UI или worker.
 */
function renderShell() {
  const groups = [];
  for (const item of filteredNav()) if (!groups.includes(item[3])) groups.push(item[3]);
  const nav = groups.map((group) => `<div class="nav-group-label">${escapeHtml(group)}</div><div class="nav-list">${filteredNav().filter((item) => item[3] === group).map(([route, icon, label]) => `<button class="nav-link ${state.route === route ? "active" : ""}" data-route="${route}"><span class="nav-icon">${icon}</span>${escapeHtml(label)}${route === "notifications" && state.notificationCounts.unread ? `<span class="nav-count">${state.notificationCounts.unread}</span>` : ""}</button>`).join("")}</div>`).join("");
  const [title, subtitle] = routeMeta[state.route] || routeMeta.dashboard;
  const organization = state.data.organization;
  const paused = Boolean(organization?.publishing_paused);
  const maintenance = Boolean(organization?.maintenance_mode);
  const unread = Number(state.notificationCounts.unread || 0);
  const critical = Number(state.notificationCounts.critical_unacknowledged || 0);
  root.innerHTML = `<div class="app-shell">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-brand"><div class="brand-mark">TF</div><span>TeleFlow</span></div>
      ${nav}
      <div class="sidebar-footer"><div class="sidebar-user"><div class="avatar">${escapeHtml(initials(state.user.display_name))}</div><div><strong>${escapeHtml(state.user.display_name)}</strong><small>${escapeHtml(roleLabel(state.user.role))}</small></div></div><button class="nav-link w-full mt-12" data-action="logout"><span class="nav-icon">↪</span>Выйти</button></div>
    </aside>
    <div class="main-area">
      <header class="topbar"><div class="topbar-left"><button class="mobile-menu" data-action="mobile-menu">☰</button><div><h1 id="topbar-title">${escapeHtml(title)}</h1><div class="topbar-meta" id="topbar-meta">${escapeHtml(subtitle)}</div></div></div><div class="topbar-actions"><button class="notification-button ${critical ? "has-critical" : ""}" data-route="notifications" aria-label="Уведомления">♢${unread ? `<span>${unread > 99 ? "99+" : unread}</span>` : ""}</button><div class="topbar-meta">Защищённая панель · ${escapeHtml(roleLabel(state.user.role))}</div></div></header>
      ${paused ? `<div class="global-stop-banner"><div><strong>Аварийная остановка публикаций включена</strong><span>${escapeHtml(organization.publishing_pause_reason || "Новые публикации заблокированы на уровне организации")}</span></div>${can("owner", "admin") ? `<button class="btn btn-light btn-sm" data-action="publishing-resume">Возобновить</button>` : ""}</div>` : ""}
      ${maintenance ? `<div class="global-stop-banner"><div><strong>Режим обслуживания активен</strong><span>${escapeHtml(organization.maintenance_reason || "Scheduler и отправка Telegram временно заблокированы")}</span></div>${can("owner", "admin") ? `<button class="btn btn-light btn-sm" data-route="changes">Открыть изменения</button>` : ""}</div>` : ""}
      <main class="page" id="page">${loadingPage()}</main>
    </div>
  </div>`;
}

/**
 * Выполнить navigate, явно сохраняя побочные эффекты UI или worker.
 */
async function navigate(route) {
  if (!routeMeta[route]) route = "dashboard";
  const nav = filteredNav().find((item) => item[0] === route);
  if (!nav) route = "dashboard";
  state.route = route;
  if (!root.querySelector(".app-shell")) renderShell();
  root.querySelectorAll(".nav-link[data-route]").forEach((el) => el.classList.toggle("active", el.dataset.route === route));
  const [title, subtitle] = routeMeta[route];
  document.getElementById("topbar-title").textContent = title;
  document.getElementById("topbar-meta").textContent = subtitle;
  document.getElementById("sidebar")?.classList.remove("open");
  const page = document.getElementById("page");
  page.innerHTML = loadingPage();
  try {
    const renderer = pages[route];
    page.innerHTML = await renderer();
    ensureAccessibleFormLabels(page);
  } catch (error) {
    if (error.status === 401) {
      state.user = null;
      renderLogin();
      return;
    }
    page.innerHTML = `<div class="alert alert-danger"><strong>Не удалось загрузить раздел.</strong><br>${escapeHtml(error.message)}</div>`;
  }
}

/**
 * Выполнить loadbase, явно сохраняя побочные эффекты UI или worker.
 */
async function loadBase({ connections = false, destinations = false, templates = false, media = false } = {}) {
  const tasks = [];
  if (connections) tasks.push(api("/connections").then((v) => { state.data.connections = v; }));
  if (destinations) tasks.push(api("/destinations").then((v) => { state.data.destinations = v; }));
  if (templates) tasks.push(api("/templates").then((v) => { state.data.templates = v; }));
  if (media) tasks.push(api("/media").then((v) => { state.data.media = v; }));
  await Promise.all(tasks);
}

const pages = {
  /**
   * Выполнить dashboard, явно сохраняя побочные эффекты UI или worker.
   */
  async dashboard() {
    const summary = await api("/dashboard/summary");
    state.notificationCounts.unread = summary.notifications_unread;
    updateChromeIndicators();
    if (state.data.organization) {
      state.data.organization.publishing_paused = summary.publishing_paused;
      state.data.organization.publishing_pause_reason = summary.publishing_pause_reason;
    }
    const safety = summary.recent_safety_events.length ? summary.recent_safety_events.map((event) => `<div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-content"><strong>${escapeHtml(event.action)}</strong><p>${formatDate(event.created_at)} · ${escapeHtml(event.entity_type || "система")} ${event.details?.code ? `· ${escapeHtml(event.details.code)}` : ""}</p></div></div>`).join("") : `<div class="empty p-28"><p>Предупреждений пока нет</p></div>`;
    return `<div class="page-header"><div><h2>Рабочая сводка</h2><p>Проверяйте подключения, разрешения, утверждения и очередь перед запуском кампаний.</p></div><div class="page-actions">${can("owner", "admin") ? summary.publishing_paused ? `<button class="btn btn-success" data-action="publishing-resume">Возобновить публикации</button>` : `<button class="btn btn-danger" data-action="publishing-pause">Аварийный стоп</button>` : ""}${can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="new-campaign">＋ Новая кампания</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      ${summary.publishing_paused ? `<div class="alert alert-danger mb-18"><strong>Все новые публикации остановлены.</strong> ${escapeHtml(summary.publishing_pause_reason || "Возобновление требует явного действия владельца или администратора.")}</div>` : ""}
      ${state.user.must_change_password ? `<div class="alert alert-warning mb-18"><strong>Смените временный пароль.</strong> Перейдите в раздел «Безопасность» до подключения реального Telegram.</div>` : ""}
      <div class="stats-grid">
        ${statCard("Подключения", summary.connections_active, `${summary.connections_total} всего`, "◉")}
        ${statCard("Разрешённые группы", summary.destinations_confirmed, `${summary.destinations_total} добавлено`, "⌖")}
        ${statCard("Активные кампании", summary.campaigns_active, `${summary.jobs_pending} заданий ожидают`, "▷")}
        ${statCard("Ожидают утверждения", summary.approvals_pending, "запросов", "✓")}
        ${statCard("Новые уведомления", summary.notifications_unread, "операторский центр", "♢")}
        ${statCard("Сегодня отправлено", summary.jobs_sent_today, `${summary.jobs_failed_today} ошибок`, "✓")}
        ${statCard("Открытые диалоги", summary.conversations_open, "Business inbox", "◌")}
        ${statCard("Кандидаты готовы", summary.candidates_ready, "к проверке", "♧")}
      </div>
      <div class="content-grid">
        <section class="panel"><div class="panel-header"><h3>Контроль готовности</h3></div><div class="panel-body"><div class="safety-list">
          ${safetyItem(summary.worker_online ? "✓" : "!", "Worker очереди", summary.worker_online ? `Онлайн${summary.worker_last_seen_at ? `, сигнал ${formatDate(summary.worker_last_seen_at)}` : ""}` : "Нет свежего heartbeat. Кампании не будут доставлены до запуска worker.", summary.worker_online ? "success" : "warning")}
          ${safetyItem("1", "Подтвердите правила каждой группы", "Кампания блокируется, пока назначение не получит статус «Разрешено» с примечанием или ссылкой на правила.")}
          ${safetyItem("2", "Получите актуальное утверждение", "Fingerprint фиксирует текст, маршрут, расписание и правила групп. Любое изменение требует повторного решения.")}
          ${safetyItem("3", "Реагируйте на ограничения", "FloodWait и антиспам останавливают доставку и создают критическое уведомление вместо обхода ограничения.")}
        </div></div></section>
        <section class="panel"><div class="panel-header"><h3>Последние события безопасности</h3>${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-route="audit">Открыть аудит</button>` : ""}</div><div class="panel-body"><div class="timeline">${safety}</div></div></section>
      </div>`;
  },

  /**
   * Выполнить analytics, явно сохраняя побочные эффекты UI или worker.
   */
  async analytics() {
    const range = state.analyticsRange || defaultAnalyticsRange();
    state.analyticsRange = range;
    const query = analyticsQuery(range);
    const [overview, daily] = await Promise.all([
      api(`/analytics/overview?${query}`),
      api(`/analytics/timeseries?${query}`),
    ]);
    const campaignRows = overview.campaigns.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.campaign_name)}</div><div class="cell-sub">${escapeHtml(item.campaign_id)}</div></td><td>${item.total}</td><td>${item.sent}</td><td>${item.failed}</td><td>${item.waiting_review}</td><td>${item.success_rate.toFixed(1)}%</td></tr>`).join("");
    const variantRows = overview.template_variants.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.campaign_name)}</div><div class="cell-sub">вариант ${escapeHtml(item.variant)}</div></td><td><div class="cell-title">${escapeHtml(item.template_name)}</div><div class="cell-sub">${escapeHtml(item.template_id)}</div></td><td>${item.total}</td><td>${item.sent}</td><td>${item.failed}</td><td>${item.waiting_review}</td><td>${item.success_rate.toFixed(1)}%</td></tr>`).join("");
    const funnel = overview.funnel.map((item, index) => {
      const previous = index === 0 ? Math.max(item.count, 1) : Math.max(overview.funnel[index - 1].count, 1);
      return `<div class="analytics-funnel-row"><div><strong>${escapeHtml(item.label)}</strong><span>${item.count}${item.conversion_from_previous === null ? "" : ` · ${item.conversion_from_previous.toFixed(1)}%`}</span></div><progress max="${previous}" value="${Math.min(item.count, previous)}"></progress></div>`;
    }).join("");
    return `<div class="page-header"><div><h2>Аналитика платформы</h2><p>Агрегированные показатели без чтения полного текста переписки и раскрытия контактов.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="analytics-export">⇩ CSV</button><button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      <form class="analytics-filter panel mb-18" id="analytics-filter-form"><div class="panel-body"><div class="analytics-filter-grid"><div class="form-group"><label>С даты</label><input class="input" type="date" name="date_from" value="${attr(range.date_from)}" required></div><div class="form-group"><label>По дату</label><input class="input" type="date" name="date_to" value="${attr(range.date_to)}" required></div><div class="form-group"><label>Часовой пояс</label><input class="input" name="timezone_name" value="${attr(range.timezone_name || overview.timezone_name)}" required></div><div class="form-group analytics-filter-action"><label>&nbsp;</label><button class="btn btn-primary" type="submit">Применить</button></div></div></div></form>
      <div class="stats-grid">
        ${statCard("Отправлено", overview.delivery_sent, `${overview.delivery_success_rate.toFixed(1)}% успешных`, "✓")}
        ${statCard("Ошибки доставки", overview.delivery_failed, `${overview.delivery_waiting_review} требуют проверки`, "!")}
        ${statCard("Новые диалоги", overview.conversations_created, `${overview.inbound_messages} входящих сообщений`, "◌")}
        ${statCard("Кандидаты", overview.candidates_created, `${overview.candidates_ready} готовы`, "♧")}
        ${statCard("Передано оператору", overview.conversations_handoff, `${overview.conversations_open} открыто`, "↗")}
        ${statCard("AI-вызовы", overview.ai_interactions, `${overview.ai_success_rate.toFixed(1)}% успешно`, "✦")}
        ${statCard("Outbox", overview.outbox_events, `${overview.outbox_delivered} доставлено`, "⇥")}
      </div>
      <div class="content-grid analytics-grid">
        <section class="panel"><div class="panel-header"><h3>Динамика по дням</h3><span class="cell-sub">${escapeHtml(range.date_from)} — ${escapeHtml(range.date_to)}</span></div><div class="panel-body">${analyticsTrend(daily)}</div></section>
        <section class="panel"><div class="panel-header"><h3>Воронка обработки</h3></div><div class="panel-body"><div class="analytics-funnel">${funnel || `<p class="cell-sub">За период данных нет</p>`}</div></div></section>
      </div>
      <div class="content-grid analytics-grid mt-18">
        <section class="panel"><div class="panel-header"><h3>Статусы доставки</h3></div><div class="panel-body">${analyticsBreakdown(overview.delivery_statuses)}</div></section>
        <section class="panel"><div class="panel-header"><h3>Статусы кандидатов</h3></div><div class="panel-body">${analyticsBreakdown(overview.candidate_statuses)}</div></section>
      </div>
      <section class="panel mt-18"><div class="panel-header"><h3>Кампании за период</h3></div>${campaignRows ? `<div class="table-wrap"><table><thead><tr><th>Кампания</th><th>Всего</th><th>Отправлено</th><th>Ошибки</th><th>Проверка</th><th>Успех</th></tr></thead><tbody>${campaignRows}</tbody></table></div>` : emptyState("⌁", "Нет запусков", "За выбранный период задания кампаний не создавались.")}</section>
      <section class="panel mt-18"><div class="panel-header"><h3>A/B-варианты доставки</h3><span class="cell-sub">Показывает техническую доставку; отклик оценивается отдельно по данным кандидатов/CRM.</span></div>${variantRows ? `<div class="table-wrap"><table><thead><tr><th>Кампания</th><th>Шаблон</th><th>Всего</th><th>Отправлено</th><th>Ошибки</th><th>Проверка</th><th>Успех</th></tr></thead><tbody>${variantRows}</tbody></table></div>` : emptyState("A/B", "Нет данных по вариантам", "После создания заданий здесь появится разбивка по шаблонам.")}</section>`;
  },

  /**
   * Выполнить connections, явно сохраняя побочные эффекты UI или worker.
   */
  async connections() {
    await loadBase({ connections: true });
    const rows = state.data.connections.map((item) => {
      const operatorActions = can("owner", "admin", "operator")
        ? `${button("Проверить", "connection-health", item.id)}${item.status === "active" ? button("Найти группы", "connection-discover", item.id) : ""}${item.status === "active" ? button("Пауза", "connection-pause", item.id, "warning") : ""}`
        : "";
      const resumeAction = can("owner", "admin") && ["paused", "error"].includes(item.status)
        ? button("Возобновить", "connection-resume", item.id, "success")
        : "";
      return `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${item.kind === "bot" ? "Bot API" : "Пользовательский аккаунт"} · ${escapeHtml(item.telegram_username ? `@${item.telegram_username}` : item.telegram_display_name || "не определён")}</div></td><td>${badge(item.status, connectionStatuses)}</td><td><div class="cell-title">${item.min_interval_seconds} сек.</div><div class="cell-sub">до ${item.daily_cap} в сутки</div></td><td><div class="cell-title">${Math.round(item.destination_cooldown_minutes / 60)} ч.</div><div class="cell-sub">между публикациями в группе</div></td><td><div class="cell-title">${formatDate(item.last_checked_at)}</div><div class="cell-sub">${escapeHtml(item.last_error_code || "ошибок нет")}</div></td><td><div class="actions">${operatorActions}${resumeAction}${can("owner", "admin") ? button("Настройки", "connection-edit", item.id) : ""}${can("owner") && item.status !== "revoked" ? button("Отозвать", "connection-revoke", item.id, "danger") : ""}</div></td></tr>`;
    }).join("");
    return `<div class="page-header"><div><h2>Каналы Telegram</h2><p>Секреты никогда не возвращаются из API и отображаются только как метаданные подключения.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-secondary" data-action="new-user-connection">＋ Аккаунт</button><button class="btn btn-primary" data-action="new-bot-connection">＋ Бот</button>` : ""}</div></div>
      <div class="alert alert-warning mb-18"><strong>Пользовательский режим требует особой осторожности.</strong> Для него принудительно включены ручное утверждение, остановка при FloodWait и минимальный интервал 60 секунд.</div>
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Подключение</th><th>Статус</th><th>Лимиты</th><th>Cooldown</th><th>Проверка</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("◉", "Подключений нет", "Добавьте Telegram-бота или авторизуйте отдельный рабочий аккаунт.", can("owner", "admin") ? `<button class="btn btn-primary" data-action="new-bot-connection">Добавить бота</button>` : "")}</section>`;
  },

  /**
   * Выполнить destinations, явно сохраняя побочные эффекты UI или worker.
   */
  async destinations() {
    await loadBase({ connections: true, destinations: true });
    const connectionNames = Object.fromEntries(state.data.connections.map((x) => [x.id, x.name]));
    const rows = state.data.destinations.map((item) => {
      const validationLabel = item.validated
        ? `проверено${item.validation_expires_at ? ` до ${formatDate(item.validation_expires_at)}` : ""}`
        : "нужна проверка";
      const validationActions = can("owner", "admin", "operator")
        ? button("Проверить", "destination-validate", item.id)
        : "";
      return `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(item.username ? `@${item.username}` : item.telegram_chat_id)}${item.topic_id ? ` · тема ${item.topic_id}` : ""}</div></td><td>${escapeHtml(connectionNames[item.connection_id] || item.connection_id)}</td><td>${badge(item.permission_status, permissionStatuses)}<div class="cell-sub mt-5">${escapeHtml(validationLabel)}</div><div class="cell-sub mt-5">${escapeHtml(destinationPermissionLabel(item))}</div></td><td><div class="cell-title">${item.enabled ? "Включено" : "Отключено"}</div><div class="cell-sub">${item.next_allowed_at ? `после ${formatDate(item.next_allowed_at)}` : "готово"}</div><div class="cell-sub mt-5">${escapeHtml(destinationWindowLabel(item))}</div></td><td><div class="cell-title">${formatDate(item.last_sent_at)}</div><div class="cell-sub">${escapeHtml(item.last_error_code || "ошибок нет")}</div></td><td><div class="actions">${validationActions}${button("История", "destination-validation-history", item.id)}${can("owner", "admin", "operator") ? button("Изменить", "destination-edit", item.id) : ""}${can("owner", "admin") ? button("Удалить", "destination-delete", item.id, "danger") : ""}</div></td></tr>`;
    }).join("");
    return `<div class="page-header"><div><h2>Разрешённые назначения</h2><p>Добавление не означает разрешение: основание публикации фиксируется отдельно и попадает в аудит.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="destination-export">⇩ Экспорт CSV</button>${can("owner", "admin", "operator") ? `<button class="btn btn-secondary" data-action="new-destination-import">⇧ Импорт</button><button class="btn btn-primary" data-action="new-destination">＋ Добавить группу</button>` : ""}</div></div>
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Группа / канал</th><th>Подключение</th><th>Разрешение</th><th>Состояние</th><th>Последняя отправка</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("⌖", "Группы не добавлены", "Добавьте только те группы, где публикация согласована или разрешена правилами.", can("owner", "admin", "operator") ? `<div class="actions"><button class="btn btn-secondary" data-action="new-destination-import">Импортировать список</button><button class="btn btn-primary" data-action="new-destination">Добавить группу</button></div>` : "")}</section>`;
  },

  /**
   * Выполнить media, явно сохраняя побочные эффекты UI или worker.
   */
  async media() {
    await loadBase({ media: true });
    const rows = state.data.media.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.original_name)}</div><div class="cell-sub">SHA-256 ${escapeHtml(item.sha256.slice(0, 16))}…</div></td><td>${escapeHtml(item.content_type)}</td><td>${formatBytes(item.size_bytes)}</td><td>${formatDate(item.created_at)}</td><td>${can("owner", "admin") ? button("Удалить", "media-delete", item.id, "danger") : ""}</td></tr>`).join("");
    return `<div class="page-header"><div><h2>Медиатека</h2><p>Файлы проверяются по размеру, типу и хешу и хранятся вне публичного каталога.</p></div><div class="page-actions">${can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="upload-media">⇧ Загрузить</button>` : ""}</div></div><section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Файл</th><th>Тип</th><th>Размер</th><th>Загружен</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("▧", "Медиафайлов нет", "Загрузите изображение или документ и прикрепите его к шаблону.", can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="upload-media">Загрузить файл</button>` : "")}</section>`;
  },

  /**
   * Выполнить templates, явно сохраняя побочные эффекты UI или worker.
   */
  async templates() {
    await loadBase({ templates: true, media: true });
    const mediaNames = Object.fromEntries(state.data.media.map((x) => [x.id, x.original_name]));
    const rows = state.data.templates.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">ревизия ${item.revision} · ${item.is_active ? "активен" : "отключён"}</div></td><td><div class="cell-title">${escapeHtml(truncate(item.body, 110))}</div><div class="cell-sub">${escapeHtml(item.parse_mode)}${item.media_asset_id ? ` · ${escapeHtml(mediaNames[item.media_asset_id] || "медиа")}` : ""}</div></td><td>${formatDate(item.updated_at)}</td><td><div class="actions">${can("owner", "admin", "operator") ? button("Изменить", "template-edit", item.id) : ""}${can("owner", "admin") ? button("Удалить", "template-delete", item.id, "danger") : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Шаблоны сообщений</h2><p>Изменение содержимого повышает ревизию; уже созданные задания сохраняют снимок текста.</p></div><div class="page-actions">${can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="new-template">＋ Новый шаблон</button>` : ""}</div></div><section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Шаблон</th><th>Содержимое</th><th>Изменён</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("≡", "Шаблонов нет", "Создайте первое объявление и затем используйте его в кампании.", can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="new-template">Создать шаблон</button>` : "")}</section>`;
  },

  /**
   * Выполнить campaigns, явно сохраняя побочные эффекты UI или worker.
   */
  async campaigns() {
    await Promise.all([
      loadBase({ connections: true, destinations: true, templates: true }),
      api("/campaigns").then((value) => { state.data.campaigns = value; }),
      api("/campaigns/approval-requests?status_filter=pending").then((value) => { state.data.approvalRequests = value; }),
    ]);
    const connections = Object.fromEntries(state.data.connections.map((x) => [x.id, x.name]));
    const templates = Object.fromEntries(state.data.templates.map((x) => [x.id, x.name]));
    const campaigns = Object.fromEntries(state.data.campaigns.map((x) => [x.id, x]));
    const pendingByCampaign = Object.fromEntries(state.data.approvalRequests.map((x) => [x.campaign_id, x]));
    /**
     * Выполнить approvalcell, явно сохраняя побочные эффекты UI или worker.
     */
    const approvalCell = (item) => {
      const pending = pendingByCampaign[item.id];
      if (pending) return `${badge("pending", approvalStatuses)}<div class="cell-sub mt-5">${pending.decisions.length}/${pending.required_approvals} решений · до ${formatDate(pending.expires_at)}</div>`;
      if (item.approved_at) return `${badge("approved", approvalStatuses)}<div class="cell-sub mt-5">fingerprint ${escapeHtml((item.approved_fingerprint || "").slice(0, 10))}…</div>`;
      return badge("cancelled", { cancelled: { label: "Не утверждено", cls: "muted" } });
    };
    const rows = state.data.campaigns.map((item) => {
      const pending = pendingByCampaign[item.id];
      const canSubmit = can("owner", "admin", "operator") && ["draft", "paused"].includes(item.status) && !pending;
      const canRun = can("owner", "admin") && Boolean(item.approved_at) && !["completed", "cancelled", "failed"].includes(item.status);
      const rollout = item.rollout_mode === "staged"
        ? `пакеты по ${item.rollout_batch_size} · ${item.rollout_require_checkpoint ? "ручной checkpoint" : "автопереход"}`
        : "стандартный последовательный запуск";
      return `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(connections[item.connection_id] || "подключение")} · ${escapeHtml(templates[item.template_id] || "шаблон")}${item.secondary_template_id ? ` · A/B ${100 - item.secondary_template_weight}/${item.secondary_template_weight}` : ""}</div></td><td>${badge(item.status, campaignStatuses)}<div class="mt-5">${approvalCell(item)}</div></td><td><div class="cell-title">${item.destination_count} назначений</div><div class="cell-sub">${escapeHtml(rollout)}</div><div class="cell-sub">интервал ${item.spacing_seconds} сек. · дубль ${item.duplicate_guard_minutes ? `${item.duplicate_guard_minutes} мин.` : "не проверяется"}</div></td><td><div class="cell-title">${formatDate(item.next_run_at || item.schedule_at)}</div><div class="cell-sub">${item.schedule_type === "once" ? "однократно" : item.schedule_type === "daily" ? "ежедневно" : "еженедельно"}</div></td><td><div class="actions">${button("Предпросмотр", "campaign-preview", item.id)}${can("owner", "admin", "operator") ? button("Preflight", "campaign-preflight", item.id) : ""}${button("Запуски", "campaign-runs", item.id)}${can("owner", "admin", "operator") ? button("Копировать", "campaign-clone", item.id) : ""}${can("owner", "admin", "operator") && ["draft", "paused"].includes(item.status) ? button("Изменить", "campaign-edit", item.id) : ""}${canSubmit ? button("На утверждение", "campaign-submit-approval", item.id, "success") : ""}${canRun ? button("Сейчас", "campaign-run-now", item.id, "primary") : ""}${can("owner", "admin", "operator") && ["scheduled", "running"].includes(item.status) ? button("Пауза", "campaign-pause", item.id, "warning") : ""}${can("owner", "admin", "operator") && item.status === "paused" && item.approved_at ? button("Продолжить", "campaign-resume", item.id, "success") : ""}${can("owner", "admin") && !["completed", "cancelled"].includes(item.status) ? button("Отменить", "campaign-cancel", item.id, "danger") : ""}</div></td></tr>`;
    }).join("");
    const pendingRows = state.data.approvalRequests.map((item) => {
      const campaign = campaigns[item.campaign_id];
      const alreadyDecided = item.decisions.some((decision) => decision.user_id === state.user.id);
      const requesterBlocked = item.require_distinct_requester && item.requested_by_id === state.user.id;
      const decisionActions = can("owner", "admin") && !alreadyDecided && !requesterBlocked ? `${button("Утвердить", "approval-approve", item.id, "success")}${button("Отклонить", "approval-reject", item.id, "danger")}` : requesterBlocked ? `<span class="cell-sub">Нужен независимый администратор</span>` : alreadyDecided ? `<span class="cell-sub">Ваше решение записано</span>` : "—";
      return `<tr><td><div class="cell-title">${escapeHtml(campaign?.name || item.campaign_id)}</div><div class="cell-sub">запрос ${escapeHtml(item.id.slice(0, 8))}</div></td><td><strong>${item.decisions.length}/${item.required_approvals}</strong><progress class="approval-progress" max="100" value="${Math.min(100, Math.round(item.decisions.length / item.required_approvals * 100))}"></progress></td><td>${formatDate(item.expires_at)}</td><td>${decisionActions}</td></tr>`;
    }).join("");
    return `<div class="page-header"><div><h2>Кампании</h2><p>Сначала выполняется сохраняемый preflight, затем утверждение. Пакетный режим удерживает последующие группы до контролируемого checkpoint.</p></div><div class="page-actions">${can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="new-campaign">＋ Новая кампания</button>` : ""}</div></div>
      <div class="alert alert-info mb-18"><strong>Controlled rollout:</strong> для первого пилота используйте пакетный режим по 5 групп с ручным checkpoint и проверкой результата после каждого пакета.</div>
      ${state.data.approvalRequests.length ? `<section class="panel mb-18"><div class="panel-header"><div><h3>Ожидают решения</h3><p class="cell-sub">Для высокорисковых маршрутов может требоваться несколько независимых решений.</p></div></div><div class="table-wrap"><table><thead><tr><th>Кампания</th><th>Прогресс</th><th>Истекает</th><th>Действие</th></tr></thead><tbody>${pendingRows}</tbody></table></div></section>` : ""}
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Кампания</th><th>Статус и утверждение</th><th>Маршрут</th><th>Расписание</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("▷", "Кампаний нет", "Создайте подключение, разрешённую группу и шаблон, затем сформируйте кампанию.", can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="new-campaign">Создать кампанию</button>` : "")}</section>`;
  },

  /**
   * Выполнить pilot, явно сохраняя побочные эффекты UI или worker.
   */
  async pilot() {
    const supportPromise = can("owner", "admin") ? api("/pilot/support-bundles?limit=50") : Promise.resolve([]);
    const [overview, reports, blackouts, campaigns, pilotStage, stageAssessments, pilotCanaries, supportBundles] = await Promise.all([
      api("/pilot/overview"),
      api("/pilot/readiness?limit=100"),
      api("/blackouts"),
      api("/campaigns"),
      api("/pilot/stage"),
      api("/pilot/stage/assessments?limit=50"),
      api("/pilot/canaries?limit=50"),
      supportPromise,
    ]);
    await loadBase({ connections: true, destinations: true });
    state.data.pilotOverview = overview;
    state.data.pilotStage = pilotStage;
    state.data.stageAssessments = stageAssessments;
    state.data.pilotCanaries = pilotCanaries;
    state.data.supportBundles = supportBundles;
    state.data.readinessReports = reports;
    state.data.blackouts = blackouts;
    state.data.campaigns = campaigns;

    const latestByCampaign = {};
    for (const report of reports) if (!latestByCampaign[report.campaign_id]) latestByCampaign[report.campaign_id] = report;
    const connectionNames = Object.fromEntries(state.data.connections.map((item) => [item.id, item.name]));
    const destinationNames = Object.fromEntries(state.data.destinations.map((item) => [item.id, item.title]));
    const currentIndex = pilotStageOrder.indexOf(pilotStage.current_stage);
    const latestAssessment = pilotStage.latest_assessment;
    const assessmentCurrent = latestAssessment && latestAssessment.requested_stage === pilotStage.next_stage && new Date(latestAssessment.expires_at) > new Date();
    const canAdvance = can("owner", "admin") && assessmentCurrent && latestAssessment.status === "passed";
    const stageActions = `${can("owner", "admin") && pilotStage.next_stage ? `<button class="btn btn-primary" data-action="pilot-stage-assess">Проверить этап ${escapeHtml(pilotStageLabels[pilotStage.next_stage])}</button>` : ""}${canAdvance ? `<button class="btn btn-success" data-action="pilot-stage-advance" data-id="${attr(latestAssessment.id)}">Перейти на этап</button>` : ""}${can("owner", "admin") && currentIndex > 0 ? `<button class="btn btn-warning" data-action="pilot-stage-lower">Понизить этап</button>` : ""}${can("owner", "admin") ? `<button class="btn btn-secondary" data-action="pilot-canary-new">Служебная canary-проверка</button><button class="btn btn-secondary" data-action="pilot-support-bundle-new">Диагностический архив</button>` : ""}`;

    const assessmentRows = stageAssessments.map((item) => `<tr><td><div class="cell-title">${escapeHtml(pilotStageLabels[item.current_stage])} → ${escapeHtml(pilotStageLabels[item.requested_stage])}</div><div class="cell-sub">${formatDate(item.created_at)} · до ${formatDate(item.expires_at)}</div></td><td>${badge(item.status, readinessStatuses)}</td><td><div class="cell-title">${item.summary?.passed_checks || 0} пройдено · ${item.summary?.warning_checks || 0} предупреждений</div><div class="cell-sub">${item.blockers?.length || 0} блокирующих причин · fingerprint ${escapeHtml(item.fingerprint.slice(0, 12))}…</div></td><td>${button("Открыть", "pilot-stage-assessment-open", item.id)}</td></tr>`).join("");

    const canaryRows = pilotCanaries.map((item) => `<tr><td><div class="cell-title">${escapeHtml(destinationNames[item.destination_id] || item.destination_id)}</div><div class="cell-sub">${escapeHtml(connectionNames[item.connection_id] || item.connection_id)}</div></td><td>${badge(item.status, pilotCanaryStatuses)}${item.is_fake ? `<div class="cell-sub mt-5">fake mode — не является live-доказательством</div>` : ""}</td><td><div class="cell-title">${escapeHtml(item.marker)}</div><div class="cell-sub">SHA-256 ${escapeHtml(item.body_sha256.slice(0, 12))}…${item.telegram_message_id ? ` · message ${escapeHtml(item.telegram_message_id)}` : ""}</div></td><td><div class="cell-title">${formatDate(item.completed_at || item.started_at)}</div><div class="cell-sub">${escapeHtml(item.error_code || item.error_message || "без ошибок")}</div></td></tr>`).join("");

    const bundleRows = supportBundles.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.id.slice(0, 8))}</div><div class="cell-sub">${item.sections.length} разделов · ${formatBytes(item.size_bytes)}</div></td><td>${badge(item.status, supportBundleStatuses)}</td><td><div class="cell-title">до ${formatDate(item.expires_at)}</div><div class="cell-sub">${item.sha256 ? `SHA-256 ${escapeHtml(item.sha256.slice(0, 12))}…` : escapeHtml(item.error_message || "—")}</div></td><td><div class="actions">${item.status === "ready" ? button("Скачать", "pilot-support-bundle-download", item.id, "primary") : ""}${item.status !== "deleted" ? button("Удалить", "pilot-support-bundle-delete", item.id, "danger") : ""}</div></td></tr>`).join("");

    const readinessRows = campaigns.filter((item) => !["completed", "cancelled", "failed"].includes(item.status)).map((item) => {
      const report = latestByCampaign[item.id];
      const reportCell = report
        ? `${badge(report.status, readinessStatuses)}<div class="cell-sub mt-5">создан ${formatDate(report.created_at)} · действует до ${formatDate(report.expires_at)}</div><div class="cell-sub">${report.summary?.passed_checks || 0} пройдено · ${report.summary?.warning_checks || 0} предупреждений · ${report.summary?.blocked_checks || 0} блокировок</div>`
        : `<span class="badge badge-muted">Не проверялась</span><div class="cell-sub mt-5">Отчёт готовности отсутствует</div>`;
      return `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(connectionNames[item.connection_id] || item.connection_id)} · ${item.destination_count} назначений</div></td><td>${badge(item.status, campaignStatuses)}</td><td>${reportCell}</td><td><div class="actions">${can("owner", "admin", "operator") ? button("Проверить готовность", "pilot-readiness", item.id, report?.status === "blocked" ? "warning" : "primary") : ""}${report ? button("Открыть отчёт", "pilot-readiness-open", report.id) : ""}</div></td></tr>`;
    }).join("");
    const attentionRows = overview.attention.map((item) => {
      const validationAction = can("owner", "admin", "operator") ? button("Проверить", "destination-validate", item.destination_id) : "";
      return `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(destinationNames[item.destination_id] || item.destination_id)}</div></td><td>${badge(item.severity, { warning: { label: "Предупреждение", cls: "warning" }, blocked: { label: "Блокирует", cls: "danger" } })}</td><td>${escapeHtml(item.issue)}</td><td>${formatDate(item.due_at)}</td><td><div class="actions">${validationAction}${button("История", "destination-validation-history", item.destination_id)}</div></td></tr>`;
    }).join("");
    const blackoutRows = blackouts.map((item) => {
      const target = item.scope === "connection" ? connectionNames[item.connection_id] : item.scope === "destination" ? destinationNames[item.destination_id] : "Вся организация";
      const windowText = item.kind === "one_time"
        ? `${formatDate(item.starts_at)} — ${formatDate(item.ends_at)}`
        : `${(item.weekdays || []).map((day) => ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][day]).join(", ")} · ${String(item.start_time || "").slice(0, 5)}–${String(item.end_time || "").slice(0, 5)} · ${item.timezone_name}`;
      return `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(item.reason)}</div></td><td><div class="cell-title">${escapeHtml(blackoutScopes[item.scope] || item.scope)}</div><div class="cell-sub">${escapeHtml(target || "—")}</div></td><td><div class="cell-title">${escapeHtml(blackoutKinds[item.kind] || item.kind)}</div><div class="cell-sub">${escapeHtml(windowText)}</div></td><td>${item.enabled ? badge("active", { active: { label: "Включён", cls: "success" } }) : badge("disabled", { disabled: { label: "Отключён", cls: "muted" } })}</td><td><div class="actions">${can("owner", "admin") ? `${button("Изменить", "blackout-edit", item.id)}${button(item.enabled ? "Отключить" : "Включить", "blackout-toggle", item.id, item.enabled ? "warning" : "success")}${button("Удалить", "blackout-delete", item.id, "danger")}` : "—"}</div></td></tr>`;
    }).join("");

    return `<div class="page-header"><div><h2>Production Pilot</h2><p>Масштаб публикаций повышается только после live-canary, подтверждённого результата предыдущего этапа и сохраняемой оценки.</p></div><div class="page-actions">${can("owner", "admin", "operator") && overview.destinations_validation_due ? `<button class="btn btn-secondary" data-action="pilot-validate-due">↻ Перепроверить просроченные</button>` : ""}${can("owner", "admin") ? `<button class="btn btn-primary" data-action="new-blackout">＋ Запрет публикаций</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      <div class="alert alert-info mb-18"><strong>Контролируемое масштабирование:</strong> локальная проверка не даёт допуска к live-публикациям. Каждый следующий этап требует свежего canary-сообщения в собственной разрешённой служебной группе и доказательства успешного предыдущего запуска.</div>
      <section class="panel mb-18"><div class="panel-header"><div><h3>Сертификация масштаба</h3><p class="cell-sub">Текущий лимит применяется в preview, scheduler и непосредственно перед сетевым запросом Telegram.</p></div><div class="actions">${stageActions}</div></div><div class="stats-grid m-18">
        ${statCard("Текущий этап", pilotStageLabels[pilotStage.current_stage], `не более ${pilotStage.current_limit} назначений`, "◫")}
        ${statCard("Следующий этап", pilotStage.next_stage ? pilotStageLabels[pilotStage.next_stage] : "Финальный", pilotStage.next_limit !== null ? `лимит ${pilotStage.next_limit}` : "100 групп", "⇥")}
        ${statCard("Live canary", pilotStage.successful_real_canary_at ? "Подтверждена" : "Нет", pilotStage.successful_real_canary_at ? formatDate(pilotStage.successful_real_canary_at) : "fake mode не учитывается", "✓")}
        ${statCard("Enforcement", pilotStage.enforcement_required ? "Обязателен" : "Dev mode", pilotStage.enforcement_required ? "обход запрещён" : "в production должен быть включён", "⌁")}
      </div>${assessmentRows ? `<div class="table-wrap"><table><thead><tr><th>Переход</th><th>Результат</th><th>Проверки</th><th>Действие</th></tr></thead><tbody>${assessmentRows}</tbody></table></div>` : emptyState("◫", "Оценок этапа нет", "Выполните служебную canary-проверку, затем создайте оценку следующего этапа.")}</section>
      <section class="panel mb-18"><div class="panel-header"><div><h3>Служебные canary-проверки</h3><p class="cell-sub">Фиксированный текст, один Telegram-запрос, без автоматического повтора и пользовательского рекламного текста.</p></div>${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-action="pilot-canary-new">Новая проверка</button>` : ""}</div>${canaryRows ? `<div class="table-wrap"><table><thead><tr><th>Назначение</th><th>Статус</th><th>Маркер</th><th>Результат</th></tr></thead><tbody>${canaryRows}</tbody></table></div>` : emptyState("✓", "Canary-проверок нет", "Используйте только собственную служебную группу с подтверждённым разрешением.")}</section>
      ${can("owner", "admin") ? `<section class="panel mb-18"><div class="panel-header"><div><h3>Безопасная диагностика</h3><p class="cell-sub">Архив содержит обезличенные статусы и контрольные суммы, но не секреты, сообщения, телефоны, email или Telegram-сессии.</p></div><button class="btn btn-secondary btn-sm" data-action="pilot-support-bundle-new">Создать архив</button></div>${bundleRows ? `<div class="table-wrap"><table><thead><tr><th>Архив</th><th>Статус</th><th>Хранение</th><th>Действия</th></tr></thead><tbody>${bundleRows}</tbody></table></div>` : emptyState("▧", "Диагностических архивов нет", "Создайте временный пакет для технической поддержки; он автоматически истечёт.")}</section>` : ""}
      <div class="stats-grid">
        ${statCard("Активные подключения", overview.active_connections, "Telegram", "◉")}
        ${statCard("Назначения", overview.destinations_total, `${overview.destinations_validation_due} требуют проверки`, "⌖")}
        ${statCard("Разрешения истекли", overview.permissions_expired, `${overview.permissions_expiring} скоро истекут`, "!")}
        ${statCard("Активные запреты", overview.active_blackouts, "сейчас", "◫")}
        ${statCard("Worker", overview.worker_fresh ? "Online" : "Offline", overview.worker_fresh ? "heartbeat актуален" : "нужна проверка", "⇄")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Готовность кампаний</h3><p class="cell-sub">Отчёт создаётся после утверждения и всегда запускает новый сохраняемый preflight.</p></div></div>${readinessRows ? `<div class="table-wrap"><table><thead><tr><th>Кампания</th><th>Статус</th><th>Последний отчёт</th><th>Действия</th></tr></thead><tbody>${readinessRows}</tbody></table></div>` : emptyState("◫", "Нет кампаний для пилота", "Создайте и утвердите кампанию в разделе «Кампании».")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Требует внимания</h3><p class="cell-sub">Сроки проверок Telegram и разрешений групп.</p></div></div>${attentionRows ? `<div class="table-wrap"><table><thead><tr><th>Назначение</th><th>Уровень</th><th>Причина</th><th>Срок</th><th>Действие</th></tr></thead><tbody>${attentionRows}</tbody></table></div>` : `<div class="alert alert-success m-18"><strong>Критичных сроков нет.</strong> Все назначения имеют актуальную проверку доступа и действующие разрешения.</div>`}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Операционный календарь</h3><p class="cell-sub">Разовые и еженедельные периоды, когда публикации автоматически откладываются.</p></div>${can("owner", "admin") ? `<button class="btn btn-primary btn-sm" data-action="new-blackout">Добавить окно</button>` : ""}</div>${blackoutRows ? `<div class="table-wrap"><table><thead><tr><th>Правило</th><th>Область</th><th>Окно</th><th>Статус</th><th>Действия</th></tr></thead><tbody>${blackoutRows}</tbody></table></div>` : emptyState("◫", "Запретов нет", "Добавьте технические окна, тихие часы или периоды ручной проверки.", can("owner", "admin") ? `<button class="btn btn-primary" data-action="new-blackout">Создать правило</button>` : "")}</section>`;
  },

  /**
   * Выполнить artifacttrust, явно сохраняя побочные эффекты UI или worker.
   */
  async artifactTrust() {
    const [keys, bundles, supportBundles] = await Promise.all([
      api("/artifact-signing-keys"),
      api("/configuration-bundles?limit=100"),
      can("owner", "admin") ? api("/pilot/support-bundles?limit=50") : Promise.resolve([]),
    ]);
    state.data.signingKeys = keys;
    state.data.configurationBundles = bundles;
    state.data.supportBundles = supportBundles;
    const active = keys.filter((item) => item.status === "active");
    const trusted = active.filter((item) => item.trusted_for_import);
    const defaultKey = active.find((item) => item.is_default && item.has_private_key);
    const keyRows = keys.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub"><code>${escapeHtml(item.key_id)}</code></div></td><td>${badge(item.status, artifactKeyStatuses)}${item.is_default ? `<div class="cell-sub mt-5">основной ключ подписи</div>` : ""}</td><td><div class="cell-title">${item.has_private_key ? "Локальная приватная часть" : "Только публичный ключ"}</div><div class="cell-sub">${item.trusted_for_import ? "доверен для импорта" : "не доверен для импорта"}</div></td><td><div class="cell-title"><code>${escapeHtml(item.fingerprint.slice(0, 20))}…</code></div><div class="cell-sub">создан ${formatDate(item.created_at)}</div></td><td><div class="actions">${button("Публичный ключ", "artifact-key-public", item.id)}${can("owner", "admin") && item.status === "active" && item.has_private_key && !item.is_default ? button("Сделать основным", "artifact-key-default", item.id, "success") : ""}${can("owner", "admin") && item.status === "active" ? button("Изменить доверие", "artifact-key-trust", item.id) : ""}${can("owner", "admin") && item.status === "active" ? button("Отозвать", "artifact-key-revoke", item.id, "danger") : ""}</div></td></tr>`).join("");
    const artifactRows = [
      ...bundles.map((item) => ({
        id: item.id,
        name: item.filename,
        kind: item.kind === "export" ? "Конфигурация · экспорт" : "Конфигурация · импорт",
        status: item.signature_status,
        fingerprint: item.signer_fingerprint,
        created_at: item.created_at,
        sha256: item.sha256,
      })),
      ...supportBundles.map((item) => ({
        id: item.id,
        name: `Диагностический архив ${item.id.slice(0, 8)}`,
        kind: "Support bundle",
        status: item.signature_status,
        fingerprint: item.signer_fingerprint,
        created_at: item.created_at,
        sha256: item.sha256,
      })),
    ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 100).map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(item.kind)}</div></td><td>${badge(item.status, artifactSignatureStatuses)}</td><td><div class="cell-title">${item.fingerprint ? `<code>${escapeHtml(item.fingerprint.slice(0, 20))}…</code>` : "—"}</div><div class="cell-sub">${formatDate(item.created_at)}</div></td><td><div class="cell-sub">SHA-256 ${item.sha256 ? `${escapeHtml(item.sha256.slice(0, 20))}…` : "—"}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Подписи и доверие</h2><p>Ed25519 подтверждает происхождение и неизменность конфигурационных архивов, диагностических пакетов и актов приёмки. Приватные ключи никогда не выводятся через API.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="artifact-key-generate">＋ Новый ключ</button><button class="btn btn-secondary" data-action="artifact-key-import">Импорт публичного ключа</button>` : ""}<button class="btn btn-secondary" data-action="artifact-verify">Проверить файл</button><button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      <div class="alert alert-info mb-18"><strong>Модель доверия:</strong> корректная подпись доказывает владение приватным ключом, но доверенной она становится только после явного добавления fingerprint в текущую организацию. Отозванный ключ блокирует импорт даже при математически верной подписи.</div>
      <div class="stats-grid">
        ${statCard("Активные ключи", active.length, `${keys.length} всего`, "⌘")}
        ${statCard("Основной ключ", defaultKey ? "Настроен" : "Не задан", defaultKey ? `${defaultKey.fingerprint.slice(0, 12)}…` : "экспорт будет без подписи в optional mode", defaultKey ? "✓" : "!")}
        ${statCard("Доверенные ключи", trusted.length, "для входящих архивов", "◇")}
        ${statCard("Подписанные артефакты", [...bundles, ...supportBundles].filter((item) => item.signature_status && item.signature_status !== "unsigned").length, `${bundles.length + supportBundles.length} учтено`, "▧")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Ключи организации</h3><p class="cell-sub">Локальная приватная часть хранится только в AES-GCM зашифрованном виде и участвует в ротации master key.</p></div>${can("owner", "admin") ? `<div class="actions"><button class="btn btn-primary btn-sm" data-action="artifact-key-generate">Создать</button><button class="btn btn-secondary btn-sm" data-action="artifact-key-import">Добавить доверенный ключ</button></div>` : ""}</div>${keyRows ? `<div class="table-wrap"><table><thead><tr><th>Ключ</th><th>Статус</th><th>Назначение</th><th>Fingerprint</th><th>Действия</th></tr></thead><tbody>${keyRows}</tbody></table></div>` : emptyState("⌘", "Ключей пока нет", "Создайте локальный ключ подписи или добавьте публичный ключ доверенного источника.", can("owner", "admin") ? `<button class="btn btn-primary" data-action="artifact-key-generate">Создать ключ</button>` : "")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Последние артефакты</h3><p class="cell-sub">Статус сохраняется в базе при создании или импорте. Независимую проверку можно выполнить загруженным файлом или scripts/verify_artifact.py.</p></div><button class="btn btn-secondary btn-sm" data-action="artifact-verify">Проверить файл</button></div>${artifactRows ? `<div class="table-wrap"><table><thead><tr><th>Артефакт</th><th>Подпись</th><th>Подписант</th><th>Контрольная сумма</th></tr></thead><tbody>${artifactRows}</tbody></table></div>` : emptyState("▧", "Артефактов пока нет", "Экспортируйте конфигурацию, создайте support bundle или акт приёмки.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Автономная проверка</h3><p class="cell-sub">Работает без API и базы данных. Передайте fingerprint или PEM публичного ключа, чтобы проверить не только подпись, но и доверие источнику.</p></div></div><div class="panel-body"><div class="code-box">python scripts/verify_artifact.py artifact.zip \\
  --trusted-public-key trusted-ed25519.pem \\
  --require-trusted --json</div></div></section>`;
  },

  /**
   * Выполнить releases, явно сохраняя побочные эффекты UI или worker.
   */
  async releases() {
    const items = await api("/release-attestations");
    state.data.releaseAttestations = items;
    const rows = items.map((item) => `<tr><td><div class="cell-title">TeleFlow ${escapeHtml(item.version)}</div><div class="cell-sub">${item.source_commit ? `commit ${escapeHtml(item.source_commit.slice(0, 16))}…` : "commit не указан"}</div></td><td>${badge(item.signature_status, artifactSignatureStatuses)}</td><td><code>${escapeHtml(item.payload_sha256.slice(0, 20))}…</code><div class="cell-sub">${Number(item.payload?.dependency_count || 0)} runtime dependencies</div></td><td>${formatDate(item.verified_at || item.created_at)}</td><td>${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-action="release-verify" data-id="${attr(item.id)}">Проверить</button>` : "—"}</td></tr>`).join("");
    return `<div class="page-header"><div><h2>Доверие к релизам</h2><p>Подписанный Ed25519 provenance связывает версию, release manifest и перечень зависимостей с доверенным ключом организации.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="release-local">Подписать текущую сборку</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      <div class="alert alert-info mb-18"><strong>Production gate:</strong> upgrade change request может быть привязан к release attestation. При обязательной политике обновление не начнётся без доверенной подписи, соответствующей целевой версии.</div>
      <section class="panel"><div class="panel-header"><div><h3>Зарегистрированные сборки</h3><p class="cell-sub">Payload содержит hashes BUILD_INFO, MANIFEST.sha256 и requirements, но не секреты и не содержимое пользовательских данных.</p></div></div>${rows ? `<div class="table-wrap"><table><thead><tr><th>Релиз</th><th>Подпись</th><th>Provenance</th><th>Проверен</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("◉", "Сборок пока нет", "Создайте основной Ed25519-ключ и подпишите текущий релиз.", can("owner", "admin") ? `<button class="btn btn-primary" data-action="release-local">Подписать текущую сборку</button>` : "")}</section>`;
  },

  /**
   * Выполнить supplychain, явно сохраняя побочные эффекты UI или worker.
   */
  async supplyChain() {
    const [attestations, policy, assessments, transparency, verification] = await Promise.all([
      api("/release-attestations"),
      api("/supply-chain/policy"),
      api("/supply-chain/assessments?limit=200"),
      api("/supply-chain/transparency?limit=500"),
      api("/supply-chain/transparency/verify"),
    ]);
    state.data.releaseAttestations = attestations;
    state.data.dependencyPolicy = policy;
    state.data.dependencyAssessments = assessments;
    state.data.transparencyEvents = transparency;
    state.data.transparencyVerification = verification;
    const latestEvent = new Map();
    transparency.forEach((item) => { if (!latestEvent.has(item.release_attestation_id)) latestEvent.set(item.release_attestation_id, item); });
    const latestAssessment = new Map();
    assessments.forEach((item) => { if (!latestAssessment.has(item.release_attestation_id)) latestAssessment.set(item.release_attestation_id, item); });
    const releaseRows = attestations.map((item) => {
      const event = latestEvent.get(item.id);
      const assessment = latestAssessment.get(item.id);
      const published = event?.event_type === "published";
      return `<tr><td><div class="cell-title">TeleFlow ${escapeHtml(item.version)}</div><div class="cell-sub">${item.source_commit ? `commit ${escapeHtml(item.source_commit.slice(0, 16))}…` : "commit не указан"}</div></td><td>${badge(item.signature_status, artifactSignatureStatuses)}<div class="cell-sub"><code>${escapeHtml(item.payload_sha256.slice(0, 16))}…</code></div></td><td>${event ? badge(event.event_type, transparencyEventStatuses) : badge("не опубликован")}<div class="cell-sub">${event ? `sequence ${event.sequence}` : "нет записи"}</div></td><td>${assessment ? `${badge(assessment.status, readinessStatuses)}<div class="cell-sub">${escapeHtml(dependencyReportKinds[assessment.report_kind] || assessment.report_kind)} · до ${formatDate(assessment.expires_at)}</div>` : `<span class="cell-sub">нет assessment</span>`}</td><td><div class="actions">${button("SBOM", "supply-sbom-release", item.id)}${can("owner", "admin") ? `${button("Inventory", "supply-inventory", item.id)}${button("Scan", "supply-assessment-new", item.id, "primary")}${published ? button("Отозвать", "supply-withdraw", item.id, "danger") : button("Опубликовать", "supply-publish", item.id, "success")}` : ""}</div></td></tr>`;
    }).join("");
    const assessmentRows = assessments.map((item) => {
      const release = attestations.find((entry) => entry.id === item.release_attestation_id);
      const severity = `C:${item.critical_count} H:${item.high_count} M:${item.medium_count}`;
      const evidence = item.blockers?.[0] || item.warnings?.[0] || "Политика выполнена";
      return `<tr><td><div class="cell-title">TeleFlow ${escapeHtml(release?.version || "—")}</div><div class="cell-sub">${escapeHtml(item.scanner_name)} ${escapeHtml(item.scanner_version || "")}</div></td><td>${badge(item.status, readinessStatuses)}<div class="cell-sub">${escapeHtml(severity)}</div></td><td>${badge(item.signature_status, artifactSignatureStatuses)}<div class="cell-sub"><code>${escapeHtml(item.report_sha256.slice(0, 16))}…</code></div></td><td><div class="cell-sub">${escapeHtml(evidence)}</div><div class="cell-sub">действует до ${formatDate(item.expires_at)}</div></td><td><div class="actions">${button("Проверить", "supply-assessment-verify", item.id)}${button("SBOM", "supply-sbom", item.id)}</div></td></tr>`;
    }).join("");
    const transparencyRows = transparency.map((item) => `<tr><td>#${item.sequence}</td><td>${badge(item.event_type, transparencyEventStatuses)}</td><td><div class="cell-title">TeleFlow ${escapeHtml(item.payload?.version || "—")}</div><div class="cell-sub">${escapeHtml(item.payload?.reason || "без комментария")}</div></td><td><code>${escapeHtml(item.entry_hash.slice(0, 18))}…</code><div class="cell-sub">prev ${escapeHtml(item.previous_hash.slice(0, 12))}…</div></td><td>${badge(item.signature_status, artifactSignatureStatuses)}<div class="cell-sub">${formatDate(item.created_at)}</div></td></tr>`).join("");
    const denied = (policy.denied_packages || []).join(", ") || "не заданы";
    return `<div class="page-header"><div><h2>Поставка и зависимости</h2><p>Подписанные dependency reports, CycloneDX SBOM и append-only журнал допуска релизов.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="supply-assessment-new">Добавить scan</button><button class="btn btn-secondary" data-action="supply-policy-edit">Политика</button>` : ""}<button class="btn btn-secondary" data-action="supply-transparency-verify">Проверить цепочку</button><button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      <div class="alert ${verification.valid ? "alert-info" : "alert-danger"} mb-18"><strong>Release transparency:</strong> ${verification.valid ? `цепочка подтверждена, записей ${verification.entry_count}, активных релизов ${verification.active_release_count}.` : `обнаружено нарушений: ${escapeHtml((verification.errors || []).join("; "))}`}</div>
      <div class="stats-grid">
        ${statCard("Опубликованные релизы", verification.active_release_count, `${verification.entry_count} событий`, "⛓")}
        ${statCard("Assessments", assessments.length, `${assessments.filter((item) => item.status === "blocked").length} заблокировано`, "◇")}
        ${statCard("Critical / High", assessments.reduce((sum, item) => sum + item.critical_count, 0), `${assessments.reduce((sum, item) => sum + item.high_count, 0)} high`, "!")}
        ${statCard("TTL отчёта", `${policy.report_ttl_hours} ч`, policy.require_vulnerability_scan ? "scan обязателен" : "inventory допускается", "◷")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Политика зависимостей</h3><p class="cell-sub">Текущая политика повторно применяется при каждой проверке change request.</p></div>${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-action="supply-policy-edit">Изменить</button>` : ""}</div><div class="panel-body"><div class="details-grid"><div><span>Точные версии</span><strong>${policy.require_exact_pins ? "обязательны" : "предупреждение"}</strong></div><div><span>Vulnerability scan</span><strong>${policy.require_vulnerability_scan ? "обязателен" : "не обязателен"}</strong></div><div><span>Доверенная подпись</span><strong>${policy.require_trusted_report ? "обязательна" : "не обязательна"}</strong></div><div><span>Пороги</span><strong>C ${policy.max_critical} · H ${policy.max_high} · M ${policy.max_medium}</strong></div><div class="full"><span>Запрещённые пакеты</span><strong>${escapeHtml(denied)}</strong></div></div></div></section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Релизы и допуск</h3><p class="cell-sub">Опубликовать можно только доверенный release attestation. Один активный attestation на версию.</p></div></div>${releaseRows ? `<div class="table-wrap"><table><thead><tr><th>Релиз</th><th>Provenance</th><th>Transparency</th><th>Последняя оценка</th><th>Действия</th></tr></thead><tbody>${releaseRows}</tbody></table></div>` : emptyState("⛓", "Нет release attestations", "Сначала подпишите текущую сборку в разделе доверия к релизам.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Dependency assessments</h3><p class="cell-sub">Evidence immutable: изменение отчёта, policy snapshot, SBOM или release attestation переводит оценку в blocked.</p></div></div>${assessmentRows ? `<div class="table-wrap"><table><thead><tr><th>Релиз и scanner</th><th>Результат</th><th>Подпись</th><th>Причина</th><th>Действия</th></tr></thead><tbody>${assessmentRows}</tbody></table></div>` : emptyState("◇", "Оценок пока нет", "Создайте inventory assessment или импортируйте нормализованный результат vulnerability scanner.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Transparency log</h3><p class="cell-sub">Каждая запись связана SHA-256 с предыдущей и отдельно подписана Ed25519.</p></div></div>${transparencyRows ? `<div class="table-wrap"><table><thead><tr><th>Sequence</th><th>Событие</th><th>Релиз</th><th>Hash-chain</th><th>Подпись</th></tr></thead><tbody>${transparencyRows}</tbody></table></div>` : emptyState("⛓", "Журнал пуст", "Опубликуйте первый доверенный release attestation.")}</section>`;
  },

  /**
   * Выполнить changes, явно сохраняя побочные эффекты UI или worker.
   */
  async changes() {
    const [items, organization] = await Promise.all([api("/changes"), api("/organization")]);
    state.data.changes = items; state.data.organization = organization;
    const statusMap = {draft:{label:"Черновик",cls:"muted"},approved:{label:"Утверждено",cls:"info"},in_progress:{label:"В работе",cls:"warning"},completed:{label:"Завершено",cls:"success"},failed:{label:"Ошибка",cls:"danger"},cancelled:{label:"Отменено",cls:"muted"}};
    const rows = items.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(item.change_type)} · ${escapeHtml(item.current_version || "—")} → ${escapeHtml(item.target_version || "—")}</div></td><td>${badge(item.status,statusMap)}</td><td><div class="cell-sub">${escapeHtml(item.risk_summary)}</div></td><td><div class="actions">${item.status==="draft"?button("Утвердить","change-approve",item.id):""}${item.status==="approved"?button("Начать","change-start",item.id,"primary"):""}${item.status==="in_progress"?button("Завершить","change-complete",item.id,"success"):""}</div></td></tr>`).join("");
    const maintenance = organization.maintenance_mode;
    return `<div class="page-header"><div><h2>Управление изменениями</h2><p>Обновления и инфраструктурные изменения выполняются через независимое одобрение, режим обслуживания и сохраняемые проверки.</p></div><div class="page-actions"><button class="btn btn-primary" data-action="change-new">＋ Изменение</button><button class="btn btn-${maintenance?"success":"warning"}" data-action="maintenance-${maintenance?"stop":"start"}">${maintenance?"Завершить обслуживание":"Режим обслуживания"}</button><button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>${maintenance?`<div class="alert alert-warning mb-18"><strong>Maintenance mode активен.</strong> ${escapeHtml(organization.maintenance_reason || "Публикации временно заблокированы.")}</div>`:""}<section class="panel"><div class="panel-header"><div><h3>Change requests</h3><p class="cell-sub">Автор не может сам утвердить собственное изменение.</p></div></div>${rows?`<div class="table-wrap"><table><thead><tr><th>Изменение</th><th>Статус</th><th>Риск</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>`:emptyState("∆","Изменений нет","Создайте change request перед обновлением или миграцией.")}</section>`;
  },

  /**
   * Выполнить recovery, явно сохраняя побочные эффекты UI или worker.
   */
  async recovery() {
    const [status, backups, drills] = await Promise.all([
      api("/recovery/status"),
      api("/recovery/backups?limit=100"),
      api("/recovery/drills?limit=100"),
    ]);
    state.data.recoveryStatus = status;
    state.data.recoveryBackups = backups;
    state.data.recoveryDrills = drills;
    const policy = status.policy;
    const latestBackup = status.latest_backup;
    const latestDrill = status.latest_drill;
    const checkRows = (status.checks || []).map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title || item.code)}</div><div class="cell-sub"><code>${escapeHtml(item.code)}</code></div></td><td>${badge(item.status, readinessCheckStatuses)}</td><td>${escapeHtml(item.message)}</td></tr>`).join("");
    const backupRows = backups.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.artifact_filename)}</div><div class="cell-sub"><code>${escapeHtml(item.backup_id)}</code></div></td><td>${badge(item.status, recoveryBackupStatuses)}<div class="cell-sub mt-5">${badge(item.signature_status, artifactSignatureStatuses)}</div></td><td><div class="cell-title">${formatDate(item.backup_created_at)}</div><div class="cell-sub">${escapeHtml(item.database_kind)} · ${formatBytes(item.artifact_size_bytes)} · ${item.artifact_encrypted ? "зашифрован" : "без шифрования"}</div></td><td><div class="cell-title"><code>${escapeHtml(item.artifact_sha256.slice(0, 20))}…</code></div><div class="cell-sub">${item.verified_at ? `проверен ${formatDate(item.verified_at)}` : "требуется recovery_verify или restore drill"}</div></td></tr>`).join("");
    const drillRows = drills.map((item) => `<tr><td><div class="cell-title"><code>${escapeHtml(item.drill_id)}</code></div><div class="cell-sub">backup ${escapeHtml(item.backup_id)}</div></td><td>${badge(item.status, recoveryDrillStatuses)}<div class="cell-sub mt-5">${escapeHtml(recoveryDrillModes[item.mode] || item.mode)}</div></td><td><div class="cell-title">${formatDate(item.completed_at)}</div><div class="cell-sub">${item.duration_seconds} сек. · RTO ${item.rto_met ? "соблюдён" : "не соблюдён"}</div></td><td><div class="cell-title">${badge(item.signature_status, artifactSignatureStatuses)}</div><div class="cell-sub">${escapeHtml((item.blockers || []).join("; ") || (item.warnings || []).join("; ") || "без блокировок")}</div></td></tr>`).join("");
    const statusClass = status.status === "blocked" ? "alert-danger" : status.status === "warning" ? "alert-warning" : "alert-success";
    return `<div class="page-header"><div><h2>Recovery Assurance</h2><p>Панель хранит только подписанные receipts и результаты drill. Backup-файлы создаются и проверяются отдельными CLI-командами, а восстановление никогда не направляется в рабочую базу через веб.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="recovery-policy-edit">Политика RPO/RTO</button><button class="btn btn-secondary" data-action="recovery-backup-import">Импорт backup receipt</button><button class="btn btn-secondary" data-action="recovery-drill-import">Импорт drill receipt</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      <div class="alert ${statusClass} mb-18"><strong>${escapeHtml(readinessStatuses[status.status]?.label || status.status)}.</strong> ${status.blockers.length} блокировок, ${status.warnings.length} предупреждений. ${status.blockers.length ? escapeHtml(status.blockers[0]) : "Подписанные доказательства проверяются относительно политики организации."}</div>
      <div class="stats-grid">
        ${statCard("RPO", `${policy.rpo_hours} ч.`, `минимум ${policy.minimum_retained_backups} коп.`, "◷")}
        ${statCard("RTO", `${policy.rto_minutes} мин.`, `drill не старше ${policy.restore_drill_max_age_days} дн.`, "⟲")}
        ${statCard("Последний backup", latestBackup ? (latestBackup.status === "verified" ? "Проверен" : "Зарегистрирован") : "Нет", latestBackup ? formatDate(latestBackup.backup_created_at) : "receipt отсутствует", "▧")}
        ${statCard("Последний drill", latestDrill ? (latestDrill.rto_met ? "RTO соблюдён" : "RTO нарушен") : "Нет", latestDrill ? formatDate(latestDrill.completed_at) : "проверка не выполнялась", "✓")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Проверки политики</h3><p class="cell-sub">RPO учитывает только backup, для которого проверены artifact SHA-256, подписанный manifest, состав файлов и база данных.</p></div>${can("owner", "admin") ? `<button class="btn btn-primary btn-sm" data-action="recovery-policy-edit">Изменить политику</button>` : ""}</div><div class="table-wrap"><table><thead><tr><th>Проверка</th><th>Статус</th><th>Комментарий</th></tr></thead><tbody>${checkRows}</tbody></table></div></section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Резервные копии</h3><p class="cell-sub">Регистрация receipt не равна проверке архива. Статус «Архив проверен» появляется только после verify или restore drill.</p></div>${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-action="recovery-backup-import">Импорт receipt</button>` : ""}</div>${backupRows ? `<div class="table-wrap"><table><thead><tr><th>Артефакт</th><th>Доверие</th><th>Создание</th><th>Контрольная сумма</th></tr></thead><tbody>${backupRows}</tbody></table></div>` : emptyState("▧", "Backup receipts не зарегистрированы", "Создайте подписанную копию через scripts/recovery_backup.py, затем выполните проверку.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Restore drill</h3><p class="cell-sub">SQLite действительно открывается в изолированном временном каталоге. Проверка PostgreSQL archive без отдельной БД честно помечается как metadata-only.</p></div>${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-action="recovery-drill-import">Импорт drill receipt</button>` : ""}</div>${drillRows ? `<div class="table-wrap"><table><thead><tr><th>Drill</th><th>Результат</th><th>Время</th><th>Доказательство</th></tr></thead><tbody>${drillRows}</tbody></table></div>` : emptyState("⟲", "Restore drill ещё не зарегистрирован", "Проверьте backup изолированной CLI-командой и импортируйте подписанный drill receipt.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Команды оператора</h3><p class="cell-sub">Команды выполняются на сервере с доступом к БД и master key. Они не публикуют данные в Telegram.</p></div></div><div class="panel-body"><div class="code-box">python scripts/recovery_backup.py --organization main --json
python scripts/recovery_verify.py BACKUP.zip BACKUP.zip.receipt.json --organization main --json
python scripts/recovery_restore_drill.py BACKUP.zip BACKUP.zip.receipt.json --organization main --json</div><div class="alert alert-warning mt-14"><strong>Production:</strong> используйте age-шифрование и храните identity отдельно от backup. Никогда не выполняйте restore поверх рабочей БД без утверждённого disaster-recovery runbook.</div></div></section>`;
  },

  /**
   * Выполнить commissioning, явно сохраняя побочные эффекты UI или worker.
   */
  async commissioning() {
    const [checks, programs, bundles, campaigns] = await Promise.all([
      api("/commissioning?limit=100"),
      api("/pilot/programs"),
      api("/configuration-bundles?limit=100"),
      api("/campaigns"),
    ]);
    state.data.commissioningChecks = checks;
    state.data.pilotPrograms = programs;
    state.data.configurationBundles = bundles;
    state.data.campaigns = campaigns;
    const latest = checks[0] || null;
    const latestRows = latest?.checks?.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(item.code)}</div></td><td>${badge(item.status, readinessCheckStatuses)}</td><td>${escapeHtml(item.message)}</td></tr>`).join("") || "";
    const programRows = programs.map((program) => {
      const current = program.stages.find((item) => item.stage_order === program.current_stage_order) || program.stages[program.stages.length - 1];
      const stageRows = program.stages.map((stage) => {
        const key = `${program.id}|${stage.id}`;
        const actions = [];
        if (can("owner", "admin", "operator") && ["pending", "ready"].includes(stage.status) && stage.stage_order === program.current_stage_order) actions.push(button("Проверить", "pilot-stage-refresh", key));
        if (can("owner", "admin") && stage.status === "ready" && stage.stage_order === program.current_stage_order) actions.push(button("Начать этап", "pilot-stage-start", key, "primary"));
        if (can("owner", "admin", "operator") && ["running", "awaiting_signoff"].includes(stage.status)) actions.push(button("Привязать запуск", "pilot-stage-attach", key));
        if (can("owner", "admin") && stage.status === "awaiting_signoff") actions.push(button("Решение", "pilot-stage-signoff", key, "success"));
        return `<tr><td><div class="cell-title">${stage.stage_order}. ${escapeHtml(stage.title)}</div><div class="cell-sub">${stage.requires_live_telegram ? "live Telegram" : "fake mode"} · ${stage.target_destination_count} назначений</div></td><td>${badge(stage.status, pilotStageStatuses)}</td><td><div class="cell-sub">Readiness: ${escapeHtml(stage.readiness_report_id || "—")}</div><div class="cell-sub">Run: ${escapeHtml(stage.campaign_run_id || "—")}</div>${stage.evidence_sha256 ? `<div class="cell-sub">SHA-256 ${escapeHtml(stage.evidence_sha256.slice(0, 16))}…</div>` : ""}</td><td><div class="actions">${actions.join("") || "—"}</div></td></tr>`;
      }).join("");
      return `<section class="panel mt-18"><div class="panel-header"><div><h3>${escapeHtml(program.name)}</h3><p class="cell-sub">${escapeHtml(program.campaign_id)} · текущий этап ${current ? current.stage_order : "—"}</p></div><div class="actions">${badge(program.status, pilotProgramStatuses)}${button("Акт приёмки", "pilot-program-report", program.id)}${can("owner", "admin") && !["completed", "cancelled"].includes(program.status) ? button("Отменить", "pilot-program-cancel", program.id, "danger") : ""}</div></div><div class="table-wrap"><table><thead><tr><th>Этап</th><th>Статус</th><th>Доказательства</th><th>Действия</th></tr></thead><tbody>${stageRows}</tbody></table></div></section>`;
    }).join("");
    const bundleRows = bundles.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.filename)}</div><div class="cell-sub">SHA-256 ${escapeHtml(item.sha256.slice(0, 20))}…</div></td><td>${escapeHtml(bundleKinds[item.kind] || item.kind)}</td><td>${formatBytes(item.size_bytes)}</td><td>${formatDate(item.created_at)}</td><td><div class="actions">${can("owner", "admin") ? `${button("Скачать", "bundle-download", item.id)}${button("Удалить", "bundle-delete", item.id, "danger")}` : "—"}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Ввод в эксплуатацию</h2><p>Формализованный путь от локальной проверки к live-пилоту. Архивы конфигурации не содержат Telegram-сессий, токенов, AI-ключей или утверждений.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-secondary" data-action="commissioning-run">Запустить диагностику</button><button class="btn btn-primary" data-action="pilot-program-new">＋ Программа пилота</button><button class="btn btn-secondary" data-action="bundle-export">Экспорт</button><button class="btn btn-secondary" data-action="bundle-import">Импорт</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      <div class="alert alert-info mb-18"><strong>Последовательность:</strong> диагностика инфраструктуры → fake mode → одна служебная группа → 5 разрешённых групп → 20 → 50 → 100. Каждый этап требует актуального approval, readiness и отдельной приёмки.</div>
      <div class="stats-grid">
        ${statCard("Инфраструктура", latest ? (readinessStatuses[latest.status]?.label || latest.status) : "Не проверялась", latest ? `до ${formatDate(latest.expires_at)}` : "создайте отчёт", "◎")}
        ${statCard("Программы пилота", programs.length, `${programs.filter((item) => item.status === "completed").length} принято`, "◫")}
        ${statCard("Архивы конфигурации", bundles.length, `${bundles.filter((item) => item.kind === "import").length} импортов`, "⇄")}
        ${statCard("Кампании", campaigns.length, `${campaigns.filter((item) => item.approved_at).length} утверждено`, "▷")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Диагностика среды</h3><p class="cell-sub">Проверяет БД и миграции, storage, worker, lock backend, audit-chain, TOTP, HTTPS, backups и безопасный runtime.</p></div>${can("owner", "admin") ? `<button class="btn btn-primary btn-sm" data-action="commissioning-run">Проверить сейчас</button>` : ""}</div>${latest ? `<div class="panel-body"><div class="alert ${latest.status === "blocked" ? "alert-danger" : latest.status === "warning" ? "alert-warning" : "alert-success"} mb-14"><strong>${escapeHtml(readinessStatuses[latest.status]?.label || latest.status)}.</strong> ${latest.blockers.length} блокировок, ${latest.warnings.length} предупреждений. Fingerprint ${escapeHtml(latest.fingerprint.slice(0, 20))}…</div></div><div class="table-wrap"><table><thead><tr><th>Проверка</th><th>Результат</th><th>Комментарий</th></tr></thead><tbody>${latestRows}</tbody></table></div>` : emptyState("◎", "Диагностика ещё не запускалась", "Создайте первый отчёт перед формированием программы пилота.", can("owner", "admin") ? `<button class="btn btn-primary" data-action="commissioning-run">Запустить диагностику</button>` : "")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Программы контролируемого пилота</h3><p class="cell-sub">Сохраняют связь с campaign run, итогами доставки и hash-chain аудита.</p></div>${can("owner", "admin") ? `<button class="btn btn-primary btn-sm" data-action="pilot-program-new">Создать программу</button>` : ""}</div>${programRows || emptyState("◫", "Программ пилота нет", "Выберите подготовленную кампанию и задайте ступени расширения.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Безопасный перенос конфигурации</h3><p class="cell-sub">Экспортируемые подключения не содержат credentials; назначения импортируются выключенными и непроверенными; кампании — черновиками.</p></div><div class="actions">${can("owner", "admin") ? `<button class="btn btn-secondary btn-sm" data-action="bundle-export">Экспортировать</button><button class="btn btn-primary btn-sm" data-action="bundle-import">Импортировать</button>` : ""}</div></div>${bundleRows ? `<div class="table-wrap"><table><thead><tr><th>Файл</th><th>Тип</th><th>Размер</th><th>Создан</th><th>Действие</th></tr></thead><tbody>${bundleRows}</tbody></table></div>` : emptyState("⇄", "Архивов пока нет", "Создайте безопасный ZIP для переноса структуры между ПК и сервером.")}</section>`;
  },

  /**
   * Выполнить operations, явно сохраняя побочные эффекты UI или worker.
   */
  async operations() {
    const [overview, assessments, incidents] = await Promise.all([
      api("/operations/overview"),
      api("/operations/slo-assessments?limit=100"),
      api("/operations/incidents?limit=200"),
    ]);
    state.data.operationsOverview = overview;
    state.data.sloAssessments = assessments;
    state.data.incidents = incidents;
    const latest = overview.latest_assessment;
    const policy = overview.policy;
    const checks = latest?.checks || [];
    const checkRows = checks.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title || item.code)}</div><div class="cell-sub"><code>${escapeHtml(item.code)}</code></div></td><td>${badge(item.status, readinessCheckStatuses)}</td><td><div>${escapeHtml(item.message)}</div><div class="cell-sub">Факт: ${escapeHtml(item.observed ?? "—")} · лимит: ${escapeHtml(item.limit ?? "—")}</div></td></tr>`).join("");
    const incidentRows = incidents.map((item) => {
      const actions = [button("Открыть", "incident-open", item.id)];
      if (can("owner", "admin", "operator")) {
        if (item.status === "open") actions.push(button("Подтвердить", "incident-acknowledge", item.id, "warning"));
        if (["open", "acknowledged"].includes(item.status)) actions.push(button("Устранение", "incident-mitigate", item.id, "primary"));
        if (["open", "acknowledged", "mitigating"].includes(item.status)) actions.push(button("Разрешить", "incident-resolve", item.id, "success"));
        if (can("owner", "admin") && item.status === "resolved") actions.push(button("Закрыть", "incident-close", item.id, "success"));
        if (can("owner", "admin") && ["resolved", "closed"].includes(item.status)) actions.push(button("Открыть снова", "incident-reopen", item.id, "warning"));
        actions.push(button("Комментарий", "incident-comment", item.id));
      }
      return `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(incidentSources[item.source] || item.source)} · ${formatDate(item.detected_at)}</div><div class="cell-sub">${escapeHtml(truncate(item.summary, 120))}</div></td><td>${badge(item.severity, severityStatuses)}<div class="mt-5">${badge(item.status, incidentStatuses)}</div></td><td><div class="cell-title">${item.owner_user_id ? `<code>${escapeHtml(item.owner_user_id.slice(0, 8))}…</code>` : "Не назначен"}</div><div class="cell-sub">${item.linked_slo_assessment_id ? `SLO ${escapeHtml(item.linked_slo_assessment_id.slice(0, 8))}…` : "без привязки к SLO"}</div></td><td><div class="actions">${actions.join("")}</div></td></tr>`;
    }).join("");
    const assessmentRows = assessments.map((item) => `<tr><td><div class="cell-title">${formatDate(item.created_at)}</div><div class="cell-sub">${escapeHtml(item.source)} · до ${formatDate(item.expires_at)}</div></td><td>${badge(item.status, readinessStatuses)}</td><td><div class="cell-title">${formatPercentBps(item.delivery_success_rate_bps)} успешных доставок</div><div class="cell-sub">${item.eligible_deliveries} в выборке · ${item.failed_deliveries} ошибок · ${item.uncertain_deliveries} требуют сверки</div></td><td><div class="cell-title">${formatPercentBps(item.error_budget_consumed_bps, { budget: true })}</div><div class="cell-sub">очередь ${item.oldest_queue_age_seconds} сек. · worker ${item.worker_heartbeat_age_seconds ?? "—"} сек.</div></td><td><code>${escapeHtml(item.fingerprint.slice(0, 16))}…</code></td></tr>`).join("");
    const latestStatus = latest ? (readinessStatuses[latest.status]?.label || latest.status) : "Нет оценки";
    const gateAlert = !overview.publishing_gate_allowed || !overview.changes_gate_allowed
      ? `<div class="alert alert-danger mb-18"><strong>Операционный gate заблокирован.</strong><br>Публикации: ${escapeHtml(overview.publishing_gate_message)}<br>Изменения: ${escapeHtml(overview.changes_gate_message)}</div>`
      : `<div class="alert alert-success mb-18"><strong>Операционный gate пройден.</strong> Публикации и управляемые изменения могут продолжаться при прохождении остальных проверок.</div>`;
    return `<div class="page-header"><div><h2>Надёжность и инциденты</h2><p>SLO-оценка фиксирует delivery success, error budget, очередь, worker и нерешённые доставки. Критические нарушения автоматически блокируют публикации и production-изменения.</p></div><div class="page-actions">${can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="operations-assess">Оценить SLO</button><button class="btn btn-danger" data-action="incident-new">＋ Инцидент</button>` : ""}${can("owner", "admin") ? `<button class="btn btn-secondary" data-action="slo-policy-edit">Политика</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">Обновить</button></div></div>
      ${gateAlert}
      <div class="stats-grid">
        ${statCard("SLO-статус", latestStatus, overview.assessment_current ? "оценка актуальна" : "оценка отсутствует или истекла", "◬")}
        ${statCard("Успешность доставки", formatPercentBps(latest?.delivery_success_rate_bps), `цель ${formatPercentBps(policy.delivery_success_target_bps)}`, "✓")}
        ${statCard("Error budget", formatPercentBps(latest?.error_budget_consumed_bps, { budget: true }), `warning ${policy.error_budget_warning_percent}% · critical ${policy.error_budget_critical_percent}%`, "◷")}
        ${statCard("Очередь", `${latest?.oldest_queue_age_seconds ?? 0} сек.`, `лимит ${policy.max_queue_age_seconds} сек.`, "⇄")}
        ${statCard("Worker heartbeat", latest?.worker_heartbeat_age_seconds === null || latest?.worker_heartbeat_age_seconds === undefined ? "Нет" : `${latest.worker_heartbeat_age_seconds} сек.`, `лимит ${policy.max_worker_heartbeat_age_seconds} сек.`, "◉")}
        ${statCard("Открытые инциденты", overview.open_incidents, `${overview.open_critical_incidents} критических`, "!")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Текущая SLO-оценка</h3><p class="cell-sub">Оценка неизменяема и действительна только при совпадении SHA-256 политики и срока действия.</p></div>${latest ? `<div>${badge(latest.status, readinessStatuses)} <span class="cell-sub">до ${formatDate(latest.expires_at)}</span></div>` : ""}</div>${latest ? `<div class="table-wrap"><table><thead><tr><th>Проверка</th><th>Статус</th><th>Результат</th></tr></thead><tbody>${checkRows}</tbody></table></div>` : emptyState("◬", "SLO ещё не оценивался", "Запустите ручную оценку после старта worker.", can("owner", "admin", "operator") ? `<button class="btn btn-primary" data-action="operations-assess">Оценить SLO</button>` : "")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Инциденты</h3><p class="cell-sub">Жизненный цикл: открыт → подтверждён → устраняется → разрешён → закрыт. Каждое действие сохраняется в отдельной истории и audit-chain.</p></div>${can("owner", "admin", "operator") ? `<button class="btn btn-danger btn-sm" data-action="incident-new">Создать инцидент</button>` : ""}</div>${incidentRows ? `<div class="table-wrap"><table><thead><tr><th>Инцидент</th><th>Уровень</th><th>Ответственный</th><th>Действия</th></tr></thead><tbody>${incidentRows}</tbody></table></div>` : emptyState("✓", "Активных и исторических инцидентов нет", "Автоматические SLO-инциденты появятся только при блокирующем нарушении.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>История SLO</h3><p class="cell-sub">Assessment сохраняет окно измерения, снимок политики, метрики, checks и fingerprint.</p></div></div>${assessmentRows ? `<div class="table-wrap"><table><thead><tr><th>Оценка</th><th>Статус</th><th>Доставка</th><th>Нагрузка</th><th>Fingerprint</th></tr></thead><tbody>${assessmentRows}</tbody></table></div>` : emptyState("◷", "История пуста", "После первой оценки здесь появится неизменяемое доказательство состояния.")}</section>`;
  },

  /**
   * Загрузить доказательства ёмкости организации и отрисовать контроль admission/backpressure.
   */
  async capacity() {
    const [overview, assessments] = await Promise.all([
      api("/capacity/overview"),
      api("/capacity/assessments?limit=100"),
    ]);
    state.data.capacityOverview = overview;
    state.data.capacityAssessments = assessments;
    const policy = overview.policy;
    const metrics = overview.metrics || {};
    const latest = overview.latest_assessment;
    const checks = latest?.checks || [];
    const alertClass = overview.admission_allowed
      ? ["warning", "blocked"].includes(latest?.status) ? "alert-warning" : "alert-success"
      : "alert-danger";
    const checkRows = checks.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.message)}</div><div class="cell-sub"><code>${escapeHtml(item.code)}</code></div></td><td>${badge(item.status, readinessCheckStatuses)}</td><td><div class="cell-title">${escapeHtml(item.actual ?? "—")}</div><div class="cell-sub">лимит ${escapeHtml(item.limit ?? "—")}</div></td></tr>`).join("");
    const assessmentRows = assessments.map((item) => `<tr><td><div class="cell-title">${formatDate(item.created_at)}</div><div class="cell-sub">${escapeHtml(item.source)} · до ${formatDate(item.expires_at)}</div></td><td>${badge(item.status, readinessStatuses)}</td><td><div class="cell-title">${item.active_jobs} активных · ${item.ready_jobs} готовых</div><div class="cell-sub">processing ${item.processing_jobs} · held ${item.held_jobs} · review ${item.waiting_review_jobs}</div></td><td><div class="cell-title">${item.queue_utilization_percent}%</div><div class="cell-sub">drain ${item.estimated_drain_seconds} сек. · сеть ${item.network_starts_last_minute}/${item.network_starts_last_hour}</div></td><td><code>${escapeHtml(item.fingerprint.slice(0, 16))}…</code></td></tr>`).join("");
    const blockers = (latest?.blockers || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const warnings = (latest?.warnings || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    return `<div class="page-header"><div><h2>Capacity & Backpressure Assurance</h2><p>Лимиты применяются до создания большой очереди и повторно непосредственно перед Telegram gateway. Отложенная работа не расходует attempt_count и не создаёт сетевой вызов.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="capacity-policy-edit">Политика</button>` : ""}${can("owner", "admin", "operator") ? `<button class="btn btn-secondary" data-action="capacity-assess">Снять оценку</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      <div class="alert ${alertClass} mb-18"><strong>${escapeHtml(overview.admission_message)}</strong><br><code>${escapeHtml(overview.admission_code)}</code>${!overview.assessment_current ? `<div class="mt-8">Последняя сохранённая оценка отсутствует, просрочена или относится к предыдущей политике.</div>` : ""}${blockers ? `<ul class="mt-8">${blockers}</ul>` : ""}${warnings ? `<ul class="mt-8">${warnings}</ul>` : ""}</div>
      <div class="stats-grid">
        ${statCard("Активные задания", metrics.active_jobs ?? 0, `лимит ${policy.max_active_jobs}`, "⇄")}
        ${statCard("Готовая очередь", metrics.ready_jobs ?? 0, `лимит ${policy.max_ready_jobs}`, "▷")}
        ${statCard("Processing", metrics.processing_jobs ?? 0, `лимит ${policy.max_processing_jobs}`, "↗")}
        ${statCard("Заполнение", `${metrics.queue_utilization_percent ?? 0}%`, `block с ${policy.admission_block_utilization_percent}%`, "◫")}
        ${statCard("Прогноз drain", `${metrics.estimated_drain_seconds ?? 0} сек.`, `лимит ${policy.max_estimated_drain_seconds} сек.`, "◷")}
        ${statCard("Сетевой бюджет", `${metrics.network_starts_last_minute ?? 0}/мин`, `${metrics.network_starts_last_hour ?? 0}/час`, "◉")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Действующая политика</h3><p class="cell-sub">Порядок лимитов защищён как API-валидацией, так и ограничениями базы: processing ≤ ready ≤ active.</p></div>${can("owner", "admin") ? `<button class="btn btn-primary btn-sm" data-action="capacity-policy-edit">Изменить</button>` : ""}</div><div class="panel-body"><dl class="detail-list"><div><dt>Admission</dt><dd>${policy.gate_admission ? "enforced" : "monitor-only"}</dd></div><div><dt>Dispatch</dt><dd>${policy.gate_dispatch ? "enforced" : "monitor-only"}</dd></div><div><dt>Запуск</dt><dd>до ${policy.max_jobs_per_run} jobs, до ${policy.max_active_runs} активных runs</dd></div><div><dt>Telegram starts</dt><dd>${policy.max_network_starts_per_minute}/мин · ${policy.max_network_starts_per_hour}/час</dd></div><div><dt>Assessment TTL</dt><dd>${policy.assessment_ttl_minutes} мин.</dd></div><div><dt>Прогнозная скорость</dt><dd>${escapeHtml(metrics.effective_dispatches_per_minute ?? 0)} dispatch/мин.</dd></div></dl></div></section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Последняя оценка</h3><p class="cell-sub">Assessment неизменяем и содержит policy SHA-256, фактические метрики и projected delta.</p></div></div>${checkRows ? `<div class="table-wrap"><table><thead><tr><th>Проверка</th><th>Статус</th><th>Факт / лимит</th></tr></thead><tbody>${checkRows}</tbody></table></div>` : emptyState("◫", "Оценка ещё не сохранена", "Создайте ручной assessment или дождитесь worker-проверки.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>История capacity evidence</h3><p class="cell-sub">Admission, dispatch, worker и ручные оценки сохраняются отдельно; изменение политики делает старые evidence неактуальными.</p></div>${can("owner", "admin", "operator") ? `<button class="btn btn-secondary btn-sm" data-action="capacity-assess">Снять оценку</button>` : ""}</div>${assessmentRows ? `<div class="table-wrap"><table><thead><tr><th>Время</th><th>Статус</th><th>Очередь</th><th>Нагрузка</th><th>Fingerprint</th></tr></thead><tbody>${assessmentRows}</tbody></table></div>` : emptyState("◫", "Истории пока нет", "Worker создаст evidence автоматически после запуска.")}</section>`;
  },

  /**
   * Выполнить execution, явно сохраняя побочные эффекты UI или worker.
   */
  async execution() {
    const [overview, failovers, attempts] = await Promise.all([
      api("/execution/overview"),
      api("/execution/failovers?limit=100"),
      api("/execution/delivery-attempts?limit=100"),
    ]);
    state.data.executionOverview = overview;
    state.data.failovers = failovers;
    state.data.deliveryAttempts = attempts;
    const lease = overview.lease;
    const siteRows = overview.sites.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.display_name)}</div><div class="cell-sub"><code>${escapeHtml(item.site_key)}</code> · ${escapeHtml(item.hostname || "hostname не указан")}</div></td><td>${item.is_active_site ? badge("active", executionLeaseStatuses) : badge("standby", {standby:{label:"Standby",cls:"muted"}})}<div class="mt-5">${item.online ? badge("online", {online:{label:"Heartbeat актуален",cls:"success"}}) : badge("offline", {offline:{label:"Heartbeat устарел",cls:"danger"}})}</div></td><td><div class="cell-title">${escapeHtml(item.last_worker_id || "—")}</div><div class="cell-sub">TeleFlow ${escapeHtml(item.version || "—")}</div></td><td>${formatDate(item.last_seen_at)}</td></tr>`).join("");
    const failoverRows = failovers.map((item) => {
      const actions = [];
      if (can("owner", "admin") && item.status === "requested") {
        actions.push(button("Подтвердить", "execution-failover-approve", item.id, "danger"));
        actions.push(button("Отменить", "execution-failover-cancel", item.id, "warning"));
      }
      return `<tr><td><div class="cell-title"><code>${escapeHtml(item.source_site_key)}</code> → <code>${escapeHtml(item.target_site_key)}</code></div><div class="cell-sub">epoch ${item.source_epoch}${item.target_epoch ? ` → ${item.target_epoch}` : ""}</div></td><td>${badge(item.status, failoverStatuses)}</td><td><div>${escapeHtml(truncate(item.reason, 100))}</div>${item.blockers?.length ? `<div class="cell-sub text-danger">${escapeHtml(item.blockers.join("; "))}</div>` : ""}</td><td><div class="cell-title">${formatDate(item.requested_at)}</div><div class="cell-sub">${item.completed_at ? `завершено ${formatDate(item.completed_at)}` : ""}</div></td><td><div class="actions">${actions.join("")}</div></td></tr>`;
    }).join("");
    const attemptRows = attempts.map((item) => `<tr><td><div class="cell-title"><code>${escapeHtml(item.job_id.slice(0, 8))}…</code> · попытка ${item.attempt_number}</div><div class="cell-sub">${escapeHtml(item.site_key)} · epoch ${item.fence_epoch}</div></td><td>${badge(item.status, deliveryAttemptStatuses)}</td><td><div class="cell-title">${escapeHtml(item.worker_id)}</div><div class="cell-sub">${formatDate(item.network_started_at || item.prepared_at)}</div></td><td><div class="cell-title">${escapeHtml(item.telegram_message_id || item.error_code || "—")}</div><div class="cell-sub">${escapeHtml(truncate(item.error_message || "", 90))}</div></td></tr>`).join("");
    const standbyCandidates = overview.sites.filter((item) => item.online && !item.is_active_site && item.enabled);
    return `<div class="page-header"><div><h2>Active / Standby и execution fencing</h2><p>Одна организация обслуживается только одним active worker. Fencing epoch и блокировка строки lease предотвращают одновременный Telegram-вызов после переключения.</p></div><div class="page-actions">${can("owner", "admin") && standbyCandidates.length && !overview.open_failover ? `<button class="btn btn-danger" data-action="execution-failover-new">Переключить площадку</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      ${lease.status !== "active" ? `<div class="alert alert-warning mb-18"><strong>Execution находится в ${escapeHtml(lease.status)}.</strong> Новые Telegram-вызовы заблокированы до завершения или отмены failover.</div>` : ""}
      <div class="stats-grid">
        ${statCard("Active site", lease.active_site_key, `текущая площадка: ${overview.current_site_key}`, "⇆")}
        ${statCard("Fencing epoch", lease.epoch, `holder: ${lease.holder_worker_id || "не назначен"}`, "#")}
        ${statCard("Processing", overview.processing_jobs, "активных jobs", "▷")}
        ${statCard("Неопределённые", overview.uncertain_jobs, "требуют ручной сверки", "!")}
        ${statCard("Network started", overview.active_attempts, "зафиксированных попыток", "↗")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Площадки выполнения</h3><p class="cell-sub">Heartbeat показывает доступность, но active-право определяется только lease и epoch в общей базе.</p></div></div>${siteRows ? `<div class="table-wrap"><table><thead><tr><th>Площадка</th><th>Роль</th><th>Worker</th><th>Heartbeat</th></tr></thead><tbody>${siteRows}</tbody></table></div>` : emptyState("⇆", "Площадки ещё не зарегистрированы", "Запустите worker с уникальным TELEFLOW_EXECUTION_SITE_KEY.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Запросы переключения</h3><p class="cell-sub">Failover сначала переводит источник в draining, затем требует независимого подтверждения и увеличивает epoch.</p></div></div>${failoverRows ? `<div class="table-wrap"><table><thead><tr><th>Маршрут</th><th>Статус</th><th>Причина</th><th>Время</th><th>Действия</th></tr></thead><tbody>${failoverRows}</tbody></table></div>` : emptyState("⇆", "Переключений ещё не было", "История controlled failover появится здесь.")}</section>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Неизменяемый журнал попыток</h3><p class="cell-sub">NETWORK_STARTED сохраняется отдельной транзакцией до Telegram-вызова; после падения такой job не повторяется автоматически.</p></div></div>${attemptRows ? `<div class="table-wrap"><table><thead><tr><th>Job</th><th>Статус</th><th>Worker</th><th>Результат</th></tr></thead><tbody>${attemptRows}</tbody></table></div>` : emptyState("↗", "Сетевых попыток ещё нет", "После доставки здесь появится site, epoch и итог Telegram-вызова.")}</section>`;
  },

  /**
   * Выполнить continuity, явно сохраняя побочные эффекты UI или worker.
   */
  async continuity() {
    const [overview, drills, execution] = await Promise.all([
      api("/continuity/overview"),
      api("/continuity/drills?limit=200"),
      api("/execution/overview"),
    ]);
    state.data.continuityOverview = overview;
    state.data.continuityDrills = drills;
    state.data.continuityExecution = execution;
    const policy = overview.policy;
    const runtime = overview.runtime_snapshot || {};
    const latest = overview.latest_drill;
    const rows = drills.map((item) => {
      const actions = [button("История", "continuity-events", item.id), button("Проверить chain", "continuity-verify", item.id)];
      if (can("owner", "admin")) {
        if (item.status === "draft") actions.unshift(button("Запустить", "continuity-start", item.id, item.mode === "live" ? "danger" : "primary"));
        if (item.status === "awaiting_failback") actions.unshift(button("Failback", "continuity-failback", item.id, "danger"));
        if (item.status === "awaiting_signoff") actions.unshift(button("Решение", "continuity-signoff", item.id, "success"));
        if (["draft", "running"].includes(item.status)) actions.push(button("Отменить", "continuity-cancel", item.id, "warning"));
      }
      const epochs = [item.source_epoch, item.target_epoch, item.return_epoch].filter((value) => value !== null && value !== undefined).join(" → ");
      return `<tr><td><div class="cell-title">${escapeHtml(continuityModes[item.mode] || item.mode)}</div><div class="cell-sub"><code>${escapeHtml(item.source_site_key)}</code> → <code>${escapeHtml(item.target_site_key)}</code></div></td><td>${badge(item.status, continuityDrillStatuses)}<div class="cell-sub mt-5">epoch ${escapeHtml(epochs || "—")}</div></td><td><div class="cell-title">RTO ${item.rto_seconds === null || item.rto_seconds === undefined ? "—" : `${item.rto_seconds} сек.`}</div><div class="cell-sub">до ${formatDate(item.expires_at)}</div></td><td><div class="cell-title">${formatDate(item.created_at)}</div><div class="cell-sub">${item.evidence_sha256 ? `SHA-256 ${escapeHtml(item.evidence_sha256.slice(0, 16))}…` : "evidence ещё нет"}</div>${item.failure_reason ? `<div class="cell-sub text-danger">${escapeHtml(truncate(item.failure_reason, 120))}</div>` : ""}</td><td><div class="actions">${actions.join("")}</div></td></tr>`;
    }).join("");
    const alertClass = overview.compliant ? "alert-success" : overview.required ? "alert-danger" : "alert-warning";
    const blockers = overview.blockers.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const warnings = overview.warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    const active = runtime.active || {};
    const standby = runtime.standby || {};
    return `<div class="page-header"><div><h2>Непрерывность и failback assurance</h2><p>Simulation доказывает нулевое сетевое воздействие. Live drill использует production execution fencing, возвращает active lease на source и требует независимой приёмки evidence.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="continuity-new">＋ Новое учение</button><button class="btn btn-secondary" data-action="continuity-policy-edit">Политика</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      <div class="alert ${alertClass} mb-18"><strong>${overview.compliant ? "Continuity gate пройден" : overview.required ? "Continuity gate заблокирован" : "Continuity gate не обязателен"}.</strong>${blockers ? `<ul class="mt-8">${blockers}</ul>` : ""}${warnings ? `<ul class="mt-8">${warnings}</ul>` : ""}</div>
      <div class="stats-grid">
        ${statCard("Политика", policy.enabled ? "Включена" : "Отключена", policy.require_live_drill ? "требуется live drill" : "simulation допустима", "∞")}
        ${statCard("Допустимый RTO", `${policy.max_rto_seconds} сек.`, `evidence ${policy.evidence_valid_days} дн.`, "◷")}
        ${statCard("Active runtime", active.site_key || "Нет", active.runtime_fingerprint ? `fingerprint ${active.runtime_fingerprint.slice(0, 12)}…` : "heartbeat/evidence отсутствует", "⇆")}
        ${statCard("Standby runtime", standby.site_key || "Нет", standby.runtime_fingerprint ? `fingerprint ${standby.runtime_fingerprint.slice(0, 12)}…` : "совместимая площадка не найдена", "⇄")}
        ${statCard("Последний результат", latest ? (continuityDrillStatuses[latest.status]?.label || latest.status) : "Нет", latest?.rto_seconds !== null && latest?.rto_seconds !== undefined ? `RTO ${latest.rto_seconds} сек.` : "принятое evidence отсутствует", "✓")}
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Учения и доказательства</h3><p class="cell-sub">История событий связана SHA-256 chain. Повторная проверка выявляет изменение, удаление, перестановку или перенос события между организациями.</p></div>${can("owner", "admin") ? `<button class="btn btn-primary btn-sm" data-action="continuity-new">Создать</button>` : ""}</div>${rows ? `<div class="table-wrap"><table><thead><tr><th>Режим и маршрут</th><th>Статус</th><th>RTO</th><th>Evidence</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("∞", "Учений ещё нет", "Начните с simulation, затем выполните controlled live failover/failback.", can("owner", "admin") ? `<button class="btn btn-primary" data-action="continuity-new">Создать учение</button>` : "")}</section>`;
  },

  /**
   * Выполнить jobs, явно сохраняя побочные эффекты UI или worker.
   */
  async jobs() {
    const jobs = await api("/jobs?limit=300");
    state.data.jobs = jobs;
    await loadBase({ destinations: true, connections: true });
    const destinationNames = Object.fromEntries(state.data.destinations.map((x) => [x.id, x.title]));
    const connectionNames = Object.fromEntries(state.data.connections.map((x) => [x.id, x.name]));
    const rows = jobs.map((item) => {
      const uncertain = item.status === "waiting_review" && ["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"].includes(item.error_code);
      const retryAllowed = can("owner", "admin") && ["failed", "skipped"].includes(item.status) && !item.review_resolution;
      const cancelAllowed = can("owner", "admin", "operator") && ["held", "pending", "retry", "waiting_review"].includes(item.status) && !uncertain;
      return `<tr><td><div class="cell-title">${escapeHtml(destinationNames[item.destination_id] || item.destination_id)}</div><div class="cell-sub">${escapeHtml(connectionNames[item.connection_id] || item.connection_id)}</div><div class="cell-sub">пакет ${item.batch_number}</div></td><td>${badge(item.status, jobStatuses)}${item.review_resolution ? `<div class="cell-sub mt-5">сверка: ${escapeHtml(item.review_resolution)}</div>` : ""}</td><td><div class="cell-title">${formatDate(item.due_at)}</div><div class="cell-sub">попытка ${item.attempt_count}/${item.max_attempts}</div></td><td><div class="cell-title">${escapeHtml(item.error_code || "—")}</div><div class="cell-sub">${escapeHtml(truncate(item.error_message || "ошибок нет", 80))}</div></td><td><div class="cell-title">${escapeHtml(item.telegram_message_id || "—")}</div><div class="cell-sub">${formatDate(item.finished_at)}</div><div class="cell-sub mono-small">${escapeHtml((item.content_fingerprint || "").slice(0, 14))}${item.content_fingerprint ? "…" : ""}</div></td><td><div class="actions">${uncertain && can("owner", "admin") ? button("Сверить", "job-resolve", item.id, "danger") : ""}${retryAllowed ? button("Повторить", "job-retry", item.id, "success") : ""}${cancelAllowed ? button("Отменить", "job-cancel", item.id, "danger") : ""}</div></td></tr>`;
    }).join("");
    return `<div class="page-header"><div><h2>Очередь доставки</h2><p>Удержанные пакеты не выбираются worker. Неоднозначный результат разрешается только ручной сверкой без автоматического повтора.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div><section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Назначение</th><th>Статус</th><th>Время</th><th>Ошибка</th><th>Результат</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("⇄", "Очередь пуста", "После запуска утверждённой кампании здесь появятся задания.")}</section>`;
  },

  /**
   * Выполнить notifications, явно сохраняя побочные эффекты UI или worker.
   */
  async notifications() {
    const [items, counts] = await Promise.all([api("/notifications?limit=500"), api("/notifications/counts")]);
    state.data.notifications = items;
    state.notificationCounts = counts;
    updateChromeIndicators();
    const rows = items.map((item) => `<tr class="${item.status === "unread" ? "notification-unread" : ""}"><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(item.event_type)}${item.occurrence_count > 1 ? ` · повторов ${item.occurrence_count}` : ""}</div></td><td>${badge(item.severity, { info: { label: "Информация", cls: "info" }, warning: { label: "Предупреждение", cls: "warning" }, critical: { label: "Критично", cls: "danger" } })}<div class="mt-5">${badge(item.status, notificationStatuses)}</div></td><td><div class="notification-message">${escapeHtml(item.message)}</div><div class="cell-sub mt-5">${formatDate(item.last_occurred_at)}</div></td><td><div class="actions">${item.status === "unread" ? button("Прочитано", "notification-read", item.id) : ""}${can("owner", "admin") && item.status !== "acknowledged" ? button("Подтвердить", "notification-ack", item.id, item.severity === "critical" ? "danger" : "success") : ""}${item.entity_type === "campaign" && item.entity_id ? `<button class="btn btn-secondary btn-sm" data-route="campaigns">К кампаниям</button>` : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Операционные уведомления</h2><p>Критические события требуют подтверждения владельцем или администратором; одинаковые события объединяются без потери счётчика.</p></div><div class="page-actions">${counts.unread ? `<button class="btn btn-secondary" data-action="notifications-read-all">Отметить всё прочитанным</button>` : ""}<button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      ${counts.critical_unacknowledged ? `<div class="alert alert-danger mb-18"><strong>Неподтверждённых критических событий: ${counts.critical_unacknowledged}.</strong> Проверьте причину и состояние очереди до возобновления публикаций.</div>` : ""}
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Событие</th><th>Уровень</th><th>Описание</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("♢", "Уведомлений нет", "Критические ограничения, запросы на утверждение и действия safety-контура появятся здесь.")}</section>`;
  },

  /**
   * Выполнить audit, явно сохраняя побочные эффекты UI или worker.
   */
  async audit() {
    const [logs, verification] = await Promise.all([api("/audit?limit=300"), api("/audit/verify")]);
    state.data.audit = logs;
    state.data.auditVerification = verification;
    const rows = logs.map((item) => `<tr><td><div class="cell-title">#${item.sequence ?? "legacy"}</div><div class="cell-sub">${formatDate(item.created_at)}</div></td><td><div class="cell-title">${escapeHtml(item.action)}</div><div class="cell-sub">${escapeHtml(item.entity_type || "система")} ${escapeHtml(item.entity_id ? item.entity_id.slice(0, 8) : "")}</div></td><td>${badge(item.severity, { info: { label: "Информация", cls: "info" }, warning: { label: "Предупреждение", cls: "warning" }, critical: { label: "Критично", cls: "danger" } })}</td><td><div class="hash-cell"><code>${escapeHtml(item.entry_hash ? item.entry_hash.slice(0, 16) + "…" : "legacy")}</code><span>${escapeHtml(item.prev_hash ? item.prev_hash.slice(0, 10) + "…" : "—")}</span></div></td><td><code>${escapeHtml(truncate(JSON.stringify(item.details || {}), 150))}</code></td></tr>`).join("");
    const status = verification.valid ? `<div class="audit-integrity audit-integrity-ok"><strong>Цепочка целостна</strong><span>${verification.checked_entries} записей · head #${verification.head_sequence}</span><code>${escapeHtml(verification.computed_head_hash)}</code></div>` : `<div class="audit-integrity audit-integrity-failed"><strong>Проверка целостности не пройдена</strong><span>${escapeHtml(verification.first_error || `${verification.legacy_entries} legacy-записей требуют offline backfill`)}</span>${verification.first_error_entry_id ? `<code>${escapeHtml(verification.first_error_entry_id)}</code>` : ""}</div>`;
    return `<div class="page-header"><div><h2>Tamper-evident журнал действий</h2><p>Каждая новая запись связана SHA-256 hash-chain. Проверка обнаруживает изменение содержимого, порядка или head-состояния.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="audit-verify">Проверить целостность</button></div></div>${status}<section class="panel mt-18">${rows ? `<div class="table-wrap"><table><thead><tr><th>№ и время</th><th>Событие</th><th>Уровень</th><th>Hash</th><th>Детали</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("☷", "Аудит пуст", "События появятся после действий в панели.")}</section>`;
  },

  /**
   * Выполнить business, явно сохраняя побочные эффекты UI или worker.
   */
  async business() {
    await loadBase({ connections: true });
    const items = await api("/business/connections");
    state.data.businessConnections = items;
    const bots = state.data.connections.filter((item) => item.kind === "bot");
    const botRows = bots.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(item.telegram_username ? `@${item.telegram_username}` : "Bot API")}</div></td><td>${badge(item.status, connectionStatuses)}</td><td><div class="cell-title">Business updates</div><div class="cell-sub">connection, message, edit, delete</div></td><td><div class="actions">${can("owner", "admin") ? `${button("Настроить webhook", "business-webhook-setup", item.id, "primary")}${button("Проверить", "business-webhook-info", item.id)}${button("Удалить", "business-webhook-delete", item.id, "danger")}` : "—"}</div></td></tr>`).join("");
    const connectionNames = Object.fromEntries(state.data.connections.map((item) => [item.id, item.name]));
    const businessRows = items.map((item) => `<tr><td><div class="cell-title">${escapeHtml(contactName(item))}</div><div class="cell-sub">Business ID ${escapeHtml(truncate(item.business_connection_id, 28))}</div></td><td>${escapeHtml(connectionNames[item.telegram_connection_id] || item.telegram_connection_id)}</td><td>${badge(item.status, businessStatuses)}</td><td><div class="cell-title">${item.is_enabled ? "Автоматизация включена" : "Отключена"}</div><div class="cell-sub">${formatDate(item.last_update_at)}</div></td><td>${can("owner", "admin") ? button(item.is_enabled ? "Отключить" : "Включить", "business-toggle", item.id, item.is_enabled ? "warning" : "success") : "—"}</td></tr>`).join("");
    return `<div class="page-header"><div><h2>Telegram Business и Secretary Mode</h2><p>Официальный webhook принимает входящие business updates, сохраняет их идемпотентно и передаёт обработку worker-у.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      <div class="alert alert-info mb-18"><strong>Webhook не раскрывает секрет в интерфейсе.</strong> URL содержит случайный path token, а Telegram дополнительно передаёт секретный заголовок. Изменение настроек фиксируется в аудите.</div>
      <section class="panel mb-18"><div class="panel-header"><h3>Bot API подключения</h3></div>${botRows ? `<div class="table-wrap"><table><thead><tr><th>Бот</th><th>Статус</th><th>Обновления</th><th>Webhook</th></tr></thead><tbody>${botRows}</tbody></table></div>` : emptyState("↯", "Нет Bot API-подключения", "Сначала подключите Telegram-бота в разделе «Подключения».")}</section>
      <section class="panel"><div class="panel-header"><h3>Подключённые бизнес-профили</h3><span class="cell-sub">${items.length} всего</span></div>${businessRows ? `<div class="table-wrap"><table><thead><tr><th>Профиль</th><th>Бот</th><th>Статус</th><th>Автоматизация</th><th>Действия</th></tr></thead><tbody>${businessRows}</tbody></table></div>` : emptyState("◌", "Business connection ещё не получен", "После подключения бота к профилю Telegram пришлёт update business_connection.")}</section>`;
  },

  /**
   * Выполнить conversations, явно сохраняя побочные эффекты UI или worker.
   */
  async conversations() {
    const items = await api("/conversations?limit=300");
    state.data.conversations = items;
    const rows = items.map((item) => `<tr><td><div class="cell-title">${escapeHtml(contactName(item))}</div><div class="cell-sub">${escapeHtml(item.username ? `@${item.username}` : `чат ${item.telegram_chat_id}`)}</div></td><td>${badge(item.status, conversationStatuses)}</td><td>${badge(item.consent_status, consentStatuses)}</td><td><div class="cell-title">${escapeHtml(item.vacancy_key || "Не определена")}</div><div class="cell-sub">${item.ai_enabled ? "AI разрешён" : "Только оператор"}</div></td><td><div class="cell-title">${formatDate(item.last_message_at)}</div><div class="cell-sub">хранить до ${formatDate(item.retention_until, false)}</div></td><td><div class="actions">${button("Открыть", "conversation-open", item.id, "primary")}${can("owner", "admin", "operator") && item.status !== "closed" ? button("Закрыть", "conversation-close", item.id, "danger") : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Входящие диалоги</h2><p>В списке отображаются только маскированные превью. Полный текст раскрывается отдельным аудируемым действием.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      <div class="stats-grid stats-grid-compact">${statCard("Открытые", items.filter((x) => !["closed", "blocked"].includes(x.status)).length, "активные обращения", "◌")}${statCard("Нужен оператор", items.filter((x) => x.status === "human_handoff").length, "ручная обработка", "!")}${statCard("AI активен", items.filter((x) => x.status === "ai_active").length, "после согласия", "✦")}${statCard("Согласие", items.filter((x) => x.consent_status === "granted").length, "получено", "✓")}</div>
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Контакт</th><th>Статус</th><th>Согласие</th><th>Вакансия</th><th>Последнее сообщение</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("◌", "Диалогов пока нет", "Они появятся после поступления business_message через настроенный webhook.")}</section>`;
  },

  /**
   * Выполнить candidates, явно сохраняя побочные эффекты UI или worker.
   */
  async candidates() {
    const items = await api("/candidates?limit=500");
    state.data.candidates = items;
    const rows = items.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.full_name || "Имя не указано")}</div><div class="cell-sub">${escapeHtml(item.city || "город не указан")}${item.age ? ` · ${item.age} лет` : ""}</div></td><td>${badge(item.status, candidateStatuses)}</td><td><div class="cell-title">${escapeHtml(item.vacancy_key || "—")}</div><div class="cell-sub">${escapeHtml(truncate(item.schedule || "график не указан", 50))}</div></td><td><div class="cell-title">${escapeHtml(truncate(item.experience || "Не указан", 80))}</div><div class="cell-sub">${item.consent_to_storage ? "согласие на хранение получено" : "нет согласия на хранение"}</div></td><td>${formatDate(item.updated_at)}</td><td><div class="actions">${button("Диалог", "conversation-open", item.conversation_id)}${can("owner", "admin", "operator") ? `${button("Изменить", "candidate-edit", item.id)}${button("Контакты", "candidate-contact", item.id, "primary")}` : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Карточки кандидатов</h2><p>AI заполняет только разрешённые поля, а оператор проверяет анкету перед использованием данных.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-secondary" data-action="candidates-export">⇩ CSV без контактов</button><button class="btn btn-primary" data-action="candidates-export-contacts">⇩ CSV с контактами</button>` : ""}</div></div>
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Кандидат</th><th>Статус</th><th>Вакансия</th><th>Опыт и согласие</th><th>Обновлён</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("♧", "Карточек пока нет", "Карточка создаётся после первого сообщения, которое обрабатывает политика автоматизации.")}</section>`;
  },

  /**
   * Выполнить automation, явно сохраняя побочные эффекты UI или worker.
   */
  async automation() {
    await loadBase({ connections: true });
    const [policies, providers, knowledge, flows] = await Promise.all([
      api("/automation/policies"), api("/automation/providers"), api("/automation/knowledge"), api("/automation/flows"),
    ]);
    state.data.policies = policies; state.data.providers = providers; state.data.knowledge = knowledge; state.data.flows = flows;
    const connectionNames = Object.fromEntries(state.data.connections.map((x) => [x.id, x.name]));
    const providerNames = Object.fromEntries(providers.map((x) => [x.id, x.name]));
    const flowNames = Object.fromEntries(flows.map((x) => [x.id, x.name]));
    const policyRows = policies.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(connectionNames[item.telegram_connection_id] || item.telegram_connection_id)}</div></td><td>${item.enabled ? badge("active", connectionStatuses) : badge("draft", connectionStatuses)}</td><td><div class="cell-title">${escapeHtml(flowNames[item.automation_flow_id] || "Без сценария")}</div><div class="cell-sub">${escapeHtml(providerNames[item.ai_provider_config_id] || "Rule-based после сценария")}</div></td><td><div class="cell-title">${item.require_consent_before_ai ? "Согласие обязательно" : "Без обязательного согласия"}</div><div class="cell-sub">до ${item.max_auto_replies_per_day} ответов/сутки · ${escapeHtml(item.timezone_name)}</div></td><td><div class="actions">${can("owner", "admin") ? `${button("Изменить", "policy-edit", item.id)}${button("Удалить", "policy-delete", item.id, "danger", item.enabled)}` : ""}</div></td></tr>`).join("");
    const providerRows = providers.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(item.kind === "rule_based" ? "Детерминированный локальный" : item.base_url || "OpenAI-compatible")}</div></td><td>${item.enabled ? badge("active", connectionStatuses) : badge("draft", connectionStatuses)}</td><td><div class="cell-title">${escapeHtml(item.model_name || "deterministic-v1")}</div><div class="cell-sub">timeout ${item.timeout_seconds} сек.</div></td><td><div class="actions">${can("owner", "admin") ? `${button("Проверить", "provider-test", item.id, "success")}${button("Изменить", "provider-edit", item.id)}${button("Удалить", "provider-delete", item.id, "danger")}` : "—"}</div></td></tr>`).join("");
    const kbRows = knowledge.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">ревизия ${item.revision} · ${escapeHtml(item.vacancy_key || "общая")}</div></td><td>${escapeHtml(truncate(item.content, 130))}</td><td>${escapeHtml((item.tags || []).join(", ") || "—")}</td><td>${item.is_active ? badge("active", connectionStatuses) : badge("draft", connectionStatuses)}</td><td><div class="actions">${can("owner", "admin", "operator") ? button("Изменить", "knowledge-edit", item.id) : ""}${can("owner", "admin") ? button("Удалить", "knowledge-delete", item.id, "danger") : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Политики входящей автоматизации</h2><p>Политика связывает Business-бота, согласие, сценарий анкеты, часы работы и AI-провайдер.</p></div><div class="page-actions"><button class="btn btn-secondary" data-route="flows">◇ Сценарии</button>${can("owner", "admin", "operator") ? `<button class="btn btn-secondary" data-action="new-knowledge">＋ Статья</button>` : ""}${can("owner", "admin") ? `<button class="btn btn-primary" data-action="new-policy">＋ Политика</button><button class="btn btn-secondary" data-action="new-provider">＋ AI-провайдер</button>` : ""}</div></div>
      <section class="panel mb-18"><div class="panel-header"><h3>Политики</h3><span class="cell-sub">${policies.length} всего</span></div>${policyRows ? `<div class="table-wrap"><table><thead><tr><th>Политика</th><th>Статус</th><th>Сценарий и AI</th><th>Приватность</th><th>Действия</th></tr></thead><tbody>${policyRows}</tbody></table></div>` : emptyState("✦", "Политик нет", "Создайте политику только после настройки Telegram Business webhook.")}</section>
      <div class="content-grid content-grid-equal"><section class="panel"><div class="panel-header"><h3>AI-провайдеры</h3></div>${providerRows ? `<div class="table-wrap"><table><thead><tr><th>Провайдер</th><th>Статус</th><th>Модель</th><th>Действия</th></tr></thead><tbody>${providerRows}</tbody></table></div>` : emptyState("✦", "Провайдеров нет", "Rule-based режим работает локально и не передаёт данные внешнему API.")}</section><section class="panel"><div class="panel-header"><h3>База знаний</h3></div>${kbRows ? `<div class="table-wrap"><table><thead><tr><th>Статья</th><th>Содержимое</th><th>Теги</th><th>Статус</th><th>Действия</th></tr></thead><tbody>${kbRows}</tbody></table></div>` : emptyState("≡", "База знаний пуста", "Добавьте ответы на вопросы по вакансиям.")}</section></div>`;
  },

  /**
   * Выполнить flows, явно сохраняя побочные эффекты UI или worker.
   */
  async flows() {
    const items = await api("/automation/flows");
    state.data.flows = items;
    const cards = items.map((item) => {
      const nodes = item.definition?.nodes || [];
      const path = nodes.map((node, index) => `<div class="flow-preview-node flow-type-${attr(node.type)}"><span>${index + 1}</span><div><strong>${escapeHtml(flowNodeTypeLabel(node.type))}</strong><small>${escapeHtml(truncate(node.text || "", 90))}</small></div></div>`).join(`<div class="flow-preview-arrow">↓</div>`);
      return `<article class="flow-card"><div class="flow-card-head"><div><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">ревизия ${item.revision} · ${nodes.length} шагов · обновлён ${formatDate(item.updated_at)}</div></div>${item.is_active ? badge("active", connectionStatuses) : badge("draft", connectionStatuses)}</div><p class="flow-description">${escapeHtml(item.description || "Описание не добавлено")}</p><div class="flow-preview">${path}</div><div class="flow-card-actions">${can("owner", "admin") ? `${button(item.is_active ? "Просмотреть" : "Изменить", "flow-edit", item.id, "primary")}${button(item.is_active ? "Отключить" : "Активировать", "flow-toggle", item.id, item.is_active ? "danger" : "success")}${button("Удалить", "flow-delete", item.id, "danger", item.is_active)}` : button("Просмотреть", "flow-edit", item.id)}</div></article>`;
    }).join("");
    return `<div class="page-header"><div><h2>Сценарии диалога</h2><p>Детерминированные анкеты работают до AI, не принимают решения о найме и сохраняют контакты только в зашифрованном виде.</p></div><div class="page-actions">${can("owner", "admin") ? `<button class="btn btn-primary" data-action="new-flow">＋ Новый сценарий</button>` : ""}<button class="btn btn-secondary" data-route="automation">← Политики</button></div></div>
      <div class="alert alert-info mb-18"><strong>Версионирование:</strong> активный сценарий неизменяем. Чтобы внести изменения, отключите использующую его политику, затем сам сценарий.</div>
      ${cards ? `<div class="flow-grid">${cards}</div>` : emptyState("◇", "Сценариев пока нет", "Создайте последовательность вопросов, вариантов ответа и завершающий шаг.")}`;
  },

  /**
   * Выполнить integrations, явно сохраняя побочные эффекты UI или worker.
   */
  async integrations() {
    const [items, outbox] = await Promise.all([api("/integrations"), api("/integrations/outbox/events?limit=300")]);
    state.data.integrations = items; state.data.outbox = outbox;
    const rows = items.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub">${escapeHtml(integrationKinds[item.kind] || item.kind)}</div></td><td>${item.is_active ? badge("active", connectionStatuses) : badge("draft", connectionStatuses)}</td><td>${escapeHtml((item.event_types || []).join(", ") || "Все события")}</td><td><div class="cell-title">${formatDate(item.last_delivery_at)}</div><div class="cell-sub">${escapeHtml(truncate(item.last_error_message || "ошибок нет", 90))}</div></td><td><div class="actions">${button("Тест", "integration-test", item.id, "success")}${button("Изменить", "integration-edit", item.id)}${button("Удалить", "integration-delete", item.id, "danger")}</div></td></tr>`).join("");
    const outboxRows = outbox.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.event_type)}</div><div class="cell-sub">${escapeHtml(item.aggregate_type)} · ${escapeHtml(truncate(item.aggregate_id, 24))}</div></td><td>${badge(item.status, outboxStatuses)}</td><td><div class="cell-title">${item.attempt_count}/${item.max_attempts}</div><div class="cell-sub">${formatDate(item.due_at)}</div></td><td>${escapeHtml(truncate(item.last_error_message || "—", 110))}</td><td>${formatDate(item.delivered_at)}</td><td>${["failed", "dead", "retry"].includes(item.status) ? button("Повторить", "outbox-retry", item.id, "warning") : "—"}</td></tr>`).join("");
    return `<div class="page-header"><div><h2>Интеграции и transactional outbox</h2><p>События кандидатов сначала фиксируются в БД, затем доставляются с подписью, lease и ограниченными повторами.</p></div><div class="page-actions"><button class="btn btn-primary" data-action="new-integration">＋ Интеграция</button><button class="btn btn-secondary" data-action="refresh-page">↻ Обновить</button></div></div>
      <section class="panel mb-18"><div class="panel-header"><h3>Endpoints</h3></div>${rows ? `<div class="table-wrap"><table><thead><tr><th>Название</th><th>Статус</th><th>События</th><th>Доставка</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("⇥", "Интеграций нет", "Подключите webhook, Google Sheets или безопасный CSV-экспорт.")}</section>
      <section class="panel"><div class="panel-header"><h3>Outbox</h3><span class="cell-sub">последние ${outbox.length} событий</span></div>${outboxRows ? `<div class="table-wrap"><table><thead><tr><th>Событие</th><th>Статус</th><th>Попытки</th><th>Ошибка</th><th>Доставлено</th><th>Действия</th></tr></thead><tbody>${outboxRows}</tbody></table></div>` : emptyState("⇄", "Outbox пуст", "События появятся после обновления кандидата или теста интеграции.")}</section>`;
  },

  /**
   * Выполнить privacy, явно сохраняя побочные эффекты UI или worker.
   */
  async privacy() {
    const [requests, conversations] = await Promise.all([api("/privacy/requests"), api("/conversations?limit=500")]);
    state.data.privacyRequests = requests; state.data.conversations = conversations;
    const rows = requests.map((item) => `<tr><td><div class="cell-title">${item.request_type === "export" ? "Экспорт" : "Удаление"}</div><div class="cell-sub">${escapeHtml(item.conversation_id || item.telegram_user_id || item.telegram_chat_id || "—")}</div></td><td>${badge(item.status, privacyStatuses)}</td><td>${formatDate(item.created_at)}</td><td><div class="cell-title">${formatDate(item.processed_at)}</div><div class="cell-sub">${escapeHtml(truncate(item.error_message || (item.details?.conversation_count !== undefined ? `${item.details?.conversation_count ?? 0} диалогов` : "—"), 90))}</div></td><td><div class="actions">${["pending", "failed"].includes(item.status) ? button("Обработать", "privacy-process", item.id, "primary") : ""}${item.request_type === "export" && item.status === "completed" && item.output_relative_path ? button("Скачать", "privacy-download", item.id, "success") : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Управление персональными данными</h2><p>Экспорт хранится зашифрованно ограниченное время; удаление очищает сообщения, карточку кандидата и исходные Telegram update.</p></div><div class="page-actions"><button class="btn btn-secondary" data-action="retention-run">Запустить retention</button><button class="btn btn-primary" data-action="new-privacy-request">＋ Запрос</button></div></div>
      <div class="alert alert-warning mb-18"><strong>Удаление необратимо.</strong> Выполняйте его только после проверки субъекта запроса и выбранного диалога. Все действия записываются в аудит.</div>
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Тип и субъект</th><th>Статус</th><th>Создан</th><th>Результат</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("◈", "Запросов нет", "Создайте экспорт или удаление по конкретному диалогу.")}</section>`;
  },

  /**
   * Выполнить organization, явно сохраняя побочные эффекты UI или worker.
   */
  async organization() {
    const item = await api("/organization");
    state.data.organization = item;
    return `<div class="page-header"><div><h2>${escapeHtml(item.name)}</h2><p>Организационная изоляция, аварийный стоп и политика утверждения применяются ко всем публикациям.</p></div><div class="page-actions">${can("owner", "admin") ? item.publishing_paused ? `<button class="btn btn-success" data-action="publishing-resume">Возобновить публикации</button>` : `<button class="btn btn-danger" data-action="publishing-pause">Аварийный стоп</button>` : ""}</div></div>
      ${item.publishing_paused ? `<div class="alert alert-danger mb-18"><strong>Публикации остановлены ${formatDate(item.publishing_paused_at)}.</strong><br>${escapeHtml(item.publishing_pause_reason || "Причина не указана")}</div>` : item.maintenance_mode ? `<div class="alert alert-warning mb-18"><strong>Активен режим обслуживания.</strong><br>${escapeHtml(item.maintenance_reason || "Scheduler и Telegram delivery временно заблокированы")}</div>` : `<div class="alert alert-success mb-18"><strong>Глобальная доставка разрешена.</strong> Все задания всё равно проходят индивидуальные safety-проверки непосредственно перед отправкой.</div>`}
      <div class="content-grid"><section class="panel"><div class="panel-header"><h3>Основные настройки</h3>${badge(item.status, { active: { label: "Активна", cls: "success" }, suspended: { label: "Приостановлена", cls: "danger" } })}</div><div class="panel-body"><form id="organization-form"><div class="form-grid"><div class="form-group full"><label>Название</label><input class="input" name="name" value="${attr(item.name)}" required minlength="2"></div><div class="form-group"><label>Часовой пояс IANA</label><input class="input" name="timezone_name" value="${attr(item.timezone_name)}" required></div><div class="form-group"><label>Срок хранения, дней</label><input class="input" name="retention_days" type="number" min="1" max="3650" value="${item.retention_days}" required></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="ai_enabled" ${item.ai_enabled ? "checked" : ""}> Разрешить AI-функции в организации</label></div></div><div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button></div></form></div></section>
      <section class="panel"><div class="panel-header"><h3>Политика утверждений</h3></div><div class="panel-body"><form id="approval-policy-form"><div class="form-grid"><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="require_distinct_campaign_approver" ${item.require_distinct_campaign_approver ? "checked" : ""}> Автор запроса не может утвердить собственную кампанию</label></div><div class="form-group"><label>Высокий риск от числа групп</label><input class="input" type="number" name="high_risk_destination_threshold" min="1" max="500" value="${item.high_risk_destination_threshold}" required></div><div class="form-group"><label>Решений для высокого риска</label><input class="input" type="number" name="high_risk_required_approvals" min="1" max="5" value="${item.high_risk_required_approvals}" required></div><div class="form-group"><label>Срок запроса, часов</label><input class="input" type="number" name="approval_request_ttl_hours" min="1" max="168" value="${item.approval_request_ttl_hours}" required></div><div class="form-group"><div class="form-label">Fingerprint</div><div class="field-note">Фиксирует текст, маршрут, расписание, интервалы и правила групп.</div></div></div><div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить политику</button></div></form></div></section></div>
      <section class="panel mt-18"><div class="panel-header"><h3>Идентификаторы и режим</h3></div><div class="panel-body"><dl class="detail-list"><div><dt>Organization ID</dt><dd><code>${escapeHtml(item.id)}</code></dd></div><div><dt>Slug</dt><dd>${escapeHtml(item.slug)}</dd></div><div><dt>Создана</dt><dd>${formatDate(item.created_at)}</dd></div><div><dt>Глобальная доставка</dt><dd>${item.publishing_paused ? "Остановлена" : item.maintenance_mode ? "Обслуживание" : "Разрешена"}</dd></div><div><dt>Maintenance mode</dt><dd>${item.maintenance_mode ? `Активен · ${escapeHtml(item.maintenance_reason || "без причины")}` : "Выключен"}</dd></div><div><dt>AI</dt><dd>${item.ai_enabled ? "Разрешён политикой организации" : "Отключён"}</dd></div></dl></div></section>`;
  },

  /**
   * Выполнить apikeys, явно сохраняя побочные эффекты UI или worker.
   */
  async apiKeys() {
    const items = await api("/api-keys");
    state.data.apiKeys = items;
    const rows = items.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.name)}</div><div class="cell-sub"><code>${escapeHtml(item.key_prefix)}…</code></div></td><td>${badge(item.status, apiKeyStatuses)}</td><td>${escapeHtml((item.scopes || []).join(", ") || "без scopes")}</td><td><div class="cell-title">${formatDate(item.last_used_at)}</div><div class="cell-sub">истекает ${formatDate(item.expires_at)}</div></td><td>${item.status === "active" ? button("Отозвать", "api-key-revoke", item.id, "danger") : "—"}</td></tr>`).join("");
    return `<div class="page-header"><div><h2>Сервисные API-ключи</h2><p>Ключ показывается только один раз. Хранится Argon2id-хеш, а доступ ограничивается scopes.</p></div><div class="page-actions"><button class="btn btn-primary" data-action="new-api-key">＋ Новый ключ</button></div></div>
      <div class="alert alert-info mb-18">Доступные scopes: <code>candidates:read</code>, <code>candidates:contacts</code>, <code>conversations:read</code>, <code>events:write</code>.</div>
      <section class="panel">${rows ? `<div class="table-wrap"><table><thead><tr><th>Ключ</th><th>Статус</th><th>Scopes</th><th>Использование</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("⌁", "API-ключей нет", "Создайте отдельный ключ с минимальным набором scopes для каждой интеграции.")}</section>`;
  },

  /**
   * Выполнить security, явно сохраняя побочные эффекты UI или worker.
   */
  async security() {
    const sessions = await api("/auth/sessions");
    const sessionRows = sessions.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.current ? "Текущее устройство" : "Активная сессия")}</div><div class="cell-sub">${escapeHtml(item.user_agent || "User-Agent не передан")}</div></td><td><div class="cell-title">${escapeHtml(item.created_ip || "IP не определён")}</div><div class="cell-sub">создана ${formatDate(item.created_at)}</div></td><td><div class="cell-title">${item.current ? badge("active", connectionStatuses) : badge("ready", connectionStatuses)}</div><div class="cell-sub">истекает ${formatDate(item.expires_at)}</div></td><td>${button(item.current ? "Выйти здесь" : "Завершить", "session-revoke", item.id, item.current ? "danger" : "secondary")}</td></tr>`).join("");
    return `<div class="page-header"><div><h2>Защита учётной записи</h2><p>Пароль хешируется Argon2id, 2FA хранится зашифрованно, refresh-токены ротируются и управляются по устройствам.</p></div></div>
      <div class="content-grid">
        <section class="panel"><div class="panel-header"><h3>Смена пароля</h3></div><div class="panel-body"><form id="password-form"><div class="form-grid"><div class="form-group full"><label>Текущий пароль</label><input class="input" type="password" name="current_password" autocomplete="current-password" required></div><div class="form-group full"><label>Новый пароль</label><input class="input" type="password" name="new_password" minlength="12" autocomplete="new-password" required><small>Не менее 12 символов; используйте уникальную фразу.</small></div></div><div class="form-actions"><button class="btn btn-primary" type="submit">Изменить пароль</button></div></form></div></section>
        <section class="panel"><div class="panel-header"><h3>Двухфакторная аутентификация</h3>${state.user.totp_enabled ? badge("active", connectionStatuses) : badge("unverified", permissionStatuses)}</div><div class="panel-body">${state.user.totp_enabled ? `<p class="cell-sub">TOTP включён. Для входа требуется код приложения-аутентификатора.</p><button class="btn btn-danger" data-action="totp-disable">Отключить 2FA</button>` : `<p class="cell-sub">Подключите TOTP перед размещением реальных токенов и пользовательских сессий.</p><button class="btn btn-primary" data-action="totp-start">Подключить 2FA</button>`}</div></section>
      </div>
      <section class="panel mt-18"><div class="panel-header"><div><h3>Активные сессии</h3><p class="cell-sub">Незнакомое устройство можно немедленно отключить. Действие записывается в аудит.</p></div>${sessions.length > 1 ? `<button class="btn btn-danger" data-action="sessions-revoke-others">Завершить остальные</button>` : ""}</div>${sessionRows ? `<div class="table-wrap"><table><thead><tr><th>Устройство</th><th>Сеть и начало</th><th>Состояние</th><th>Действие</th></tr></thead><tbody>${sessionRows}</tbody></table></div>` : emptyState("⌁", "Активных сессий нет", "Выполните вход повторно.")}</section>`;
  },

  /**
   * Выполнить users, явно сохраняя побочные эффекты UI или worker.
   */
  async users() {
    const users = await api("/users");
    state.data.users = users;
    const rows = users.map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.display_name)}</div><div class="cell-sub">${escapeHtml(item.email)}</div></td><td>${escapeHtml(roleLabel(item.role))}</td><td>${item.is_active ? badge("active", connectionStatuses) : badge("revoked", connectionStatuses)}</td><td><div class="cell-title">${formatDate(item.last_login_at)}</div><div class="cell-sub">создан ${formatDate(item.created_at, false)}</div></td><td><div class="actions">${button("Изменить", "user-edit", item.id)}${item.id !== state.user.id && item.is_active ? button("Отключить", "user-delete", item.id, "danger") : ""}</div></td></tr>`).join("");
    return `<div class="page-header"><div><h2>Команда</h2><p>Принцип наименьших привилегий: владелец, администратор, оператор и наблюдатель.</p></div><div class="page-actions"><button class="btn btn-primary" data-action="new-user">＋ Пользователь</button></div></div><section class="panel"><div class="table-wrap"><table><thead><tr><th>Пользователь</th><th>Роль</th><th>Состояние</th><th>Вход</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  },
};

/**
 * Выполнить statcard, явно сохраняя побочные эффекты UI или worker.
 */
function statCard(label, value, note, icon) {
  return `<div class="stat-card"><div class="stat-top"><span>${escapeHtml(label)}</span><span class="stat-icon">${icon}</span></div><div class="stat-value">${escapeHtml(value)}</div><div class="stat-note">${escapeHtml(note)}</div></div>`;
}
/**
 * Выполнить safetyitem, явно сохраняя побочные эффекты UI или worker.
 */
function safetyItem(icon, title, text, tone = "warning") {
  return `<div class="safety-item"><div class="safety-item-icon ${tone === "success" ? "success" : ""}">${icon}</div><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p></div></div>`;
}

/**
 * Выполнить dateinputvalue, явно сохраняя побочные эффекты UI или worker.
 */
function dateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Выполнить defaultanalyticsrange, явно сохраняя побочные эффекты UI или worker.
 */
function defaultAnalyticsRange() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 29);
  return {
    date_from: dateInputValue(start),
    date_to: dateInputValue(end),
    timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  };
}

/**
 * Выполнить analyticsquery, явно сохраняя побочные эффекты UI или worker.
 */
function analyticsQuery(range = state.analyticsRange || defaultAnalyticsRange()) {
  const params = new URLSearchParams();
  params.set("date_from", range.date_from);
  params.set("date_to", range.date_to);
  if (range.timezone_name) params.set("timezone_name", range.timezone_name);
  return params.toString();
}

/**
 * Выполнить analyticsbreakdown, явно сохраняя побочные эффекты UI или worker.
 */
function analyticsBreakdown(items) {
  if (!items?.length) return `<p class="cell-sub">За выбранный период данных нет.</p>`;
  const maximum = Math.max(...items.map((item) => item.count), 1);
  return `<div class="analytics-breakdown">${items.map((item) => `<div class="analytics-breakdown-row"><div><strong>${escapeHtml(item.label)}</strong><span>${item.count}</span></div><progress max="${maximum}" value="${item.count}"></progress></div>`).join("")}</div>`;
}

/**
 * Выполнить analyticstrend, явно сохраняя побочные эффекты UI или worker.
 */
function analyticsTrend(points) {
  if (!points?.length) return `<p class="cell-sub">За выбранный период данных нет.</p>`;
  const width = 760;
  const height = 220;
  const padding = 24;
  const maxValue = Math.max(...points.flatMap((point) => [point.delivery_sent, point.candidates_created, point.inbound_messages]), 1);
  /**
   * Выполнить topolyline, явно сохраняя побочные эффекты UI или worker.
   */
  const toPolyline = (key) => points.map((point, index) => {
    const x = points.length === 1 ? width / 2 : padding + (index * (width - padding * 2)) / (points.length - 1);
    const y = height - padding - (Number(point[key] || 0) / maxValue) * (height - padding * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const labels = points.filter((_point, index) => index === 0 || index === points.length - 1 || index % Math.max(1, Math.ceil(points.length / 6)) === 0).map((point) => {
    const index = points.indexOf(point);
    const x = points.length === 1 ? width / 2 : padding + (index * (width - padding * 2)) / (points.length - 1);
    return `<text x="${x.toFixed(1)}" y="214" text-anchor="middle">${escapeHtml(point.date.slice(5))}</text>`;
  }).join("");
  return `<div class="analytics-legend"><span class="legend-sent">Отправлено</span><span class="legend-inbound">Входящие</span><span class="legend-candidates">Кандидаты</span></div><div class="analytics-chart-scroll"><svg class="analytics-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика показателей"><line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" class="chart-axis"></line><polyline points="${toPolyline("delivery_sent")}" class="chart-line chart-sent"></polyline><polyline points="${toPolyline("inbound_messages")}" class="chart-line chart-inbound"></polyline><polyline points="${toPolyline("candidates_created")}" class="chart-line chart-candidates"></polyline>${labels}</svg></div>`;
}

/**
 * Выполнить formdataobject, явно сохраняя побочные эффекты UI или worker.
 */
function formDataObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

/**
 * Выполнить numberornull, явно сохраняя побочные эффекты UI или worker.
 */
function numberOrNull(value) {
  return value === "" || value === null || value === undefined ? null : Number(value);
}

/**
 * Выполнить connectionoptions, явно сохраняя побочные эффекты UI или worker.
 */
function connectionOptions(selected = "", activeOnly = false) {
  return state.data.connections.filter((item) => !activeOnly || item.status === "active").map((item) => `<option value="${attr(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.name)} · ${item.kind === "bot" ? "бот" : "аккаунт"}</option>`).join("");
}

/**
 * Выполнить mediaoptions, явно сохраняя побочные эффекты UI или worker.
 */
function mediaOptions(selected = "") {
  return `<option value="">Без медиа</option>${state.data.media.map((item) => `<option value="${attr(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.original_name)}</option>`).join("")}`;
}

/**
 * Выполнить openbotconnectionmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openBotConnectionModal() {
  openModal("Подключить Telegram-бота", `<form id="bot-connection-form"><div class="alert alert-info mb-16">Создайте бота через BotFather, добавьте его в разрешённые группы и выдайте только необходимые права.</div><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="name" required minlength="2" placeholder="HR Publisher Bot"></div><div class="form-group"><label>Токен Bot API</label><input class="input" name="bot_token" required type="password" autocomplete="off" placeholder="123456:ABC..."></div><div class="form-group"><label>Интервал, секунд</label><input class="input" name="min_interval_seconds" type="number" min="1" value="30"></div><div class="form-group"><label>Лимит в сутки</label><input class="input" name="daily_cap" type="number" min="1" max="500" value="100"></div><div class="form-group"><label>Cooldown группы, минут</label><input class="input" name="destination_cooldown_minutes" type="number" min="1" value="1440"></div><div class="form-group"><label>&nbsp;</label><label class="checkbox-row"><input type="checkbox" name="require_manual_approval" checked> Ручное утверждение кампаний</label><label class="checkbox-row"><input type="checkbox" name="stop_on_flood" checked> Остановка при FloodWait</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Проверить и подключить</button></div></form>`);
}

/**
 * Выполнить openuserconnectionmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openUserConnectionModal() {
  openModal("Авторизовать рабочий аккаунт", `<form id="user-connection-start-form"><div class="alert alert-warning mb-16"><strong>Используйте отдельный рабочий аккаунт владельца.</strong> Система не вступает в группы, не пишет пользователям и не обходит ограничения Telegram.</div><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="name" required placeholder="Аккаунт Михаила"></div><div class="form-group"><label>Телефон</label><input class="input" name="phone" required placeholder="+31..."></div><div class="form-group"><label>API ID</label><input class="input" name="api_id" type="number" min="1" required></div><div class="form-group"><label>API Hash</label><input class="input" name="api_hash" type="password" autocomplete="off" required></div><div class="form-group"><label>Интервал, секунд</label><input class="input" name="min_interval_seconds" type="number" min="60" value="90"></div><div class="form-group"><label>Лимит в сутки</label><input class="input" name="daily_cap" type="number" min="1" max="500" value="100"></div><div class="form-group"><label>Cooldown группы, минут</label><input class="input" name="destination_cooldown_minutes" type="number" min="60" value="1440"></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Получить код</button></div></form>`);
}

/**
 * Выполнить openusercodemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openUserCodeModal(challenge) {
  openModal("Подтвердить вход Telegram", `<form id="user-connection-complete-form"><input type="hidden" name="challenge_id" value="${attr(challenge.challenge_id)}"><div class="alert alert-info mb-16">Код отправлен в Telegram. Запрос действует до ${formatDate(challenge.expires_at)}.${challenge.code_hint ? ` ${escapeHtml(challenge.code_hint)}.` : ""}</div><div class="form-grid"><div class="form-group"><label>Код Telegram</label><input class="input" name="code" required autocomplete="one-time-code" autofocus></div><div class="form-group"><label>Облачный пароль 2FA <span class="cell-sub">если запрошен</span></label><input class="input" name="password" type="password" autocomplete="off"></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Завершить авторизацию</button></div></form>`);
}

/**
 * Выполнить openconnectioneditmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openConnectionEditModal(item) {
  openModal("Настройки подключения", `<form id="connection-edit-form" data-id="${attr(item.id)}"><div class="form-grid"><div class="form-group full"><label>Название</label><input class="input" name="name" value="${attr(item.name)}" required></div><div class="form-group"><label>Интервал, секунд</label><input class="input" name="min_interval_seconds" type="number" min="${item.kind === "user" ? 60 : 1}" value="${item.min_interval_seconds}"></div><div class="form-group"><label>Лимит в сутки</label><input class="input" name="daily_cap" type="number" min="1" max="500" value="${item.daily_cap}"></div><div class="form-group"><label>Cooldown группы, минут</label><input class="input" name="destination_cooldown_minutes" type="number" min="${item.kind === "user" ? 60 : 1}" value="${item.destination_cooldown_minutes}"></div><div class="form-group"><label>&nbsp;</label><label class="checkbox-row"><input type="checkbox" name="require_manual_approval" ${item.require_manual_approval ? "checked" : ""} ${item.kind === "user" ? "disabled" : ""}> Ручное утверждение</label><label class="checkbox-row"><input type="checkbox" name="stop_on_flood" ${item.stop_on_flood ? "checked" : ""} ${item.kind === "user" ? "disabled" : ""}> Остановка при FloodWait</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`);
}

/**
 * Выполнить destinationpermissionlabel, явно сохраняя побочные эффекты UI или worker.
 */
function destinationPermissionLabel(item) {
  if (item.permission_status !== "confirmed") return "разрешение не подтверждено";
  if (!item.permission_expires_at) return item.permission_reviewed_at ? `проверено ${formatDate(item.permission_reviewed_at, false)}` : "без срока действия";
  const expires = new Date(item.permission_expires_at);
  const expired = !Number.isNaN(expires.getTime()) && expires.getTime() <= Date.now();
  return `${expired ? "истекло" : "до"} ${formatDate(item.permission_expires_at)}`;
}

/**
 * Выполнить destinationwindowlabel, явно сохраняя побочные эффекты UI или worker.
 */
function destinationWindowLabel(item) {
  const names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const days = item.allowed_weekdays?.length ? item.allowed_weekdays.map((day) => names[day]).join(", ") : "ежедневно";
  const window = item.allowed_start_time && item.allowed_end_time
    ? `${String(item.allowed_start_time).slice(0, 5)}–${String(item.allowed_end_time).slice(0, 5)}`
    : "весь день";
  const timezone = item.timezone_name || "часовой пояс кампании";
  const cooldown = item.cooldown_minutes_override ? ` · cooldown ${item.cooldown_minutes_override} мин.` : "";
  return `${days}, ${window}, ${timezone}${cooldown}`;
}

/**
 * Выполнить destinationweekdayoptions, явно сохраняя побочные эффекты UI или worker.
 */
function destinationWeekdayOptions(selected = []) {
  const labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  return labels.map((label, value) => `<label class="weekday-option"><input type="checkbox" name="allowed_weekdays" value="${value}" ${selected.includes(value) ? "checked" : ""}><span>${label}</span></label>`).join("");
}

/**
 * Выполнить blackoutweekdayoptions, явно сохраняя побочные эффекты UI или worker.
 */
function blackoutWeekdayOptions(selected = []) {
  const labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  return labels.map((label, value) => `<label class="weekday-option"><input type="checkbox" name="blackout_weekdays" value="${value}" ${selected.includes(value) ? "checked" : ""}><span>${label}</span></label>`).join("");
}

/**
 * Выполнить openblackoutmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openBlackoutModal(item = null) {
  const editing = Boolean(item);
  const timezoneName = item?.timezone_name || Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Amsterdam";
  const startsAt = item?.starts_at ? dateTimeLocalForZone(item.starts_at, timezoneName) : "";
  const endsAt = item?.ends_at ? dateTimeLocalForZone(item.ends_at, timezoneName) : "";
  const destinationOptions = state.data.destinations.map((destination) => `<option value="${attr(destination.id)}" ${item?.destination_id === destination.id ? "selected" : ""}>${escapeHtml(destination.title)}</option>`).join("");
  openModal(
    editing ? "Изменить запрет публикаций" : "Новый запрет публикаций",
    `<form id="blackout-form" data-id="${attr(item?.id || "")}"><div class="alert alert-info mb-16"><strong>Это операционная блокировка, а не способ управлять лимитами Telegram.</strong> При активном окне worker переносит задание и не создаёт сетевой запрос.</div><div class="form-grid">
      <div class="form-group full"><label>Название</label><input class="input" name="title" required minlength="2" maxlength="180" value="${attr(item?.title || "")}" placeholder="Тихие часы отдела подбора"></div>
      <div class="form-group full"><label>Причина</label><textarea class="textarea" name="reason" required minlength="3" maxlength="4000">${escapeHtml(item?.reason || "")}</textarea></div>
      <div class="form-group"><label>Область действия</label><select class="select" name="scope"><option value="organization" ${item?.scope === "organization" || !item ? "selected" : ""}>Вся организация</option><option value="connection" ${item?.scope === "connection" ? "selected" : ""}>Telegram-подключение</option><option value="destination" ${item?.scope === "destination" ? "selected" : ""}>Отдельная группа</option></select></div>
      <div class="form-group"><label>Тип окна</label><select class="select" name="kind"><option value="one_time" ${item?.kind === "one_time" || !item ? "selected" : ""}>Разовое</option><option value="weekly" ${item?.kind === "weekly" ? "selected" : ""}>Еженедельное</option></select></div>
      <div class="form-group"><label>Подключение</label><select class="select" name="connection_id"><option value="">Не выбрано</option>${connectionOptions(item?.connection_id || "")}</select><small>Нужно только для области «Telegram-подключение».</small></div>
      <div class="form-group"><label>Группа / канал</label><select class="select" name="destination_id"><option value="">Не выбрано</option>${destinationOptions}</select><small>Нужно только для области «Отдельная группа».</small></div>
      <div class="form-group"><label>Начало разового окна</label><input class="input" name="starts_at" type="datetime-local" value="${attr(startsAt)}"></div>
      <div class="form-group"><label>Окончание разового окна</label><input class="input" name="ends_at" type="datetime-local" value="${attr(endsAt)}"></div>
      <div class="form-group full"><label>Дни еженедельного окна</label><div class="weekday-grid">${blackoutWeekdayOptions(item?.weekdays || [])}</div></div>
      <div class="form-group"><label>Начало weekly-окна</label><input class="input" name="start_time" type="time" value="${attr(item?.start_time ? String(item.start_time).slice(0, 5) : "")}"></div>
      <div class="form-group"><label>Окончание weekly-окна</label><input class="input" name="end_time" type="time" value="${attr(item?.end_time ? String(item.end_time).slice(0, 5) : "")}"></div>
      <div class="form-group"><label>Часовой пояс weekly-окна</label><input class="input" name="timezone_name" value="${attr(timezoneName)}" placeholder="Europe/Amsterdam"></div>
      <div class="form-group"><label>&nbsp;</label><label class="checkbox-row"><input type="checkbox" name="enabled" ${item?.enabled !== false ? "checked" : ""}> Правило активно</label></div>
    </div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`,
    { large: true },
  );
}

/**
 * Выполнить openreadinessreport, явно сохраняя побочные эффекты UI или worker.
 */
function openReadinessReport(report) {
  if (!report) throw new Error("Отчёт готовности не найден");
  const checks = (report.checks || []).map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title)}</div><div class="cell-sub">${escapeHtml(item.code)}</div></td><td>${badge(item.status, readinessCheckStatuses)}</td><td>${escapeHtml(item.message)}</td></tr>`).join("");
  openModal(`Отчёт готовности: ${report.summary?.campaign_name || report.campaign_id}`, `<div class="detail-list"><div><dt>Статус</dt><dd>${badge(report.status, readinessStatuses)}</dd></div><div><dt>Создан</dt><dd>${formatDate(report.created_at)}</dd></div><div><dt>Действует до</dt><dd>${formatDate(report.expires_at)}</dd></div><div><dt>Fingerprint</dt><dd class="mono-small">${escapeHtml(report.fingerprint)}</dd></div><div><dt>Preflight</dt><dd>${escapeHtml(report.preflight_report_id || "—")}</dd></div></div>${report.blockers?.length ? `<div class="alert alert-danger mt-16"><strong>Блокирующие причины:</strong><br>${report.blockers.map(escapeHtml).join("<br>")}</div>` : ""}${report.warnings?.length ? `<div class="alert alert-warning mt-16"><strong>Предупреждения:</strong><br>${report.warnings.map(escapeHtml).join("<br>")}</div>` : ""}<div class="table-wrap mt-16"><table><thead><tr><th>Проверка</th><th>Статус</th><th>Результат</th></tr></thead><tbody>${checks}</tbody></table></div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`, { large: true });
}

/**
 * Выполнить openstageassessment, явно сохраняя побочные эффекты UI или worker.
 */
function openStageAssessment(report) {
  if (!report) throw new Error("Оценка этапа не найдена");
  const checks = (report.checks || []).map((item) => `<tr><td><div class="cell-title">${escapeHtml(item.title || item.code)}</div><div class="cell-sub">${escapeHtml(item.code || "")}</div></td><td>${badge(item.status, readinessCheckStatuses)}</td><td>${escapeHtml(item.message || "—")}</td></tr>`).join("");
  openModal(`Оценка этапа: ${pilotStageLabels[report.current_stage]} → ${pilotStageLabels[report.requested_stage]}`, `<div class="detail-list"><div><dt>Статус</dt><dd>${badge(report.status, readinessStatuses)}</dd></div><div><dt>Создана</dt><dd>${formatDate(report.created_at)}</dd></div><div><dt>Действует до</dt><dd>${formatDate(report.expires_at)}</dd></div><div><dt>Fingerprint</dt><dd class="mono-small">${escapeHtml(report.fingerprint)}</dd></div></div>${report.blockers?.length ? `<div class="alert alert-danger mt-16"><strong>Блокирующие причины:</strong><br>${report.blockers.map(escapeHtml).join("<br>")}</div>` : ""}${report.warnings?.length ? `<div class="alert alert-warning mt-16"><strong>Предупреждения:</strong><br>${report.warnings.map(escapeHtml).join("<br>")}</div>` : ""}<div class="table-wrap mt-16"><table><thead><tr><th>Проверка</th><th>Статус</th><th>Результат</th></tr></thead><tbody>${checks}</tbody></table></div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`, { large: true });
}

/**
 * Выполнить opendestinationvalidationhistory, явно сохраняя побочные эффекты UI или worker.
 */
async function openDestinationValidationHistory(destinationId) {
  const destination = state.data.destinations.find((item) => item.id === destinationId);
  const history = await api(`/destinations/${destinationId}/validation-history?limit=100`);
  const rows = history.map((item) => `<tr><td>${formatDate(item.checked_at)}</td><td>${badge(item.status, { passed: { label: "Доступ есть", cls: "success" }, failed: { label: "Ошибка", cls: "danger" }, write_forbidden: { label: "Нет права писать", cls: "danger" } })}</td><td>${escapeHtml(item.source)}</td><td><div class="cell-title">${escapeHtml(item.error_code || "без ошибок")}</div><div class="cell-sub">${escapeHtml(item.error_message || "")}</div></td></tr>`).join("");
  openModal(`История проверки: ${destination?.title || destinationId}`, rows ? `<div class="table-wrap"><table><thead><tr><th>Время</th><th>Результат</th><th>Источник</th><th>Комментарий</th></tr></thead><tbody>${rows}</tbody></table></div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>` : emptyState("⌖", "Истории нет", "Назначение ещё не проверялось через Telegram."), { large: true });
}

/**
 * Выполнить opendestinationmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openDestinationModal(item = null) {
  if (!item && !state.data.connections.some((x) => x.status === "active")) {
    toast("Нет активного подключения", "Сначала подключите Telegram-бота или аккаунт.", "warning");
    return;
  }
  const editing = Boolean(item);
  const timezoneName = item?.timezone_name || Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Amsterdam";
  const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Amsterdam";
  const weekdays = item?.allowed_weekdays || [];
  const startTime = item?.allowed_start_time ? String(item.allowed_start_time).slice(0, 5) : "";
  const endTime = item?.allowed_end_time ? String(item.allowed_end_time).slice(0, 5) : "";
  const permissionExpiry = item?.permission_expires_at ? dateTimeLocalForZone(item.permission_expires_at, browserTimezone) : "";
  openModal(
    editing ? "Изменить назначение" : "Добавить разрешённую группу",
    `<form id="destination-form" data-id="${attr(item?.id || "")}" data-editing="${editing}">
      <div class="form-grid">
        ${editing ? "" : `<div class="form-group full"><label>Подключение</label><select class="select" name="connection_id" required>${connectionOptions("", true)}</select></div>
          <div class="form-group"><label>Username группы</label><input class="input" name="username" placeholder="work_moscow"></div>
          <div class="form-group"><label>Chat ID</label><input class="input" name="telegram_chat_id" type="number" placeholder="-100..."></div>
          <div class="form-group"><label>Тип</label><select class="select" name="kind"><option value="supergroup">Супергруппа</option><option value="group">Группа</option><option value="channel">Канал</option><option value="forum_topic">Тема форума</option></select></div>
          <div class="form-group"><label>Topic ID</label><input class="input" name="topic_id" type="number" min="1"></div>`}
        <div class="form-group full"><label>Название в панели</label><input class="input" name="title" value="${attr(item?.title || "")}" placeholder="Вакансии Москва"></div>
        <div class="form-group full"><label>Статус разрешения</label>
          ${editing ? `<select class="select" name="permission_status" id="destination-permission-status"><option value="unverified" ${item.permission_status === "unverified" ? "selected" : ""}>Не проверено</option><option value="confirmed" ${item.permission_status === "confirmed" ? "selected" : ""}>Публикация разрешена</option><option value="denied" ${item.permission_status === "denied" ? "selected" : ""}>Публикация запрещена</option></select>` : `<label class="checkbox-row"><input type="checkbox" name="permission_confirmed" id="destination-permission-confirmed"> Подтверждаю, что публикация разрешена правилами или администратором</label>`}
        </div>
        <div class="form-group full"><label>Основание разрешения</label><textarea class="textarea" name="permission_note" placeholder="Например: согласовано с администратором @name 05.08.2026">${escapeHtml(item?.permission_note || "")}</textarea></div>
        <div class="form-group full"><label>Ссылка на правила</label><input class="input" name="rules_url" type="url" value="${attr(item?.rules_url || "")}" placeholder="https://t.me/..."></div>
        <div class="form-group"><label>Разрешение действует до <span class="cell-sub">необязательно</span></label><input class="input" id="destination-permission-expiry" name="permission_expires_at" type="datetime-local" value="${attr(permissionExpiry)}"><small>После указанного времени preflight и worker автоматически заблокируют публикацию до повторной проверки.</small></div>
        <div class="form-group"><label>Последняя проверка</label><div class="input readonly-field">${escapeHtml(item?.permission_reviewed_at ? formatDate(item.permission_reviewed_at) : "Будет зафиксирована при подтверждении")}</div><small>Система записывает пользователя и время изменения в аудит.</small></div>
        <div class="form-group full destination-window-group"><label>Разрешённые дни недели</label><div class="weekday-grid">${destinationWeekdayOptions(weekdays)}</div><small>Если дни не выбраны, разрешены все дни. Эти настройки не заменяют правила группы.</small></div>
        <div class="form-group"><label>Начало окна</label><input class="input" name="allowed_start_time" type="time" value="${attr(startTime)}"><small>Оставьте оба поля пустыми для круглосуточного окна.</small></div>
        <div class="form-group"><label>Окончание окна</label><input class="input" name="allowed_end_time" type="time" value="${attr(endTime)}"><small>Поддерживается переход через полночь, например 22:00–06:00.</small></div>
        <div class="form-group"><label>Часовой пояс</label><input class="input" name="timezone_name" value="${attr(timezoneName)}" placeholder="Europe/Amsterdam"><small>IANA timezone. Если очистить, используется часовой пояс кампании.</small></div>
        <div class="form-group"><label>Индивидуальный cooldown, минут</label><input class="input" name="cooldown_minutes_override" type="number" min="1" max="525600" value="${attr(item?.cooldown_minutes_override || "")}" placeholder="по настройке подключения"><small>Переопределяет общий cooldown только для этой группы.</small></div>
        ${editing ? `<div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="enabled" ${item.enabled ? "checked" : ""}> Назначение включено</label></div>` : ""}
      </div>
      <div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">${editing ? "Сохранить" : "Проверить и добавить"}</button></div>
    </form>`,
    { large: true },
  );
}

/**
 * Выполнить opendestinationimportmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openDestinationImportModal() {
  const activeConnections = state.data.connections.filter((item) => item.status === "active");
  if (!activeConnections.length) {
    toast("Нет активного подключения", "Сначала подключите Telegram-бота или рабочий аккаунт.", "warning");
    return;
  }
  openModal("Массовый импорт назначений", `<form id="destination-import-form"><div class="alert alert-warning mb-16"><strong>Импорт не вступает в группы и не подтверждает разрешение автоматически.</strong> Аккаунт или бот уже должен иметь доступ. Строки TXT создаются со статусом «Не проверено» и не могут участвовать в кампании до ручного подтверждения.</div><div class="form-grid"><div class="form-group full"><label>Telegram-подключение</label><select class="select" name="connection_id" required>${connectionOptions("", true)}</select></div><div class="form-group full"><label>Файл TXT, CSV или TSV</label><input class="input" name="file" type="file" accept=".txt,.csv,.tsv,text/plain,text/csv,text/tab-separated-values" required><small>До 500 строк и 2 МБ. TXT: одна публичная ссылка, @username или chat ID на строку. Invite-ссылки и автоматическое вступление не поддерживаются.</small></div></div><details class="import-help mt-14"><summary>Поддерживаемые колонки CSV</summary><div class="code-box mt-10">link, username, telegram_chat_id, topic_id, title, kind, permission_confirmed, permission_note, rules_url, enabled, timezone_name, allowed_weekdays, allowed_start_time, allowed_end_time, cooldown_minutes_override, permission_expires_at</div><p class="cell-sub mt-8">Чтобы импортировать строку сразу как разрешённую, укажите permission_confirmed=true и заполните permission_note или rules_url.</p></details><div id="destination-import-result" class="mt-16"></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-secondary" type="button" data-action="destination-import-preview">Проверить файл</button><button class="btn btn-primary" type="button" data-action="destination-import-apply">Импортировать</button></div></form>`, { large: true });
}

/**
 * Выполнить destinationimportstatus, явно сохраняя побочные эффекты UI или worker.
 */
function destinationImportStatus(status) {
  return ({
    ready: { label: "Готово", cls: "success" },
    created: { label: "Создано", cls: "success" },
    duplicate: { label: "Дубликат", cls: "warning" },
    error: { label: "Ошибка", cls: "danger" },
    deferred: { label: "Отложено", cls: "warning" },
  })[status] || { label: status, cls: "muted" };
}

/**
 * Выполнить renderdestinationimportresult, явно сохраняя побочные эффекты UI или worker.
 */
function renderDestinationImportResult(result) {
  const target = document.getElementById("destination-import-result");
  if (!target) return;
  const rows = result.rows.map((item) => `<tr><td>${item.row_number}</td><td><div class="cell-title">${escapeHtml(item.title || item.username || item.telegram_chat_id || item.source)}</div><div class="cell-sub">${escapeHtml(item.username ? `@${item.username}` : item.telegram_chat_id || item.source)}${item.topic_id ? ` · тема ${item.topic_id}` : ""}</div></td><td>${badge(item.status, { [item.status]: destinationImportStatus(item.status) })}</td><td><div class="cell-sub">${escapeHtml(item.message)}</div></td><td>${item.permission_confirmed ? badge("confirmed", permissionStatuses) : badge("unverified", permissionStatuses)}</td></tr>`).join("");
  target.innerHTML = `<div class="import-summary"><span>Всего <strong>${result.total}</strong></span><span>${result.dry_run ? "Готово" : "Создано"} <strong>${result.dry_run ? result.ready : result.created}</strong></span><span>Дубликаты <strong>${result.duplicates}</strong></span><span>Ошибки <strong>${result.errors}</strong></span><span>Отложено <strong>${result.deferred}</strong></span></div><div class="table-wrap import-table"><table><thead><tr><th>Строка</th><th>Назначение</th><th>Статус</th><th>Комментарий</th><th>Разрешение</th></tr></thead><tbody>${rows}</tbody></table></div>${result.dry_run ? `<div class="alert alert-info mt-14">Предпросмотр ничего не изменил. Проверьте строки и нажмите «Импортировать».</div>` : `<div class="alert alert-success mt-14">Импорт завершён. Создано назначений: <strong>${result.created}</strong>. Неподтверждённые строки останутся заблокированными для кампаний.</div><div class="form-actions"><button class="btn btn-primary" type="button" data-action="destination-import-finish">Закрыть и обновить список</button></div>`}`;
}

/**
 * Выполнить rundestinationimport, явно сохраняя побочные эффекты UI или worker.
 */
async function runDestinationImport(dryRun) {
  const form = document.getElementById("destination-import-form");
  if (!form || !form.reportValidity()) return;
  if (!dryRun && !window.confirm("Импортировать проверенные строки? Неподтверждённые назначения будут созданы, но останутся недоступны для кампаний.")) return;
  const buttons = [...form.querySelectorAll("button[data-action^='destination-import-']")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const result = await api(`/destinations/bulk/${dryRun ? "preview" : "apply"}`, { method: "POST", body: new FormData(form) });
    renderDestinationImportResult(result);
    toast(dryRun ? "Файл проверен" : "Импорт завершён", `${dryRun ? result.ready : result.created} строк обработано успешно`, result.errors ? "warning" : "success");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}


/**
 * Выполнить openconnectiondiscoverymodal, явно сохраняя побочные эффекты UI или worker.
 */
async function openConnectionDiscoveryModal(connectionId) {
  const connection = state.data.connections.find((item) => item.id === connectionId);
  if (!connection) throw new Error("Telegram-подключение не найдено в текущем списке");
  openModal("Поиск доступных групп", `<div class="discovery-loading"><div class="spinner"></div><p>Telegram возвращает только диалоги, к которым подключение уже имеет доступ…</p></div>`, { large: true });
  try {
    const [discovered, existing] = await Promise.all([
      api(`/connections/${connectionId}/discover`),
      api(`/destinations?connection_id=${encodeURIComponent(connectionId)}`),
    ]);
    const existingKeys = new Set(existing.map((item) => `${item.telegram_chat_id}:${item.topic_id || ""}`));
    const rows = discovered.map((item) => {
      const key = `${item.telegram_chat_id}:`;
      const duplicate = existingKeys.has(key);
      const label = item.username ? `@${item.username}` : String(item.telegram_chat_id);
      return `<label class="discovery-item ${duplicate ? "is-existing" : ""}"><input type="checkbox" name="discovery_item" value="${attr(item.telegram_chat_id)}" data-username="${attr(item.username || "")}" data-title="${attr(item.title || label)}" data-kind="${attr(item.kind || "supergroup")}" ${duplicate ? "disabled" : ""}><span class="discovery-check">${duplicate ? "✓" : ""}</span><span class="discovery-copy"><strong>${escapeHtml(item.title || label)}</strong><small>${escapeHtml(label)} · ${escapeHtml(item.kind || "supergroup")}${duplicate ? " · уже добавлено" : ""}</small></span></label>`;
    }).join("");
    const timezoneName = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Amsterdam";
    openModal("Добавить существующие группы", `<form id="destination-discovery-form" data-connection-id="${attr(connectionId)}"><div class="alert alert-warning mb-16"><strong>Автоматического вступления нет.</strong> Показаны только диалоги, к которым бот или рабочий аккаунт уже имеет доступ. Найденная группа остаётся неподтверждённой, пока вы отдельно не укажете основание разрешения на публикацию.</div><div class="discovery-toolbar"><div><div class="cell-title">${escapeHtml(connection.name)}</div><div class="cell-sub">Найдено ${discovered.length}, уже добавлено ${existing.length}</div></div><label class="checkbox-row"><input id="discovery-select-all" type="checkbox" ${rows ? "" : "disabled"}> Выбрать все новые</label></div><div class="discovery-list">${rows || `<div class="empty p-25"><p>Доступных групп и каналов не найдено.</p></div>`}</div><div class="form-grid mt-16"><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="permission_confirmed"> Подтверждаю, что публикация разрешена правилами группы или её администратором</label><small>Без подтверждения назначения будут созданы, но не смогут участвовать в кампаниях.</small></div><div class="form-group full"><label>Основание разрешения</label><textarea class="textarea" name="permission_note" placeholder="Например: публикация вакансий разрешена закреплёнными правилами; проверено 06.08.2026"></textarea></div><div class="form-group full"><label>Ссылка на правила</label><input class="input" name="rules_url" type="url" placeholder="https://t.me/..."></div><div class="form-group full destination-window-group"><label>Разрешённые дни недели</label><div class="weekday-grid">${destinationWeekdayOptions([])}</div><small>Пустой выбор означает все дни. Настройка ограничивает планировщик, но не заменяет правила группы.</small></div><div class="form-group"><label>Начало окна</label><input class="input" name="allowed_start_time" type="time"></div><div class="form-group"><label>Окончание окна</label><input class="input" name="allowed_end_time" type="time"></div><div class="form-group"><label>Часовой пояс</label><input class="input" name="timezone_name" value="${attr(timezoneName)}"></div><div class="form-group"><label>Cooldown, минут</label><input class="input" name="cooldown_minutes_override" type="number" min="1" max="525600" placeholder="по подключению"></div></div><div id="destination-import-result" class="mt-16"></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="button" data-action="destination-discovery-apply">Добавить выбранные</button></div></form>`, { large: true });
  } catch (error) {
    closeModal();
    throw error;
  }
}

/**
 * Выполнить applyconnectiondiscovery, явно сохраняя побочные эффекты UI или worker.
 */
async function applyConnectionDiscovery() {
  const form = document.getElementById("destination-discovery-form");
  if (!form) return;
  const selected = [...form.querySelectorAll('input[name="discovery_item"]:checked')];
  if (!selected.length) throw new Error("Выберите хотя бы одну новую группу");
  const confirmed = form.elements.permission_confirmed.checked;
  const permissionNote = form.elements.permission_note.value.trim();
  const rulesUrl = form.elements.rules_url.value.trim();
  if (confirmed && !permissionNote && !rulesUrl) throw new Error("Для подтверждения разрешения добавьте примечание или ссылку на правила");
  const allowedWeekdays = [...form.querySelectorAll('input[name="allowed_weekdays"]:checked')].map((element) => Number(element.value));
  const startTime = form.elements.allowed_start_time.value || null;
  const endTime = form.elements.allowed_end_time.value || null;
  if ((startTime === null) !== (endTime === null)) throw new Error("Начало и окончание временного окна задаются вместе");
  if (startTime && startTime === endTime) throw new Error("Начало и окончание окна не должны совпадать");
  const payload = {
    connection_id: form.dataset.connectionId,
    items: selected.map((element) => ({
      telegram_chat_id: Number(element.value),
      username: element.dataset.username || null,
      title: element.dataset.title || null,
      kind: element.dataset.kind || "supergroup",
      topic_id: null,
    })),
    permission_confirmed: confirmed,
    permission_note: permissionNote || null,
    rules_url: rulesUrl || null,
    timezone_name: form.elements.timezone_name.value.trim() || null,
    allowed_weekdays: allowedWeekdays,
    allowed_start_time: startTime,
    allowed_end_time: endTime,
    cooldown_minutes_override: numberOrNull(form.elements.cooldown_minutes_override.value),
  };
  const button = form.querySelector('[data-action="destination-discovery-apply"]');
  if (button) button.disabled = true;
  try {
    const result = await api("/destinations/bulk/discovery", { method: "POST", body: JSON.stringify(payload) });
    renderDestinationImportResult(result);
    selected.forEach((element) => {
      element.checked = false;
      element.disabled = true;
      element.closest(".discovery-item")?.classList.add("is-existing");
    });
    toast("Группы обработаны", `Создано: ${result.created}; дубликаты: ${result.duplicates}; ошибки: ${result.errors}`, result.errors ? "warning" : "success");
  } finally {
    if (button) button.disabled = false;
  }
}

/**
 * Выполнить openmediamodal, явно сохраняя побочные эффекты UI или worker.
 */
function openMediaModal() {
  openModal("Загрузить медиафайл", `<form id="media-form"><div class="form-group"><label>Файл</label><input class="input" name="file" type="file" required accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.zip"><small>Максимальный размер определяется настройками сервера. Исполняемые файлы не принимаются.</small></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Загрузить</button></div></form>`);
}

/**
 * Выполнить opentemplatemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openTemplateModal(item = null) {
  const editing = Boolean(item);
  openModal(editing ? "Изменить шаблон" : "Новый шаблон", `<form id="template-form" data-id="${attr(item?.id || "")}"><div class="form-grid"><div class="form-group full"><label>Название</label><input class="input" name="name" required value="${attr(item?.name || "")}" placeholder="Вакансия курьера"></div><div class="form-group full"><label>Текст</label><textarea class="textarea textarea-lg" name="body" required maxlength="4096">${escapeHtml(item?.body || "")}</textarea><small>Не используйте вариации текста для обхода фильтров. Адаптация допустима только под реальные правила и аудиторию группы.</small></div><div class="form-group"><label>Форматирование</label><select class="select" name="parse_mode"><option value="plain" ${item?.parse_mode === "plain" || !item ? "selected" : ""}>Обычный текст</option><option value="html" ${item?.parse_mode === "html" ? "selected" : ""}>HTML</option><option value="markdown" ${item?.parse_mode === "markdown" ? "selected" : ""}>Markdown</option></select></div><div class="form-group"><label>Медиафайл</label><select class="select" name="media_asset_id">${mediaOptions(item?.media_asset_id || "")}</select></div><div class="form-group"><label class="checkbox-row"><input type="checkbox" name="link_preview" ${item?.link_preview !== false ? "checked" : ""}> Показывать предпросмотр ссылок</label></div>${editing ? `<div class="form-group"><label class="checkbox-row"><input type="checkbox" name="is_active" ${item.is_active ? "checked" : ""}> Шаблон активен</label></div>` : ""}</div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
}

/**
 * Выполнить destinationchecks, явно сохраняя побочные эффекты UI или worker.
 */
function destinationChecks(connectionId, selected = []) {
  const items = state.data.destinations.filter((item) => item.connection_id === connectionId);
  if (!items.length) return `<div class="empty p-25"><p>Для подключения нет назначений.</p></div>`;
  return items.map((item) => `<label class="multi-option"><input type="checkbox" name="destination_ids" value="${attr(item.id)}" ${selected.includes(item.id) ? "checked" : ""} ${item.permission_status !== "confirmed" || !item.enabled ? "disabled" : ""}><div><strong>${escapeHtml(item.title)}</strong><div class="cell-sub">${escapeHtml(item.username ? `@${item.username}` : item.telegram_chat_id)} · ${permissionStatuses[item.permission_status]?.label || item.permission_status}${!item.enabled ? " · отключено" : ""}</div></div></label>`).join("");
}

/**
 * Выполнить datetimelocalforzone, явно сохраняя побочные эффекты UI или worker.
 */
function dateTimeLocalForZone(value, timezoneName) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("sv-SE", {
      timeZone: timezoneName || Intl.DateTimeFormat().resolvedOptions().timeZone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  } catch {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }
}

/**
 * Выполнить opencampaignmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openCampaignModal(item = null, clone = false) {
  const editing = Boolean(item) && !clone;
  const availableConnections = state.data.connections.filter((connection) => connection.status !== "revoked");
  const availableTemplates = state.data.templates.filter((template) => template.is_active || template.id === item?.template_id || template.id === item?.secondary_template_id);
  if (!availableConnections.length || !availableTemplates.length) {
    toast("Недостаточно данных", "Нужны Telegram-подключение и хотя бы один шаблон.", "warning");
    return;
  }
  const connectionId = item?.connection_id || availableConnections.find((connection) => connection.status === "active")?.id || availableConnections[0].id;
  const templateId = item?.template_id || availableTemplates[0].id;
  const secondaryTemplateId = item?.secondary_template_id || "";
  const secondaryTemplateWeight = item?.secondary_template_weight || 30;
  const timezoneName = item?.timezone_name || Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Amsterdam";
  const rolloutMode = item?.rollout_mode || "staged";
  const now = new Date(Date.now() + 10 * 60 * 1000);
  now.setSeconds(0, 0);
  const scheduleValue = editing
    ? dateTimeLocalForZone(item.schedule_at, timezoneName)
    : dateTimeLocalForZone(now.toISOString(), timezoneName);
  const endValue = editing && item.end_at ? dateTimeLocalForZone(item.end_at, timezoneName) : "";
  const scheduleType = item?.schedule_type || "once";
  const selectedDestinations = item?.destination_ids || [];
  const name = clone ? `Копия — ${item.name}`.slice(0, 180) : item?.name || "";
  const connectionOptionsHtml = availableConnections.map((connection) => `<option value="${attr(connection.id)}" ${connection.id === connectionId ? "selected" : ""}>${escapeHtml(connection.name)}${connection.status !== "active" ? ` · ${escapeHtml(connectionStatuses[connection.status]?.label || connection.status)}` : ""}</option>`).join("");
  const templateOptionsHtml = availableTemplates.map((template) => `<option value="${attr(template.id)}" ${template.id === templateId ? "selected" : ""}>${escapeHtml(template.name)}${!template.is_active ? " · отключён" : ""}</option>`).join("");
  const secondaryTemplateOptionsHtml = `<option value="">Без A/B-теста</option>` + availableTemplates.map((template) => `<option value="${attr(template.id)}" ${template.id === secondaryTemplateId ? "selected" : ""} ${template.id === templateId ? "disabled" : ""}>${escapeHtml(template.name)}${!template.is_active ? " · отключён" : ""}</option>`).join("");
  openModal(
    editing ? "Изменить кампанию" : clone ? "Копировать кампанию" : "Создать кампанию",
    `<form id="campaign-form" data-id="${attr(editing ? item.id : "")}" data-mode="${editing ? "edit" : clone ? "clone" : "create"}">
      <div class="form-grid">
        <div class="form-group full"><label>Название</label><input class="input" name="name" required maxlength="180" value="${attr(name)}" placeholder="Вакансии — утренний проход"></div>
        <div class="form-group"><label>Подключение</label><select class="select" name="connection_id" id="campaign-connection" required ${editing ? "disabled" : ""}>${connectionOptionsHtml}</select>${editing ? `<small>Чтобы сменить подключение, создайте копию кампании.</small>` : ""}</div>
        <div class="form-group"><label>Шаблон</label><select class="select" name="template_id" required ${editing ? "disabled" : ""}>${templateOptionsHtml}</select>${editing ? `<small>Утверждённые снимки сообщений не изменяются.</small>` : ""}</div>
        <div class="form-group"><label>Вариант B <span class="cell-sub">необязательно</span></label><select class="select" name="secondary_template_id" id="campaign-secondary-template">${secondaryTemplateOptionsHtml}</select><small>A/B применяется для измерения отклика, а не обхода модерации.</small></div>
        <div class="form-group"><label>Доля варианта B, %</label><input class="input" name="secondary_template_weight" id="campaign-secondary-weight" type="number" min="1" max="99" value="${secondaryTemplateWeight}" ${secondaryTemplateId ? "" : "disabled"}><small>Назначение варианта стабильно для каждой группы.</small></div>
        <div class="form-group"><label>Тип расписания</label><select class="select" name="schedule_type" id="campaign-schedule-type"><option value="once" ${scheduleType === "once" ? "selected" : ""}>Однократно</option><option value="daily" ${scheduleType === "daily" ? "selected" : ""}>Ежедневно</option><option value="weekly" ${scheduleType === "weekly" ? "selected" : ""}>По дням недели</option></select></div>
        <div class="form-group"><label>Начало</label><input class="input" name="schedule_at" type="datetime-local" value="${attr(scheduleValue)}" required></div>
        <div class="form-group"><label>Окончание <span class="cell-sub">необязательно</span></label><input class="input" name="end_at" type="datetime-local" value="${attr(endValue)}"></div>
        <div class="form-group"><label>Часовой пояс</label><input class="input" name="timezone_name" value="${attr(timezoneName)}" required></div>
        <div class="form-group"><label>Интервал между группами, секунд</label><input class="input" name="spacing_seconds" type="number" min="1" max="86400" value="${item?.spacing_seconds || 90}" required></div>
        <div class="form-group"><label>Режим запуска</label><select class="select" name="rollout_mode" id="campaign-rollout-mode"><option value="staged" ${rolloutMode === "staged" ? "selected" : ""}>Пакетный контролируемый</option><option value="standard" ${rolloutMode === "standard" ? "selected" : ""}>Стандартный последовательный</option></select><small>Пакетный режим удерживает следующие группы до checkpoint.</small></div>
        <div class="form-group"><label>Защита от дубля, минут</label><input class="input" name="duplicate_guard_minutes" type="number" min="0" max="43200" value="${item?.duplicate_guard_minutes ?? 1380}"><small>0 отключает проверку. Рекомендуется не менее 1380 минут.</small></div>
        <div class="form-group full ${rolloutMode === "staged" ? "" : "hidden"}" id="campaign-rollout-settings">
          <div class="panel panel-soft"><div class="panel-header"><div><h3>Пакетный запуск</h3><p class="cell-sub">Первый пакет отправляется после утверждения, остальные физически удерживаются в очереди.</p></div></div>
            <div class="form-grid panel-body">
              <div class="form-group"><label>Групп в пакете</label><input class="input" name="rollout_batch_size" type="number" min="1" max="500" value="${item?.rollout_batch_size || 5}"></div>
              <div class="form-group"><label>Пауза между пакетами, секунд</label><input class="input" name="rollout_pause_seconds" type="number" min="0" max="86400" value="${item?.rollout_pause_seconds ?? 300}"></div>
              <div class="form-group"><label>Порог ошибок для остановки, %</label><input class="input" name="rollout_failure_threshold_percent" type="number" min="0" max="100" value="${item?.rollout_failure_threshold_percent ?? 20}"></div>
              <div class="form-group"><label class="checkbox-row"><input type="checkbox" name="rollout_require_checkpoint" ${item?.rollout_require_checkpoint !== false ? "checked" : ""}> Требовать ручной checkpoint после каждого пакета</label><small>Даже без обязательного checkpoint превышение порога ошибок остановит запуск.</small></div>
            </div>
          </div>
        </div>
        <div class="form-group full ${scheduleType === "weekly" ? "" : "hidden"}" id="weekdays-group"><label>Дни недели</label><div class="actions">${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day, index) => `<label class="checkbox-row"><input type="checkbox" name="weekdays" value="${index}" ${(item?.weekdays || []).includes(index) ? "checked" : ""}> ${day}</label>`).join("")}</div></div>
        <div class="form-group full"><label>Разрешённые назначения</label><div class="multi-select" id="campaign-destinations">${destinationChecks(connectionId, selectedDestinations)}</div></div>
        <div class="form-group full"><label>Примечание</label><textarea class="textarea" name="notes" placeholder="Цель и ответственный за запуск">${escapeHtml(item?.notes || "")}</textarea></div>
      </div>
      <div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">${editing ? "Сохранить как черновик" : clone ? "Создать копию" : "Создать черновик"}</button></div>
    </form>`,
    { large: true },
  );
}

/**
 * Выполнить opencampaignpreview, явно сохраняя побочные эффекты UI или worker.
 */
async function openCampaignPreview(id) {
  const preview = await api(`/campaigns/${id}/preview`);
  const blockers = preview.blockers.length ? `<div class="alert alert-danger mb-14"><strong>Запуск заблокирован</strong><br>${preview.blockers.map(escapeHtml).join("<br>")}</div>` : "";
  const warnings = preview.warnings.length ? `<div class="alert alert-warning mb-14"><strong>Предупреждения</strong><br>${preview.warnings.map(escapeHtml).join("<br>")}</div>` : "";
  const items = preview.items.map((item, index) => {
    const itemIssues = [
      ...(item.blockers || []).map((value) => `<div class="cell-sub text-danger">Блокировка: ${escapeHtml(value)}</div>`),
      ...(item.warnings || []).map((value) => `<div class="cell-sub text-warning">Внимание: ${escapeHtml(value)}</div>`),
    ].join("");
    return `<div class="panel mb-12"><div class="panel-header"><h3>${index + 1}. ${escapeHtml(item.destination_title)}</h3><span class="cell-sub">Пакет ${item.batch_number || 1} · вариант ${escapeHtml(item.template_variant)} · ${escapeHtml(item.template_name)} · ${formatDate(item.due_at)}</span></div><div class="panel-body"><div class="preview-message">${escapeHtml(item.body)}</div><div class="cell-sub mt-8">Content fingerprint: ${escapeHtml((item.content_fingerprint || "—").slice(0, 20))}${item.content_fingerprint ? "…" : ""}</div>${itemIssues}</div></div>`;
  }).join("");
  openModal("Предпросмотр кампании", `${blockers}${warnings}<div class="alert alert-info mb-14">Будет создано заданий: <strong>${preview.items.length}</strong>. Перед утверждением выполните сохраняемую preflight-проверку; worker повторит safety-проверки непосредственно перед доставкой.</div>${items || emptyState("▷", "Маршрут пуст", "Нет включённых назначений.")}<div class="form-actions"><button class="btn btn-secondary" data-action="close-modal">Закрыть</button>${can("owner", "admin", "operator") ? `<button class="btn btn-secondary" data-action="campaign-preflight" data-id="${attr(id)}">Запустить preflight</button>` : ""}${can("owner", "admin", "operator") && preview.valid ? `<button class="btn btn-success" data-action="campaign-submit-approval" data-id="${attr(id)}">Отправить на утверждение</button>` : ""}</div>`, { large: true });
}

/**
 * Выполнить renderpreflightreport, явно сохраняя побочные эффекты UI или worker.
 */
function renderPreflightReport(report) {
  const items = (report.items || []).map((item, index) => {
    const blockers = (item.blockers || []).map((value) => `<div class="cell-sub text-danger">${escapeHtml(value)}</div>`).join("");
    const warnings = (item.warnings || []).map((value) => `<div class="cell-sub text-warning">${escapeHtml(value)}</div>`).join("");
    return `<tr><td>${index + 1}</td><td><div class="cell-title">${escapeHtml(item.destination_title || item.destination_id)}</div><div class="cell-sub">${escapeHtml(item.destination_id)}</div></td><td>Пакет ${item.batch_number || 1}<div class="cell-sub">${formatDate(item.due_at)}</div></td><td><span class="mono-small">${escapeHtml((item.content_fingerprint || "").slice(0, 16))}…</span></td><td>${blockers || warnings || `<span class="cell-sub">Проверка пройдена</span>`}</td></tr>`;
  }).join("");
  const blockers = report.blockers?.length ? `<div class="alert alert-danger mb-14"><strong>Блокирующие причины</strong><br>${report.blockers.map(escapeHtml).join("<br>")}</div>` : "";
  const warnings = report.warnings?.length ? `<div class="alert alert-warning mb-14"><strong>Предупреждения</strong><br>${report.warnings.map(escapeHtml).join("<br>")}</div>` : "";
  const summary = report.summary || {};
  return `<div class="panel mb-14"><div class="panel-header"><div><h3>${badge(report.status, preflightStatuses)}</h3><p class="cell-sub">Отчёт ${escapeHtml(report.id.slice(0, 8))} · создан ${formatDate(report.created_at)} · действует до ${formatDate(report.expires_at)}</p></div></div><div class="panel-body"><div class="stats-grid stats-grid-compact"><div class="stat-card"><span>Назначений</span><strong>${summary.destination_count ?? report.items?.length ?? 0}</strong></div><div class="stat-card"><span>Пакетов</span><strong>${summary.total_batches ?? "—"}</strong></div><div class="stat-card"><span>Блокировок</span><strong>${report.blockers?.length || 0}</strong></div><div class="stat-card"><span>Предупреждений</span><strong>${report.warnings?.length || 0}</strong></div></div><div class="cell-sub mt-10">Fingerprint кампании: <span class="mono-small">${escapeHtml(report.fingerprint)}</span></div></div></div>${blockers}${warnings}<div class="table-wrap"><table><thead><tr><th>#</th><th>Назначение</th><th>Пакет и время</th><th>Content fingerprint</th><th>Результат</th></tr></thead><tbody>${items}</tbody></table></div>`;
}

/**
 * Выполнить opencampaignpreflight, явно сохраняя побочные эффекты UI или worker.
 */
async function openCampaignPreflight(id) {
  let report;
  try {
    report = await api(`/campaigns/${id}/preflight`, { method: "POST" });
  } catch (error) {
    if (error.status !== 422) throw error;
    report = await api(`/campaigns/${id}/preflight/latest`);
  }
  openModal("Предзапусковая проверка", `${renderPreflightReport(report)}<div class="form-actions"><button class="btn btn-secondary" data-action="close-modal">Закрыть</button>${can("owner", "admin", "operator") && ["passed", "warning"].includes(report.status) ? `<button class="btn btn-success" data-action="campaign-submit-approval" data-id="${attr(id)}">На утверждение</button>` : ""}</div>`, { large: true });
}

/**
 * Выполнить opencampaignruns, явно сохраняя побочные эффекты UI или worker.
 */
async function openCampaignRuns(id) {
  const runs = await api(`/campaigns/${id}/runs`);
  const rows = runs.map((run) => {
    const active = ["queued", "running", "awaiting_checkpoint"].includes(run.status);
    const progress = run.total_jobs ? Math.round((run.sent_jobs + run.failed_jobs) / run.total_jobs * 100) : 0;
    const actions = [
      can("owner", "admin") && run.status === "awaiting_checkpoint" && run.active_batch < run.total_batches ? button("Разрешить следующий пакет", "run-continue", `${id}|${run.id}`, "success") : "",
      can("owner", "admin") && active ? button("Остановить запуск", "run-abort", `${id}|${run.id}`, "danger") : "",
    ].join("");
    return `<tr><td><div class="cell-title">${formatDate(run.scheduled_for)}</div><div class="cell-sub">${escapeHtml(run.id.slice(0, 8))}</div></td><td>${badge(run.status, runStatuses)}<div class="cell-sub mt-5">${escapeHtml(run.checkpoint_reason || "")}</div></td><td><div class="cell-title">Пакет ${run.active_batch}/${run.total_batches}</div><div class="cell-sub">по ${run.batch_size} назначений · ${run.rollout_mode === "staged" ? "контролируемый" : "стандартный"}</div></td><td><div class="cell-title">${run.sent_jobs} отправлено · ${run.failed_jobs} ошибок</div><progress class="approval-progress" max="100" value="${progress}"></progress></td><td><div class="cell-sub">Checkpoint: ${run.checkpoint_required ? "обязателен" : "автоматический"}</div><div class="cell-sub">Порог ошибок: ${run.failure_threshold_percent}%</div></td><td><div class="actions">${actions || "—"}</div></td></tr>`;
  }).join("");
  openModal("Запуски кампании", `${runs.length ? `<div class="table-wrap"><table><thead><tr><th>Запуск</th><th>Статус</th><th>Пакет</th><th>Прогресс</th><th>Политика</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("▷", "Запусков ещё нет", "После постановки утверждённой кампании в очередь здесь появится история пакетов.")}<div class="form-actions"><button class="btn btn-secondary" data-action="close-modal">Закрыть</button></div>`, { large: true });
}

/**
 * Выполнить opendeliveryreview, явно сохраняя побочные эффекты UI или worker.
 */
async function openDeliveryReview(id) {
  const item = state.data.jobs.find((job) => job.id === id) || await api(`/jobs/${id}`);
  if (item.status !== "waiting_review" || !["DELIVERY_RESULT_UNCERTAIN", "WORKER_CRASH_DURING_SEND"].includes(item.error_code)) {
    throw new Error("Это задание не ожидает сверки неоднозначной доставки");
  }
  openModal("Ручная сверка доставки", `<form id="delivery-review-form" data-id="${attr(id)}"><div class="alert alert-danger mb-14"><strong>Не нажимайте повтор до проверки Telegram.</strong> Откройте целевую группу и убедитесь, появилось ли сообщение. Неоднозначный сетевой ответ не означает, что Telegram его не принял.</div><div class="form-group"><label>Результат проверки</label><select class="select" name="resolution" id="delivery-review-resolution" required><option value="confirmed_sent">Сообщение найдено в группе</option><option value="confirmed_not_sent">Сообщения точно нет — разрешить один повтор</option><option value="skipped">Пропустить без повтора</option></select></div><div class="form-group mt-12" id="delivery-review-message-id-group"><label>Telegram message ID</label><input class="input" name="telegram_message_id" required maxlength="100" placeholder="Например 15382"><small>Возьмите идентификатор из ссылки/журнала или укажите подтверждённое значение.</small></div><div class="form-group mt-12"><label>Комментарий сверки</label><textarea class="textarea" name="note" required minlength="5" maxlength="2000" placeholder="Где и как проверен результат"></textarea></div><div class="detail-list mt-14"><div><dt>Назначение</dt><dd>${escapeHtml(item.destination_id)}</dd></div><div><dt>Пакет</dt><dd>${item.batch_number}</dd></div><div><dt>Content fingerprint</dt><dd class="mono-small">${escapeHtml(item.content_fingerprint || "—")}</dd></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Зафиксировать результат</button></div></form>`);
}

/**
 * Выполнить opentotpconfirmmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openTotpConfirmModal(data) {
  openModal("Подключение TOTP", `<div class="qr-wrap"><img src="${attr(data.qr_data_uri)}" alt="QR-код TOTP"><div><p>Отсканируйте QR-код приложением-аутентификатором.</p><div class="code-box">${escapeHtml(data.provisioning_uri)}</div><p class="cell-sub">Подсказка секрета: ${escapeHtml(data.secret_hint)}</p></div></div><form id="totp-confirm-form" class="mt-20"><div class="form-group"><label>Шестизначный код</label><input class="input" name="code" inputmode="numeric" maxlength="6" pattern="[0-9]{6}" required></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Включить 2FA</button></div></form>`);
}

/**
 * Выполнить openusermodal, явно сохраняя побочные эффекты UI или worker.
 */
function openUserModal(item = null) {
  openModal(item ? "Изменить пользователя" : "Новый пользователь", `<form id="user-form" data-id="${attr(item?.id || "")}"><div class="form-grid"><div class="form-group"><label>Имя</label><input class="input" name="display_name" required value="${attr(item?.display_name || "")}"></div>${item ? "" : `<div class="form-group"><label>Email</label><input class="input" name="email" type="email" required></div><div class="form-group"><label>Временный пароль</label><input class="input" name="password" type="password" minlength="12" required autocomplete="new-password"></div>`}<div class="form-group"><label>Роль</label><select class="select" name="role"><option value="viewer" ${item?.role === "viewer" ? "selected" : ""}>Наблюдатель</option><option value="operator" ${item?.role === "operator" ? "selected" : ""}>Оператор</option><option value="admin" ${item?.role === "admin" ? "selected" : ""}>Администратор</option><option value="owner" ${item?.role === "owner" ? "selected" : ""}>Владелец</option></select></div>${item ? `<div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="is_active" ${item.is_active ? "checked" : ""}> Учётная запись активна</label><label class="checkbox-row"><input type="checkbox" name="must_change_password" ${item.must_change_password ? "checked" : ""}> Потребовать смену пароля</label></div>` : ""}</div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`);
}

/**
 * Выполнить openconversationmodal, явно сохраняя побочные эффекты UI или worker.
 */
async function openConversationModal(id) {
  const [conversation, messages] = await Promise.all([
    api(`/conversations/${id}`), api(`/conversations/${id}/messages?limit=500`),
  ]);
  const messageHtml = messages.map((message) => `<article class="message-bubble ${message.direction === "inbound" ? "inbound" : "outbound"}"><div class="message-meta"><span>${escapeHtml(message.author)}</span><span>${formatDate(message.created_at)}</span></div><div class="message-body" id="message-body-${attr(message.id)}">${escapeHtml(message.body_preview || "[Пустое сообщение]")}</div>${can("owner", "admin", "operator") ? `<button class="text-button" data-action="message-reveal" data-id="${attr(`${id}|${message.id}`)}">Показать полный текст</button>` : ""}</article>`).join("");
  const statusOptions = Object.entries(conversationStatuses).map(([value, meta]) => `<option value="${value}" ${conversation.status === value ? "selected" : ""}>${escapeHtml(meta.label)}</option>`).join("");
  openModal(`Диалог: ${contactName(conversation)}`, `<div class="conversation-layout"><section><div class="conversation-header"><div>${badge(conversation.status, conversationStatuses)} ${badge(conversation.consent_status, consentStatuses)}</div><div class="cell-sub">Telegram chat ${escapeHtml(conversation.telegram_chat_id)}</div></div><div class="message-stream">${messageHtml || emptyState("◌", "Сообщений нет", "В диалоге ещё нет сохранённых сообщений.")}</div></section><aside class="conversation-aside"><dl class="detail-list"><div><dt>Вакансия</dt><dd>${escapeHtml(conversation.vacancy_key || "—")}</dd></div><div><dt>AI</dt><dd>${conversation.ai_enabled ? "Разрешён" : "Отключён"}</dd></div><div><dt>Согласие</dt><dd>${escapeHtml(consentStatuses[conversation.consent_status]?.label || conversation.consent_status)}</dd></div><div><dt>Хранение до</dt><dd>${formatDate(conversation.retention_until)}</dd></div></dl>${can("owner", "admin", "operator") ? `<form id="conversation-settings-form" data-id="${attr(id)}" class="mt-20"><div class="form-group"><label>Статус</label><select class="select" name="status">${statusOptions}</select></div><div class="form-group mt-12"><label>Ключ вакансии</label><input class="input" name="vacancy_key" value="${attr(conversation.vacancy_key || "")}"></div><label class="checkbox-row mt-12"><input type="checkbox" name="ai_enabled" ${conversation.ai_enabled ? "checked" : ""}> Разрешить AI</label><button class="btn btn-secondary w-full mt-14" type="submit">Сохранить настройки</button></form><form id="conversation-reply-form" data-id="${attr(id)}" class="mt-20"><div class="form-group"><label>Ответ оператора</label><textarea class="textarea" name="text" maxlength="4096" required placeholder="Сообщение будет отправлено через Telegram Business"></textarea></div><button class="btn btn-primary w-full mt-12" type="submit">Отправить</button></form>` : ""}</aside></div>`, { large: true });
}

/**
 * Выполнить opencandidatemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openCandidateModal(item) {
  openModal("Карточка кандидата", `<form id="candidate-form" data-id="${attr(item.id)}"><div class="form-grid"><div class="form-group"><label>ФИО</label><input class="input" name="full_name" value="${attr(item.full_name || "")}"></div><div class="form-group"><label>Город</label><input class="input" name="city" value="${attr(item.city || "")}"></div><div class="form-group"><label>Возраст</label><input class="input" name="age" type="number" min="14" max="120" value="${attr(item.age || "")}"></div><div class="form-group"><label>Статус</label><select class="select" name="status">${Object.entries(candidateStatuses).map(([value, meta]) => `<option value="${value}" ${item.status === value ? "selected" : ""}>${escapeHtml(meta.label)}</option>`).join("")}</select></div><div class="form-group"><label>Телефон</label><input class="input" name="phone" autocomplete="off" placeholder="Оставьте пустым, чтобы не менять"></div><div class="form-group"><label>Email</label><input class="input" name="email" type="email" autocomplete="off" placeholder="Оставьте пустым, чтобы не менять"></div><div class="form-group full"><label>Опыт</label><textarea class="textarea" name="experience">${escapeHtml(item.experience || "")}</textarea></div><div class="form-group"><label>График</label><input class="input" name="schedule" value="${attr(item.schedule || "")}"></div><div class="form-group"><label>Ключ вакансии</label><input class="input" name="vacancy_key" value="${attr(item.vacancy_key || "")}"></div><div class="form-group full"><label>Резюме оператора</label><textarea class="textarea" name="summary">${escapeHtml(item.summary || "")}</textarea></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="consent_to_storage" ${item.consent_to_storage ? "checked" : ""}> Согласие на хранение подтверждено</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
}

/**
 * Выполнить listtotext, явно сохраняя побочные эффекты UI или worker.
 */
function listToText(values) {
  return (values || []).join(", ");
}
/**
 * Выполнить texttolist, явно сохраняя побочные эффекты UI или worker.
 */
function textToList(value) {
  return String(value || "").split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

/**
 * Выполнить flownodetypelabel, явно сохраняя побочные эффекты UI или worker.
 */
function flowNodeTypeLabel(type) {
  return ({
    message: "Сообщение",
    question: "Вопрос",
    choice: "Выбор",
    handoff: "Передача оператору",
    end: "Завершение",
  })[type] || type;
}

/**
 * Выполнить flowfieldlabel, явно сохраняя побочные эффекты UI или worker.
 */
function flowFieldLabel(field) {
  return ({
    full_name: "ФИО",
    city: "Город",
    age: "Возраст",
    experience: "Опыт",
    schedule: "График",
    phone: "Телефон",
    email: "Email",
    vacancy_key: "Ключ вакансии",
  })[field] || field;
}

/**
 * Выполнить flowfieldoptions, явно сохраняя побочные эффекты UI или worker.
 */
function flowFieldOptions(selected = "full_name") {
  return ["full_name", "city", "age", "experience", "schedule", "phone", "email", "vacancy_key"]
    .map((value) => `<option value="${value}" ${selected === value ? "selected" : ""}>${escapeHtml(flowFieldLabel(value))}</option>`)
    .join("");
}

/**
 * Выполнить newflownode, явно сохраняя побочные эффекты UI или worker.
 */
function newFlowNode(type = "question") {
  const id = `step_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
  const defaults = {
    message: "Здравствуйте! Я задам несколько вопросов.",
    question: "Введите ответ",
    choice: "Выберите подходящий вариант",
    handoff: "Передаю диалог специалисту.",
    end: "Спасибо. Анкета заполнена.",
  };
  return {
    id,
    type,
    text: defaults[type] || "",
    field: type === "choice" ? "schedule" : "full_name",
    validation: "text",
    optionsText: type === "choice" ? "Полный день|полный\nСменный|смены" : "",
    skip_if_present: true,
    completion_mode: "handoff",
  };
}

/**
 * Выполнить islinearflowdefinition, явно сохраняя побочные эффекты UI или worker.
 */
function isLinearFlowDefinition(definition) {
  const nodes = definition?.nodes || [];
  return nodes.every((node, index) => {
    const next = nodes[index + 1]?.id;
    if (["message", "question"].includes(node.type)) return node.next_node_id === next;
    if (node.type === "choice") return node.options?.every((option) => option.next_node_id === next);
    return index === nodes.length - 1 && ["handoff", "end"].includes(node.type);
  });
}

/**
 * Выполнить flowdraftfromitem, явно сохраняя побочные эффекты UI или worker.
 */
function flowDraftFromItem(item = null) {
  const source = item?.definition?.nodes || [newFlowNode("message"), newFlowNode("question"), newFlowNode("end")];
  const nodes = source.map((node) => ({
    id: node.id || newFlowNode(node.type).id,
    type: node.type,
    text: node.text || "",
    field: node.field || "full_name",
    validation: node.validation || "text",
    optionsText: (node.options || []).map((option) => `${option.label}${option.aliases?.length ? `|${option.aliases.join(",")}` : ""}`).join("\n"),
    skip_if_present: node.skip_if_present !== false,
    completion_mode: node.completion_mode || "handoff",
  }));
  return {
    id: item?.id || "",
    name: item?.name || "Анкета кандидата",
    description: item?.description || "Последовательный сбор данных с передачей оператору",
    is_active: Boolean(item?.is_active),
    revision: item?.revision || 1,
    nodes,
    readOnly: !can("owner", "admin") || Boolean(item?.is_active) || (item && !isLinearFlowDefinition(item.definition)),
    nonLinear: Boolean(item && !isLinearFlowDefinition(item.definition)),
  };
}

/**
 * Выполнить captureflowdraftfromdom, явно сохраняя побочные эффекты UI или worker.
 */
function captureFlowDraftFromDom() {
  const form = document.getElementById("flow-form");
  if (!form || !state.flowDraft) return;
  state.flowDraft.name = form.elements.name?.value || state.flowDraft.name;
  state.flowDraft.description = form.elements.description?.value || "";
  state.flowDraft.is_active = Boolean(form.elements.is_active?.checked);
  state.flowDraft.nodes = [...form.querySelectorAll(".flow-step")].map((element) => ({
    id: element.dataset.nodeId,
    type: element.querySelector('[name="node_type"]')?.value || "message",
    text: element.querySelector('[name="node_text"]')?.value || "",
    field: element.querySelector('[name="node_field"]')?.value || "full_name",
    validation: element.querySelector('[name="node_validation"]')?.value || "text",
    optionsText: element.querySelector('[name="node_options"]')?.value || "",
    skip_if_present: Boolean(element.querySelector('[name="node_skip"]')?.checked),
    completion_mode: element.querySelector('[name="node_completion"]')?.value || "handoff",
  }));
}

/**
 * Выполнить flowstephtml, явно сохраняя побочные эффекты UI или worker.
 */
function flowStepHtml(node, index, total, readOnly) {
  const controls = readOnly ? "" : `<div class="flow-step-actions"><button class="icon-button flow-drag-handle" type="button" draggable="true" data-flow-drag-node="${attr(node.id)}" title="Перетащить шаг" aria-label="Перетащить шаг">⋮⋮</button><button class="icon-button" type="button" data-action="flow-node-up" data-id="${index}" ${index === 0 ? "disabled" : ""} title="Выше">↑</button><button class="icon-button" type="button" data-action="flow-node-down" data-id="${index}" ${index === total - 1 ? "disabled" : ""} title="Ниже">↓</button><button class="icon-button danger" type="button" data-action="flow-node-remove" data-id="${index}" title="Удалить">×</button></div>`;
  const common = `<div class="form-group full"><label>Текст сообщения</label><textarea class="textarea" name="node_text" maxlength="4000" ${readOnly ? "disabled" : ""} required>${escapeHtml(node.text)}</textarea></div>`;
  let details = "";
  if (["question", "choice"].includes(node.type)) {
    const inferredValidation = ["age", "phone", "email"].includes(node.field) ? node.field : node.validation;
    details += `<div class="form-group"><label>Поле анкеты</label><select class="select" name="node_field" ${readOnly ? "disabled" : ""}>${flowFieldOptions(node.field)}</select></div><div class="form-group"><label>Проверка ответа</label><select class="select" name="node_validation" ${readOnly ? "disabled" : ""}><option value="text" ${inferredValidation === "text" ? "selected" : ""}>Текст</option><option value="age" ${inferredValidation === "age" ? "selected" : ""}>Возраст 14–120</option><option value="phone" ${inferredValidation === "phone" ? "selected" : ""}>Телефон</option><option value="email" ${inferredValidation === "email" ? "selected" : ""}>Email</option></select></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="node_skip" ${node.skip_if_present ? "checked" : ""} ${readOnly ? "disabled" : ""}> Не спрашивать повторно, если поле уже заполнено</label></div>`;
  }
  if (node.type === "choice") {
    details += `<div class="form-group full"><label>Варианты</label><textarea class="textarea code-input" name="node_options" ${readOnly ? "disabled" : ""} required>${escapeHtml(node.optionsText)}</textarea><small>Один вариант на строку: <code>Полный день|полный,полный график</code>. После выбора сценарий идёт к следующему шагу.</small></div>`;
  }
  if (node.type === "end") {
    details += `<div class="form-group full"><label>После анкеты</label><select class="select" name="node_completion" ${readOnly ? "disabled" : ""}><option value="handoff" ${node.completion_mode === "handoff" ? "selected" : ""}>Передать оператору</option><option value="ai" ${node.completion_mode === "ai" ? "selected" : ""}>Продолжить через AI</option></select></div>`;
  }
  return `<article class="flow-step" data-node-id="${attr(node.id)}"><div class="flow-step-head"><div class="flow-step-number">${index + 1}</div><div class="form-group flow-step-type"><label>Тип шага</label><select class="select" name="node_type" data-flow-node-type data-index="${index}" ${readOnly ? "disabled" : ""}><option value="message" ${node.type === "message" ? "selected" : ""}>Сообщение</option><option value="question" ${node.type === "question" ? "selected" : ""}>Вопрос</option><option value="choice" ${node.type === "choice" ? "selected" : ""}>Выбор</option><option value="handoff" ${node.type === "handoff" ? "selected" : ""}>Передача оператору</option><option value="end" ${node.type === "end" ? "selected" : ""}>Завершение</option></select></div>${controls}</div><div class="form-grid">${common}${details}</div>${index < total - 1 ? `<div class="flow-connector">↓</div>` : ""}</article>`;
}

/**
 * Выполнить renderfloweditor, явно сохраняя побочные эффекты UI или worker.
 */
function renderFlowEditor() {
  const target = document.getElementById("flow-editor");
  if (!target || !state.flowDraft) return;
  target.innerHTML = state.flowDraft.nodes.map((node, index) => flowStepHtml(node, index, state.flowDraft.nodes.length, state.flowDraft.readOnly)).join("");
  ensureAccessibleFormLabels(target);
}

/**
 * Выполнить clearflowdragclasses, явно сохраняя побочные эффекты UI или worker.
 */
function clearFlowDragClasses() {
  document.querySelectorAll(".flow-step.is-dragging, .flow-step.drag-over-before, .flow-step.drag-over-after").forEach((element) => {
    element.classList.remove("is-dragging", "drag-over-before", "drag-over-after");
  });
}

/**
 * Выполнить finishflowdrag, явно сохраняя побочные эффекты UI или worker.
 */
function finishFlowDrag() {
  if (!state.flowDragNodeId) return;
  captureFlowDraftFromDom();
  state.flowDragNodeId = null;
  clearFlowDragClasses();
  renderFlowEditor();
}

/**
 * Выполнить openflowmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openFlowModal(item = null) {
  state.flowDraft = flowDraftFromItem(item);
  const readOnly = state.flowDraft.readOnly;
  const warning = item?.is_active
    ? `<div class="alert alert-info mb-16">Активная ревизия доступна только для просмотра. Отключите использующую её политику, затем сценарий.</div>`
    : state.flowDraft.nonLinear
      ? `<div class="alert alert-warning mb-16">В сценарии есть ветвление. Этот интерфейс показывает его, но редактирование ветвящегося графа выполняется через API.</div>`
      : "";
  const addButtons = readOnly ? "" : `<div class="flow-add-bar"><span>Добавить шаг:</span><button class="btn btn-secondary btn-sm" type="button" data-action="flow-node-add" data-id="message">Сообщение</button><button class="btn btn-secondary btn-sm" type="button" data-action="flow-node-add" data-id="question">Вопрос</button><button class="btn btn-secondary btn-sm" type="button" data-action="flow-node-add" data-id="choice">Выбор</button><button class="btn btn-secondary btn-sm" type="button" data-action="flow-node-add" data-id="handoff">Оператор</button><button class="btn btn-secondary btn-sm" type="button" data-action="flow-node-add" data-id="end">Завершение</button></div>`;
  openModal(item ? `${readOnly ? "Просмотр" : "Изменение"} сценария` : "Новый сценарий", `${warning}<form id="flow-form" data-id="${attr(state.flowDraft.id)}" data-read-only="${readOnly}"><div class="form-grid mb-18"><div class="form-group"><label>Название</label><input class="input" name="name" value="${attr(state.flowDraft.name)}" ${readOnly ? "disabled" : ""} required></div><div class="form-group"><label class="checkbox-row flow-active-check"><input type="checkbox" name="is_active" ${state.flowDraft.is_active ? "checked" : ""} ${readOnly ? "disabled" : ""}> Активировать после сохранения</label></div><div class="form-group full"><label>Описание</label><textarea class="textarea" name="description" maxlength="4000" ${readOnly ? "disabled" : ""}>${escapeHtml(state.flowDraft.description)}</textarea></div></div>${addButtons}<div id="flow-editor" class="flow-editor"></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">${readOnly ? "Закрыть" : "Отмена"}</button>${readOnly ? "" : `<button class="btn btn-primary" type="submit">Сохранить сценарий</button>`}</div></form>`, { large: true });
  renderFlowEditor();
}

/**
 * Выполнить parseflowoptions, явно сохраняя побочные эффекты UI или worker.
 */
function parseFlowOptions(value, nextNodeId) {
  const lines = String(value || "").split(/\n/).map((item) => item.trim()).filter(Boolean);
  if (lines.length < 2) throw new Error("Для шага выбора укажите минимум два варианта");
  return lines.map((line) => {
    const [labelRaw, aliasesRaw = ""] = line.split("|", 2);
    const label = labelRaw.trim();
    if (!label) throw new Error("Название варианта не должно быть пустым");
    return {
      label,
      aliases: aliasesRaw.split(",").map((item) => item.trim()).filter(Boolean),
      next_node_id: nextNodeId,
    };
  });
}

/**
 * Выполнить buildflowdefinition, явно сохраняя побочные эффекты UI или worker.
 */
function buildFlowDefinition() {
  captureFlowDraftFromDom();
  const nodes = state.flowDraft?.nodes || [];
  if (nodes.length < 2) throw new Error("Добавьте минимум два шага");
  if (!["end", "handoff"].includes(nodes.at(-1).type)) throw new Error("Последним шагом должно быть завершение или передача оператору");
  if (nodes.slice(0, -1).some((node) => ["end", "handoff"].includes(node.type))) throw new Error("Терминальный шаг может быть только последним");
  const definitionNodes = nodes.map((node, index) => {
    const nextNodeId = nodes[index + 1]?.id;
    const base = { id: node.id, type: node.type, text: node.text.trim() };
    if (!base.text) throw new Error(`Заполните текст шага ${index + 1}`);
    if (["message", "question"].includes(node.type)) base.next_node_id = nextNodeId;
    if (["question", "choice"].includes(node.type)) {
      base.field = node.field;
      base.validation = ["age", "phone", "email"].includes(node.field) ? node.field : node.validation;
      base.skip_if_present = node.skip_if_present;
    }
    if (node.type === "choice") base.options = parseFlowOptions(node.optionsText, nextNodeId);
    if (node.type === "end") base.completion_mode = node.completion_mode;
    return base;
  });
  return { version: 1, start_node_id: definitionNodes[0].id, nodes: definitionNodes };
}

/**
 * Выполнить openpolicymodal, явно сохраняя побочные эффекты UI или worker.
 */
function openPolicyModal(item = null) {
  const bots = state.data.connections.filter((connection) => connection.kind === "bot");
  const providerOptions = `<option value="">Локальный rule-based</option>${state.data.providers.map((provider) => `<option value="${attr(provider.id)}" ${item?.ai_provider_config_id === provider.id ? "selected" : ""}>${escapeHtml(provider.name)}</option>`).join("")}`;
  const flowOptions = `<option value="">Без детерминированного сценария</option>${state.data.flows.filter((flow) => flow.is_active || flow.id === item?.automation_flow_id).map((flow) => `<option value="${attr(flow.id)}" ${item?.automation_flow_id === flow.id ? "selected" : ""}>${escapeHtml(flow.name)} · rev ${flow.revision}</option>`).join("")}`;
  const botOptions = bots.map((connection) => `<option value="${attr(connection.id)}" ${item?.telegram_connection_id === connection.id ? "selected" : ""}>${escapeHtml(connection.name)}</option>`).join("");
  openModal(item ? "Изменить политику" : "Новая политика", `<form id="policy-form" data-id="${attr(item?.id || "")}"><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="name" required value="${attr(item?.name || "HR Assistant")}"></div><div class="form-group"><label>Business-бот</label><select class="select" name="telegram_connection_id" ${item ? "disabled" : ""} required>${botOptions}</select></div><div class="form-group"><label>Сценарий анкеты</label><select class="select" name="automation_flow_id">${flowOptions}</select><small>Сценарий выполняется после согласия и до AI.</small></div><div class="form-group"><label>AI-провайдер после сценария</label><select class="select" name="ai_provider_config_id">${providerOptions}</select></div><div class="form-group"><label>Часовой пояс</label><input class="input" name="timezone_name" value="${attr(item?.timezone_name || "Europe/Helsinki")}" required></div><div class="form-group"><label>Ответов в сутки на диалог</label><input class="input" name="max_auto_replies_per_day" type="number" min="1" max="200" value="${item?.max_auto_replies_per_day || 20}"></div><div class="form-group"><label class="checkbox-row"><input type="checkbox" name="enabled" ${item?.enabled ? "checked" : ""}> Политика включена</label><label class="checkbox-row"><input type="checkbox" name="require_consent_before_ai" ${item?.require_consent_before_ai !== false ? "checked" : ""}> Сначала получить согласие</label></div><div class="form-group full"><label>Текст согласия</label><textarea class="textarea" name="consent_notice" required minlength="20">${escapeHtml(item?.consent_notice || "Согласны на автоматизированную обработку сообщений и хранение данных кандидата? Ответьте «да» или «нет».")}</textarea></div><div class="form-group full"><label>Fallback оператору</label><textarea class="textarea" name="fallback_message" required>${escapeHtml(item?.fallback_message || "Передаю диалог специалисту, он ответит вручную.")}</textarea></div><div class="form-group"><label>Ключевые слова handoff</label><input class="input" name="handoff_keywords" value="${attr(listToText(item?.handoff_keywords || ["оператор", "человек"]))}"></div><div class="form-group"><label>Стоп-слова</label><input class="input" name="stop_words" value="${attr(listToText(item?.stop_words || ["стоп", "отписаться"]))}"></div><div class="form-group full"><label>Активные часы, JSON</label><textarea class="textarea code-input" name="active_hours">${escapeHtml(JSON.stringify(item?.active_hours || {}, null, 2))}</textarea><small>Пустой объект означает круглосуточную работу. Например {"mon":["09:00","18:00"]}.</small></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
}

/**
 * Выполнить openprovidermodal, явно сохраняя побочные эффекты UI или worker.
 */
function openProviderModal(item = null) {
  const kind = item?.kind || "rule_based";
  openModal(item ? "Изменить AI-провайдер" : "Новый AI-провайдер", `<form id="provider-form" data-id="${attr(item?.id || "")}"><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="name" required value="${attr(item?.name || "")}"></div><div class="form-group"><label>Тип</label><select class="select" name="kind" id="provider-kind" ${item ? "disabled" : ""}><option value="rule_based" ${kind === "rule_based" ? "selected" : ""}>Rule-based локальный</option><option value="openai_compatible" ${kind === "openai_compatible" ? "selected" : ""}>OpenAI-compatible API</option></select></div><div class="form-group provider-external"><label>Base URL</label><input class="input" name="base_url" type="url" value="${attr(item?.base_url || "")}" placeholder="https://api.example.com/v1"></div><div class="form-group provider-external"><label>Модель</label><input class="input" name="model_name" value="${attr(item?.model_name || "")}" placeholder="gpt-5-mini"></div><div class="form-group provider-external"><label>API key</label><input class="input" name="api_key" type="password" autocomplete="off" placeholder="${item ? "Оставьте пустым, чтобы не менять" : "Секретный ключ"}"></div><div class="form-group"><label>Timeout, секунд</label><input class="input" name="timeout_seconds" type="number" min="1" max="120" value="${item?.timeout_seconds || 30}"></div><div class="form-group"><label>Max output tokens</label><input class="input" name="max_output_tokens" type="number" min="50" max="8000" value="${item?.max_output_tokens || 500}"></div><div class="form-group"><label>Temperature</label><input class="input" name="temperature" type="number" min="0" max="2" step="0.1" value="${item ? item.temperature_milli / 1000 : 0.2}"></div><div class="form-group full"><label>System prompt</label><textarea class="textarea textarea-lg" name="system_prompt">${escapeHtml(item?.system_prompt || "Собирай сведения о кандидате по одному вопросу. Не принимай решения о найме.")}</textarea></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="enabled" ${item?.enabled ? "checked" : ""}> Провайдер включён</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
  toggleProviderFields(kind);
}

/**
 * Выполнить toggleproviderfields, явно сохраняя побочные эффекты UI или worker.
 */
function toggleProviderFields(kind) {
  document.querySelectorAll(".provider-external").forEach((element) => element.classList.toggle("hidden", kind !== "openai_compatible"));
}

/**
 * Выполнить openknowledgemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openKnowledgeModal(item = null) {
  openModal(item ? "Изменить статью" : "Новая статья базы знаний", `<form id="knowledge-form" data-id="${attr(item?.id || "")}"><div class="form-grid"><div class="form-group full"><label>Название</label><input class="input" name="title" required value="${attr(item?.title || "")}"></div><div class="form-group"><label>Ключ вакансии</label><input class="input" name="vacancy_key" value="${attr(item?.vacancy_key || "")}" placeholder="пусто = общая статья"></div><div class="form-group"><label>Теги через запятую</label><input class="input" name="tags" value="${attr(listToText(item?.tags || []))}"></div><div class="form-group full"><label>Содержимое</label><textarea class="textarea textarea-lg" name="content" required>${escapeHtml(item?.content || "")}</textarea></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="is_active" ${item?.is_active !== false ? "checked" : ""}> Статья активна</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
}

/**
 * Выполнить openintegrationmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openIntegrationModal(item = null) {
  const kind = item?.kind || "webhook";
  openModal(item ? "Изменить интеграцию" : "Новая интеграция", `<form id="integration-form" data-id="${attr(item?.id || "")}"><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="name" required value="${attr(item?.name || "")}"></div><div class="form-group"><label>Тип</label><select class="select" name="kind" id="integration-kind" ${item ? "disabled" : ""}><option value="webhook" ${kind === "webhook" ? "selected" : ""}>Webhook</option><option value="google_sheets" ${kind === "google_sheets" ? "selected" : ""}>Google Sheets</option><option value="csv_export" ${kind === "csv_export" ? "selected" : ""}>CSV</option></select></div><div class="form-group full"><label>События через запятую</label><input class="input" name="event_types" value="${attr(listToText(item?.event_types || ["candidate.updated", "candidate.ready"]))}"><small>Пусто — все события.</small></div>${item ? `<div class="alert alert-info full">Зашифрованная конфигурация не возвращается из API. Для смены credentials удалите endpoint и создайте новый.</div>` : `<div class="form-group full integration-config integration-webhook"><label>Webhook URL</label><input class="input" name="webhook_url" type="url" placeholder="https://crm.example.com/hooks/teleflow"></div><div class="form-group full integration-config integration-google hidden"><label>Spreadsheet ID</label><input class="input" name="spreadsheet_id"></div><div class="form-group full integration-config integration-google hidden"><label>Service account JSON</label><textarea class="textarea code-input" name="service_account_json" placeholder='{"client_email":"...","private_key":"..."}'></textarea></div><div class="form-group full integration-config integration-csv hidden"><label>Относительный путь CSV</label><input class="input" name="relative_path" value="integrations/events.csv"></div>`}<div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="is_active" ${item?.is_active ? "checked" : ""}> Интеграция активна</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
  if (!item) toggleIntegrationFields(kind);
}

/**
 * Выполнить toggleintegrationfields, явно сохраняя побочные эффекты UI или worker.
 */
function toggleIntegrationFields(kind) {
  document.querySelectorAll(".integration-config").forEach((element) => element.classList.add("hidden"));
  const selector = kind === "google_sheets" ? ".integration-google" : kind === "csv_export" ? ".integration-csv" : ".integration-webhook";
  document.querySelectorAll(selector).forEach((element) => element.classList.remove("hidden"));
}

/**
 * Выполнить openprivacymodal, явно сохраняя побочные эффекты UI или worker.
 */
function openPrivacyModal() {
  const options = state.data.conversations.map((item) => `<option value="${attr(item.id)}">${escapeHtml(contactName(item))} · ${escapeHtml(item.telegram_chat_id)}</option>`).join("");
  openModal("Новый privacy-запрос", `<form id="privacy-form"><div class="alert alert-warning mb-16">Перед удалением проверьте личность заявителя. Удаление выполняется каскадно и не отменяется.</div><div class="form-grid"><div class="form-group"><label>Тип</label><select class="select" name="request_type"><option value="export">Экспорт данных</option>${state.user.role === "owner" ? `<option value="delete">Удаление данных</option>` : ""}</select></div><div class="form-group"><label>Диалог</label><select class="select" name="conversation_id" required>${options}</select></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать запрос</button></div></form>`);
}

/**
 * Выполнить openapikeymodal, явно сохраняя побочные эффекты UI или worker.
 */
function openApiKeyModal() {
  const scopes = [
    ["candidates:read", "Чтение кандидатов"], ["candidates:contacts", "Раскрытие контактов"],
    ["conversations:read", "Чтение диалогов"], ["events:write", "Создание событий"],
  ];
  openModal("Новый API-ключ", `<form id="api-key-form"><div class="form-group"><label>Название</label><input class="input" name="name" required placeholder="CRM production"></div><div class="form-group mt-14"><label>Scopes</label><div class="multi-select">${scopes.map(([value, label]) => `<label class="multi-option"><input type="checkbox" name="scopes" value="${value}"><span><strong>${escapeHtml(value)}</strong><span class="cell-sub block">${escapeHtml(label)}</span></span></label>`).join("")}</div></div><div class="form-group mt-14"><label>Истекает</label><input class="input" name="expires_at" type="datetime-local"><small>Пусто — без автоматического срока, ключ всё равно можно отозвать.</small></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать</button></div></form>`);
}

/**
 * Выполнить openpilotprogrammodal, явно сохраняя побочные эффекты UI или worker.
 */
function openPilotProgramModal() {
  const campaigns = state.data.campaigns.filter((item) => !["completed", "cancelled", "failed"].includes(item.status));
  const options = campaigns.map((item) => `<option value="${attr(item.id)}">${escapeHtml(item.name)} · ${item.destination_count} назначений</option>`).join("");
  if (!options) throw new Error("Сначала создайте кампанию для контролируемого пилота");
  openModal("Новая программа пилота", `<form id="pilot-program-form"><div class="alert alert-info mb-14">Система добавит локальный fake-этап, затем live-этапы. Первый live-этап всегда содержит одну служебную группу.</div><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="name" required minlength="3" maxlength="180" value="Контролируемый запуск"></div><div class="form-group"><label>Кампания</label><select class="select" name="campaign_id" required>${options}</select></div><div class="form-group full"><label>Размеры live-этапов</label><input class="input" name="stage_sizes" required value="1, 5, 20, 50, 100"><small>Допустимые возрастающие этапы: 1, 5, 20, 50 и 100. Маршрут кампании должен точно соответствовать текущему этапу.</small></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="require_distinct_signoff" checked> Live-этап принимает другой владелец или администратор</label></div><div class="form-group full"><label>Примечание</label><textarea class="textarea" name="notes" maxlength="4000" placeholder="Цель пилота, ответственные и критерии остановки"></textarea></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать программу</button></div></form>`, { large: true });
}

/**
 * Выполнить openpilotstagerunmodal, явно сохраняя побочные эффекты UI или worker.
 */
async function openPilotStageRunModal(programId, stageId) {
  const program = state.data.pilotPrograms.find((item) => item.id === programId);
  if (!program) throw new Error("Программа пилота не найдена");
  const runs = await api(`/campaigns/${program.campaign_id}/runs`);
  const options = runs.map((run) => `<option value="${attr(run.id)}">${formatDate(run.scheduled_for)} · ${escapeHtml(run.status)} · ${run.total_jobs} заданий</option>`).join("");
  if (!options) throw new Error("У кампании ещё нет запусков для фиксации доказательств");
  openModal("Привязать фактический запуск", `<form id="pilot-stage-run-form" data-program-id="${attr(programId)}" data-stage-id="${attr(stageId)}"><div class="alert alert-warning mb-14">Приёмка использует статусы всех delivery jobs, Telegram message ID и текущее состояние hash-chain. Тексты сообщений и контакты в акт не включаются.</div><div class="form-group"><label>Запуск кампании</label><select class="select" name="campaign_run_id" required>${options}</select></div><div class="form-group mt-14"><label>Комментарий оператора</label><textarea class="textarea" name="note" maxlength="2000" placeholder="Что проверено вручную"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Зафиксировать доказательства</button></div></form>`);
}

/**
 * Выполнить openpilotstagesignoffmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openPilotStageSignoffModal(programId, stageId) {
  openModal("Итоговое решение по этапу", `<form id="pilot-stage-signoff-form" data-program-id="${attr(programId)}" data-stage-id="${attr(stageId)}"><div class="alert alert-warning mb-14">Принять этап можно только без блокирующих результатов. Решение, пользователь и комментарий попадут в audit-chain.</div><div class="form-group"><label>Решение</label><select class="select" name="decision"><option value="passed">Этап принят</option><option value="failed">Этап не принят, программу приостановить</option></select></div><div class="form-group mt-14"><label>Комментарий</label><textarea class="textarea" name="note" required minlength="5" maxlength="4000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Зафиксировать решение</button></div></form>`);
}

/**
 * Выполнить openconfigurationexportmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openConfigurationExportModal() {
  openModal("Экспорт безопасной конфигурации", `<form id="bundle-export-form"><div class="alert alert-info mb-14"><strong>Секреты исключаются всегда.</strong> Telegram token, api_hash, MTProto StringSession, AI API keys, service account и утверждения кампаний в ZIP не попадут.</div><label class="checkbox-row"><input type="checkbox" name="include_media"> Включить бинарные медиаматериалы</label><p class="cell-sub mt-12">Без media архив меньше и удобнее для ревью. При включении каждый файл проверяется по сохранённому SHA-256.</p><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать архив</button></div></form>`);
}

/**
 * Выполнить openconfigurationimportmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openConfigurationImportModal() {
  openModal("Импорт конфигурации", `<form id="bundle-import-form"><div class="alert alert-warning mb-14"><strong>Импорт ничего не активирует автоматически.</strong> Подключения создаются без credentials, группы отключаются и требуют повторной проверки, кампании остаются черновиками.</div><div class="form-group"><label>ZIP TeleFlow</label><input class="input" type="file" name="file" accept=".zip,application/zip" required></div><div class="form-group mt-14"><label>Конфликты имён</label><select class="select" name="conflict_mode"><option value="rename">Создать копию с новым именем</option><option value="skip">Использовать существующий объект или пропустить</option></select></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Проверить и импортировать</button></div></form>`);
}

/**
 * Выполнить openrecoverypolicymodal, явно сохраняя побочные эффекты UI или worker.
 */
function openRecoveryPolicyModal() {
  const policy = state.data.recoveryStatus?.policy;
  if (!policy) throw new Error("Политика recovery не загружена");
  openModal("Политика RPO/RTO", `<form id="recovery-policy-form"><div class="alert alert-warning mb-14">В production обязательные шифрование, доверенная подпись и restore drill нельзя отключить через API.</div><div class="form-grid"><div class="form-group"><label>RPO, часов</label><input class="input" type="number" name="rpo_hours" min="1" max="720" value="${attr(policy.rpo_hours)}" required></div><div class="form-group"><label>RTO, минут</label><input class="input" type="number" name="rto_minutes" min="1" max="10080" value="${attr(policy.rto_minutes)}" required></div><div class="form-group"><label>Drill не старше, дней</label><input class="input" type="number" name="restore_drill_max_age_days" min="1" max="3650" value="${attr(policy.restore_drill_max_age_days)}" required></div><div class="form-group"><label>Минимум копий</label><input class="input" type="number" name="minimum_retained_backups" min="1" max="1000" value="${attr(policy.minimum_retained_backups)}" required></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="enabled" ${policy.enabled ? "checked" : ""}> Контролировать RPO/RTO</label><label class="checkbox-row mt-10"><input type="checkbox" name="require_encrypted_backup" ${policy.require_encrypted_backup ? "checked" : ""}> Требовать шифрование backup</label><label class="checkbox-row mt-10"><input type="checkbox" name="require_trusted_signature" ${policy.require_trusted_signature ? "checked" : ""}> Требовать доверенную Ed25519-подпись</label><label class="checkbox-row mt-10"><input type="checkbox" name="require_restore_drill" ${policy.require_restore_drill ? "checked" : ""}> Требовать актуальный restore drill</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
}

/**
 * Выполнить openrecoveryreceiptimportmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openRecoveryReceiptImportModal(kind) {
  const backup = kind === "backup";
  openModal(backup ? "Импорт backup receipt" : "Импорт restore-drill receipt", `<form id="${backup ? "recovery-backup-import-form" : "recovery-drill-import-form"}"><div class="alert alert-info mb-14">Загружается только небольшой подписанный JSON receipt. Сам backup-файл через веб не принимается и не сохраняется.</div><div class="form-group"><label>JSON receipt</label><input class="input" type="file" name="file" accept=".json,application/json" required></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Проверить и зарегистрировать</button></div></form>`);
}

/**
 * Выполнить openartifactkeygeneratemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openArtifactKeyGenerateModal() {
  openModal("Новый Ed25519-ключ", `<form id="artifact-key-generate-form"><div class="alert alert-warning mb-14"><strong>Приватная часть не будет показана или выгружена.</strong> Она сразу шифруется master key. Для резервного восстановления необходима резервная копия БД и корректная процедура ротации ключей.</div><div class="form-group"><label>Название</label><input class="input" name="name" required minlength="3" maxlength="160" value="Основной ключ артефактов"></div><label class="checkbox-row mt-14"><input type="checkbox" name="make_default" checked> Использовать для новых подписей</label><label class="checkbox-row mt-10"><input type="checkbox" name="trusted_for_import" checked> Доверять этому ключу при импорте</label><div class="form-group mt-14"><label>Примечание</label><textarea class="textarea" name="note" maxlength="2000" placeholder="Назначение и ответственное лицо"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать ключ</button></div></form>`);
}

/**
 * Выполнить openartifactkeyimportmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openArtifactKeyImportModal() {
  openModal("Добавить публичный Ed25519-ключ", `<form id="artifact-key-import-form"><div class="alert alert-info mb-14">Сверьте fingerprint с владельцем ключа по независимому каналу. Добавление публичного ключа не предоставляет доступ к приватной части.</div><div class="form-group"><label>Название источника</label><input class="input" name="name" required minlength="3" maxlength="160"></div><div class="form-group mt-14"><label>PEM или Base64 публичного ключа</label><textarea class="textarea code-box" name="public_key" required minlength="32" maxlength="4000" rows="8" placeholder="-----BEGIN PUBLIC KEY-----"></textarea></div><label class="checkbox-row mt-14"><input type="checkbox" name="trusted_for_import" checked> Считать ключ доверенным для импорта</label><div class="form-group mt-14"><label>Примечание</label><textarea class="textarea" name="note" maxlength="2000" placeholder="Как и кем проверен fingerprint"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Добавить ключ</button></div></form>`, { large: true });
}

/**
 * Выполнить openartifactkeyrevokemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openArtifactKeyRevokeModal(key) {
  openModal("Отозвать ключ подписи", `<form id="artifact-key-revoke-form" data-key-id="${attr(key.id)}"><div class="alert alert-danger mb-14"><strong>Отзыв необратим.</strong> Новые подписи этим ключом запрещаются, а входящие артефакты с его подписью будут блокироваться.</div><dl class="detail-list"><div><dt>Ключ</dt><dd>${escapeHtml(key.name)}</dd></div><div><dt>Fingerprint</dt><dd><code>${escapeHtml(key.fingerprint)}</code></dd></div></dl><div class="form-group mt-14"><label>Причина</label><textarea class="textarea" name="reason" required minlength="5" maxlength="2000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Отозвать ключ</button></div></form>`);
}

/**
 * Выполнить openartifactverifymodal, явно сохраняя побочные эффекты UI или worker.
 */
function openArtifactVerifyModal() {
  openModal("Проверить артефакт TeleFlow", `<form id="artifact-verify-form"><div class="alert alert-info mb-14">Поддерживаются configuration bundle ZIP, support bundle ZIP и JSON-акт приёмки пилота. Файл проверяется в памяти и не сохраняется.</div><div class="form-group"><label>ZIP или JSON</label><input class="input" type="file" name="file" accept=".zip,.json,application/zip,application/json" required></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Проверить подпись</button></div></form>`);
}

/**
 * Выполнить openartifactinspectionmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openArtifactInspectionModal(result) {
  const signature = result.signature || {};
  const statusClass = signature.trusted ? "alert-success" : signature.cryptographically_valid ? "alert-warning" : signature.signed ? "alert-danger" : "alert-warning";
  openModal("Результат проверки", `<div class="alert ${statusClass} mb-14"><strong>${result.integrity_valid ? "Целостность подтверждена" : "Целостность нарушена"}.</strong> ${escapeHtml(artifactSignatureStatuses[signature.status]?.label || signature.status || "Подпись отсутствует")}</div><dl class="detail-list"><div><dt>Тип</dt><dd>${escapeHtml(result.artifact_type)}</dd></div><div><dt>Размер</dt><dd>${formatBytes(result.size_bytes)}</dd></div><div><dt>SHA-256 файла</dt><dd><code>${escapeHtml(result.sha256)}</code></dd></div><div><dt>Fingerprint подписанта</dt><dd><code>${escapeHtml(signature.fingerprint || "—")}</code></dd></div><div><dt>Назначение подписи</dt><dd><code>${escapeHtml(signature.purpose || "—")}</code></dd></div><div><dt>Доверие</dt><dd>${signature.trusted ? "Публичный ключ доверен этой организацией" : "Публичный ключ не доверен этой организацией"}</dd></div></dl><div class="code-box mt-14">${escapeHtml(JSON.stringify(result.details || {}, null, 2))}</div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`, { large: true });
}

/**
 * Выполнить openslopolicymodal, явно сохраняя побочные эффекты UI или worker.
 */
function openSloPolicyModal() {
  const policy = state.data.operationsOverview?.policy;
  if (!policy) throw new Error("Политика SLO не загружена");
  openModal("Политика операционной надёжности", `<form id="slo-policy-form"><div class="alert alert-info mb-14">Изменение политики делает предыдущие SLO-оценки неактуальными. Production может требовать обязательные gates для публикаций и изменений.</div><div class="form-grid">
    <div class="form-group"><label>Окно оценки, часов</label><input class="input" name="evaluation_window_hours" type="number" min="1" max="720" value="${attr(policy.evaluation_window_hours)}" required></div>
    <div class="form-group"><label>Цель успешной доставки, %</label><input class="input" name="delivery_success_target_percent" type="number" min="50" max="100" step="0.01" value="${attr(Number(policy.delivery_success_target_bps) / 100)}" required></div>
    <div class="form-group"><label>Минимальная выборка</label><input class="input" name="minimum_delivery_sample_size" type="number" min="1" value="${attr(policy.minimum_delivery_sample_size)}" required></div>
    <div class="form-group"><label>Максимальный возраст очереди, сек.</label><input class="input" name="max_queue_age_seconds" type="number" min="1" value="${attr(policy.max_queue_age_seconds)}" required></div>
    <div class="form-group"><label>Максимальный возраст heartbeat, сек.</label><input class="input" name="max_worker_heartbeat_age_seconds" type="number" min="5" value="${attr(policy.max_worker_heartbeat_age_seconds)}" required></div>
    <div class="form-group"><label>Неоднозначных доставок допустимо</label><input class="input" name="max_unresolved_delivery_reviews" type="number" min="0" value="${attr(policy.max_unresolved_delivery_reviews)}" required></div>
    <div class="form-group"><label>Критических инцидентов допустимо</label><input class="input" name="max_open_critical_incidents" type="number" min="0" value="${attr(policy.max_open_critical_incidents)}" required></div>
    <div class="form-group"><label>Срок оценки, минут</label><input class="input" name="assessment_ttl_minutes" type="number" min="1" max="1440" value="${attr(policy.assessment_ttl_minutes)}" required></div>
    <div class="form-group"><label>Warning error budget, %</label><input class="input" name="error_budget_warning_percent" type="number" min="1" value="${attr(policy.error_budget_warning_percent)}" required></div>
    <div class="form-group"><label>Critical error budget, %</label><input class="input" name="error_budget_critical_percent" type="number" min="1" value="${attr(policy.error_budget_critical_percent)}" required></div>
    <div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="enabled" ${policy.enabled ? "checked" : ""}> Политика включена</label><label class="checkbox-row mt-10"><input type="checkbox" name="gate_publishing" ${policy.gate_publishing ? "checked" : ""}> Блокировать публикации при нарушении SLO</label><label class="checkbox-row mt-10"><input type="checkbox" name="gate_changes" ${policy.gate_changes ? "checked" : ""}> Блокировать production-изменения при нарушении SLO</label><label class="checkbox-row mt-10"><input type="checkbox" name="auto_create_incidents" ${policy.auto_create_incidents ? "checked" : ""}> Автоматически создавать критические инциденты</label><label class="checkbox-row mt-10"><input type="checkbox" name="auto_resolve_incidents" ${policy.auto_resolve_incidents ? "checked" : ""}> Автоматически разрешать восстановленные SLO-инциденты</label><label class="checkbox-row mt-10"><input type="checkbox" name="suppress_incidents_during_maintenance" ${policy.suppress_incidents_during_maintenance ? "checked" : ""}> Не создавать новые SLO-инциденты во время maintenance mode</label></div>
  </div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить политику</button></div></form>`, { large: true });
}

/**
 * Выполнить openincidentcreatemodal, явно сохраняя побочные эффекты UI или worker.
 */
function openIncidentCreateModal() {
  openModal("Новый операционный инцидент", `<form id="incident-create-form"><div class="form-grid"><div class="form-group full"><label>Название</label><input class="input" name="title" required minlength="3" maxlength="220"></div><div class="form-group"><label>Критичность</label><select class="select" name="severity"><option value="warning">Предупреждение</option><option value="critical">Критично</option><option value="info">Информация</option></select></div><div class="form-group"><label>Источник</label><input class="input" value="Ручной операторский инцидент" disabled></div><div class="form-group full"><label>Описание</label><textarea class="textarea" name="summary" required minlength="5" maxlength="8000"></textarea></div><div class="form-group full"><label>Влияние</label><textarea class="textarea" name="impact" maxlength="8000" placeholder="Какие пользователи, кампании или процессы затронуты"></textarea></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Создать инцидент</button></div></form>`, { large: true });
}

/**
 * Выполнить openincidentdetails, явно сохраняя побочные эффекты UI или worker.
 */
async function openIncidentDetails(id) {
  const item = state.data.incidents.find((incident) => incident.id === id) || await api(`/operations/incidents/${id}`);
  const events = await api(`/operations/incidents/${id}/events`);
  const eventRows = events.map((event) => `<div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-content"><strong>${escapeHtml(event.event_type)}</strong><p>${formatDate(event.created_at)}${event.actor_user_id ? ` · ${escapeHtml(event.actor_user_id.slice(0, 8))}…` : " · система"}</p><div>${escapeHtml(event.message || "—")}</div></div></div>`).join("");
  openModal(`Инцидент: ${item.title}`, `<div class="alert ${item.severity === "critical" ? "alert-danger" : item.severity === "warning" ? "alert-warning" : "alert-info"} mb-14"><strong>${escapeHtml(severityStatuses[item.severity]?.label || item.severity)} · ${escapeHtml(incidentStatuses[item.status]?.label || item.status)}</strong><br>${escapeHtml(item.summary)}</div><dl class="detail-list"><div><dt>Источник</dt><dd>${escapeHtml(incidentSources[item.source] || item.source)}</dd></div><div><dt>Обнаружен</dt><dd>${formatDate(item.detected_at)}</dd></div><div><dt>Влияние</dt><dd>${escapeHtml(item.impact || "—")}</dd></div><div><dt>Ответственный</dt><dd>${escapeHtml(item.owner_user_id || "—")}</dd></div><div><dt>Причина</dt><dd>${escapeHtml(item.root_cause || "—")}</dd></div><div><dt>Postmortem</dt><dd>${item.postmortem_url ? `<code>${escapeHtml(item.postmortem_url)}</code>` : "—"}</dd></div><div><dt>Разрешение</dt><dd>${escapeHtml(item.resolution_summary || "—")}</dd></div></dl><section class="mt-18"><h4>История</h4><div class="timeline mt-12">${eventRows || `<div class="empty p-28"><p>Событий нет</p></div>`}</div></section><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`, { large: true });
}

/**
 * Выполнить openincidentactionmodal, явно сохраняя побочные эффекты UI или worker.
 */
function openIncidentActionModal(item, action) {
  const labels = {
    acknowledge: ["Подтвердить инцидент", "Подтвердить", "warning"],
    mitigate: ["Начать устранение", "Начать устранение", "primary"],
    resolve: ["Разрешить инцидент", "Разрешить", "success"],
    close: ["Закрыть инцидент", "Закрыть", "success"],
    reopen: ["Повторно открыть инцидент", "Открыть снова", "warning"],
    comment: ["Добавить комментарий", "Добавить", "primary"],
  };
  const [title, submitLabel, cls] = labels[action] || ["Действие", "Сохранить", "primary"];
  const resolveFields = action === "resolve" ? `<div class="form-group mt-14"><label>Корневая причина</label><textarea class="textarea" name="root_cause" maxlength="12000"></textarea></div><div class="form-group mt-14"><label>URL postmortem</label><input class="input" name="postmortem_url" type="url" maxlength="1000"></div>` : "";
  openModal(title, `<form id="incident-action-form" data-id="${attr(item.id)}" data-incident-action="${attr(action)}"><div class="alert alert-info mb-14"><strong>${escapeHtml(item.title)}</strong><br>${escapeHtml(truncate(item.summary, 300))}</div><div class="form-group"><label>Комментарий</label><textarea class="textarea" name="note" required minlength="3" maxlength="8000"></textarea></div>${resolveFields}<div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-${cls}" type="submit">${submitLabel}</button></div></form>`, { large: action === "resolve" });
}

/**
 * Выполнить perform, явно сохраняя побочные эффекты UI или worker.
 */
async function perform(action, id) {
  try {
    if (action === "logout") {
      await api("/auth/logout", { method: "POST" });
      state.user = null;
      closeModal();
      renderLogin();
      return;
    }
    if (action === "analytics-export") {
      await downloadFile(`/analytics/export.csv?${analyticsQuery()}`, "teleflow-analytics.csv");
      toast("Аналитика экспортирована", "CSV сформирован без текста переписки и контактов");
      return;
    }
    if (action === "session-revoke") {
      if (!window.confirm("Завершить выбранную сессию?")) return;
      const sessions = await api("/auth/sessions");
      const current = sessions.find((item) => item.id === id)?.current;
      const result = await api(`/auth/sessions/${id}`, { method: "DELETE" });
      toast("Сессия завершена", result.message, current ? "warning" : "success");
      if (current) {
        state.user = null;
        closeModal();
        renderLogin();
        return;
      }
      return navigate("security");
    }
    if (action === "sessions-revoke-others") {
      if (!window.confirm("Завершить все сессии, кроме текущей?")) return;
      const result = await api("/auth/sessions/revoke-others", { method: "POST" });
      toast("Остальные сессии завершены", result.message);
      return navigate("security");
    }
    if (action === "refresh-page") return navigate(state.route);
    if (action === "capacity-assess") {
      const result = await api("/capacity/assessments", { method: "POST" });
      toast("Capacity-оценка сохранена", `${readinessStatuses[result.status]?.label || result.status} · ${result.active_jobs} активных jobs`, result.status === "blocked" ? "warning" : "success");
      return navigate("capacity");
    }
    if (action === "capacity-policy-edit") {
      const policy = state.data.capacityOverview?.policy || await api("/capacity/policy");
      return openModal("Политика нагрузки и backpressure", `<form id="capacity-policy-form"><div class="alert alert-warning mb-14"><strong>Лимиты применяются сервером.</strong> В production admission и dispatch нельзя отключить. Изменение policy делает прежний assessment неактуальным.</div><div class="form-grid"><div class="form-group"><label>Активных jobs</label><input class="input" name="max_active_jobs" type="number" min="1" max="1000000" value="${attr(policy.max_active_jobs)}" required></div><div class="form-group"><label>Готовых jobs</label><input class="input" name="max_ready_jobs" type="number" min="1" max="1000000" value="${attr(policy.max_ready_jobs)}" required></div><div class="form-group"><label>Processing jobs</label><input class="input" name="max_processing_jobs" type="number" min="1" max="100000" value="${attr(policy.max_processing_jobs)}" required></div><div class="form-group"><label>Активных runs</label><input class="input" name="max_active_runs" type="number" min="1" max="100000" value="${attr(policy.max_active_runs)}" required></div><div class="form-group"><label>Jobs одного run</label><input class="input" name="max_jobs_per_run" type="number" min="1" max="1000000" value="${attr(policy.max_jobs_per_run)}" required></div><div class="form-group"><label>Drain, сек.</label><input class="input" name="max_estimated_drain_seconds" type="number" min="60" max="2592000" value="${attr(policy.max_estimated_drain_seconds)}" required></div><div class="form-group"><label>Telegram starts/мин.</label><input class="input" name="max_network_starts_per_minute" type="number" min="1" max="100000" value="${attr(policy.max_network_starts_per_minute)}" required></div><div class="form-group"><label>Telegram starts/час</label><input class="input" name="max_network_starts_per_hour" type="number" min="1" max="10000000" value="${attr(policy.max_network_starts_per_hour)}" required></div><div class="form-group"><label>Warning, %</label><input class="input" name="warning_utilization_percent" type="number" min="1" max="99" value="${attr(policy.warning_utilization_percent)}" required></div><div class="form-group"><label>Admission block, %</label><input class="input" name="admission_block_utilization_percent" type="number" min="2" max="100" value="${attr(policy.admission_block_utilization_percent)}" required></div><div class="form-group"><label>Assessment TTL, мин.</label><input class="input" name="assessment_ttl_minutes" type="number" min="1" max="1440" value="${attr(policy.assessment_ttl_minutes)}" required></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="enabled" ${policy.enabled ? "checked" : ""}> Политика включена</label><label class="checkbox-row mt-10"><input type="checkbox" name="gate_admission" ${policy.gate_admission ? "checked" : ""}> Блокировать создание и раскрытие очереди</label><label class="checkbox-row mt-10"><input type="checkbox" name="gate_dispatch" ${policy.gate_dispatch ? "checked" : ""}> Блокировать Telegram dispatch до gateway</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
    }
    if (action === "operations-assess") {
      const result = await api("/operations/slo-assessments", { method: "POST" });
      toast("SLO-оценка сохранена", `${readinessStatuses[result.status]?.label || result.status} · ${result.blockers.length} блокировок · ${result.warnings.length} предупреждений`, result.status === "blocked" ? "warning" : "success");
      return navigate("operations");
    }
    if (action === "slo-policy-edit") return openSloPolicyModal();
    if (action === "incident-new") return openIncidentCreateModal();
    if (action === "incident-open") return openIncidentDetails(id);
    if (["incident-acknowledge", "incident-mitigate", "incident-resolve", "incident-close", "incident-reopen", "incident-comment"].includes(action)) {
      const item = state.data.incidents.find((incident) => incident.id === id);
      if (!item) throw new Error("Инцидент не найден");
      return openIncidentActionModal(item, action.replace("incident-", ""));
    }
    if (action === "recovery-policy-edit") return openRecoveryPolicyModal();
    if (action === "recovery-backup-import") return openRecoveryReceiptImportModal("backup");
    if (action === "recovery-drill-import") return openRecoveryReceiptImportModal("drill");
    if (action === "artifact-key-generate") return openArtifactKeyGenerateModal();
    if (action === "artifact-key-import") return openArtifactKeyImportModal();
    if (action === "artifact-verify") return openArtifactVerifyModal();
    if (action === "artifact-key-public") {
      const result = await api(`/artifact-signing-keys/${id}/public`);
      return openModal("Публичный ключ Ed25519", `<dl class="detail-list"><div><dt>Key ID</dt><dd><code>${escapeHtml(result.key_id)}</code></dd></div><div><dt>Fingerprint SHA-256</dt><dd><code>${escapeHtml(result.fingerprint)}</code></dd></div></dl><div class="code-box mt-14">${escapeHtml(result.public_key_pem)}</div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`, { large: true });
    }
    if (action === "artifact-key-default") {
      await api(`/artifact-signing-keys/${id}/default`, { method: "POST" });
      toast("Основной ключ изменён", "Новые артефакты будут подписываться выбранным ключом", "success");
      return navigate("artifactTrust");
    }
    if (action === "artifact-key-trust") {
      const key = state.data.signingKeys.find((item) => item.id === id);
      if (!key) throw new Error("Ключ не найден");
      await api(`/artifact-signing-keys/${id}`, { method: "PATCH", body: JSON.stringify({ trusted_for_import: !key.trusted_for_import }) });
      toast(key.trusted_for_import ? "Доверие отключено" : "Ключ добавлен в доверенные", key.fingerprint, key.trusted_for_import ? "warning" : "success");
      return navigate("artifactTrust");
    }
    if (action === "artifact-key-revoke") {
      const key = state.data.signingKeys.find((item) => item.id === id);
      if (!key) throw new Error("Ключ не найден");
      return openArtifactKeyRevokeModal(key);
    }
    if (action === "commissioning-run") {
      const result = await api("/commissioning/run", { method: "POST" });
      toast("Диагностика завершена", `${result.blockers.length} блокировок · ${result.warnings.length} предупреждений`, result.status === "blocked" ? "warning" : "success");
      return navigate("commissioning");
    }
    if (action === "pilot-program-new") return openPilotProgramModal();
    if (action === "pilot-stage-refresh") {
      const [programId, stageId] = id.split("|");
      await api(`/pilot/programs/${programId}/stages/${stageId}/refresh`, { method: "POST" });
      toast("Готовность этапа пересчитана");
      return navigate("commissioning");
    }
    if (action === "pilot-stage-start") {
      const [programId, stageId] = id.split("|");
      if (!window.confirm("Начать текущий этап пилота? Перед фактической отправкой запустите только утверждённую кампанию с точным числом назначений.")) return;
      await api(`/pilot/programs/${programId}/stages/${stageId}/start`, { method: "POST" });
      toast("Этап пилота начат");
      return navigate("commissioning");
    }
    if (action === "pilot-stage-attach") {
      const [programId, stageId] = id.split("|");
      return openPilotStageRunModal(programId, stageId);
    }
    if (action === "pilot-stage-signoff") {
      const [programId, stageId] = id.split("|");
      return openPilotStageSignoffModal(programId, stageId);
    }
    if (action === "pilot-program-report") {
      await downloadFile(`/pilot/programs/${id}/acceptance-report`, `teleflow-pilot-${id}-acceptance.json`);
      toast("Акт приёмки сформирован", "Проверьте SHA-256 manifest внутри файла");
      return;
    }
    if (action === "pilot-program-cancel") {
      if (!window.confirm("Отменить программу пилота и закрыть незавершённые этапы?")) return;
      const result = await api(`/pilot/programs/${id}/cancel`, { method: "POST" });
      toast("Программа отменена", result.message, "warning");
      return navigate("commissioning");
    }
    if (action === "bundle-export") return openConfigurationExportModal();
    if (action === "bundle-import") return openConfigurationImportModal();
    if (action === "bundle-download") {
      await downloadFile(`/configuration-bundles/${id}/download`, `teleflow-config-${id}.zip`);
      toast("Архив скачан");
      return;
    }
    if (action === "bundle-delete") {
      if (!window.confirm("Удалить конфигурационный архив из хранилища? Импортированные объекты останутся в системе.")) return;
      const result = await api(`/configuration-bundles/${id}`, { method: "DELETE" });
      toast("Архив удалён", result.message, "warning");
      return navigate("commissioning");
    }
    if (action === "business-webhook-setup") {
      const result = await api(`/business/bots/${id}/webhook`, { method: "POST", body: JSON.stringify({ drop_pending_updates: false }) });
      return openModal("Webhook настроен", `<div class="alert alert-success mb-14">Telegram webhook зарегистрирован.</div><dl class="detail-list"><div><dt>URL</dt><dd><code>${escapeHtml(result.webhook_url)}</code></dd></div><div><dt>Secret hint</dt><dd><code>${escapeHtml(result.secret_hint)}</code></dd></div><div><dt>Updates</dt><dd>${escapeHtml(result.allowed_updates.join(", "))}</dd></div></dl><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Готово</button></div>`);
    }
    if (action === "business-webhook-info") {
      const result = await api(`/business/bots/${id}/webhook`);
      return openModal("Webhook info", `<div class="code-box">${escapeHtml(JSON.stringify(result, null, 2))}</div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`);
    }
    if (action === "business-webhook-delete") {
      if (!window.confirm("Удалить webhook и остановить приём входящих Telegram Business updates?")) return;
      const result = await api(`/business/bots/${id}/webhook`, { method: "DELETE" });
      toast("Webhook удалён", result.message, "warning"); return navigate("business");
    }
    if (action === "business-toggle") {
      const item = state.data.businessConnections.find((value) => value.id === id);
      if (!item) throw new Error("Business connection не найден");
      await api(`/business/connections/${id}`, { method: "PATCH", body: JSON.stringify({ is_enabled: !item.is_enabled }) });
      toast(item.is_enabled ? "Автоматизация отключена" : "Автоматизация включена"); return navigate("business");
    }
    if (action === "conversation-open") return openConversationModal(id);
    if (action === "conversation-close") {
      if (!window.confirm("Закрыть диалог и отключить AI?")) return;
      const result = await api(`/conversations/${id}/close`, { method: "POST" }); toast("Диалог закрыт", result.message); return navigate("conversations");
    }
    if (action === "message-reveal") {
      const [conversationId, messageId] = id.split("|");
      const result = await api(`/conversations/${conversationId}/messages/${messageId}/body`);
      const element = document.getElementById(`message-body-${messageId}`);
      if (element) { element.textContent = result.body || "[Пустое сообщение]"; element.classList.add("revealed"); }
      toast("Полный текст раскрыт", "Действие записано в аудит", "warning"); return;
    }
    if (action === "candidate-edit") {
      const item = state.data.candidates.find((value) => value.id === id);
      if (!item) throw new Error("Карточка кандидата не найдена");
      return openCandidateModal(item);
    }
    if (action === "candidate-contact") {
      const result = await api(`/candidates/${id}/contact`);
      return openModal("Контакты кандидата", `<div class="alert alert-warning mb-14">Раскрытие контактов записано в аудит.</div><dl class="detail-list"><div><dt>Телефон</dt><dd>${escapeHtml(result.phone || "—")}</dd></div><div><dt>Email</dt><dd>${escapeHtml(result.email || "—")}</dd></div></dl><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`);
    }
    if (action === "candidates-export") { await downloadFile("/candidates-export.csv", "candidates.csv"); toast("CSV сформирован"); return; }
    if (action === "candidates-export-contacts") {
      if (!window.confirm("Экспортировать контакты кандидатов? Действие попадёт в аудит.")) return;
      await downloadFile("/candidates-export.csv?include_contacts=true", "candidates-with-contacts.csv"); toast("CSV с контактами сформирован", "Храните файл безопасно", "warning"); return;
    }
    if (action === "new-flow") return openFlowModal();
    if (action === "flow-edit") {
      const item = state.data.flows.find((value) => value.id === id);
      if (!item) throw new Error("Сценарий не найден");
      return openFlowModal(item);
    }
    if (action === "flow-toggle") {
      const item = state.data.flows.find((value) => value.id === id);
      if (!item) throw new Error("Сценарий не найден");
      if (item.is_active && !window.confirm("Отключить сценарий? Включённая политика должна быть отключена заранее.")) return;
      await api(`/automation/flows/${id}`, { method: "PATCH", body: JSON.stringify({ is_active: !item.is_active }) });
      toast(item.is_active ? "Сценарий отключён" : "Сценарий активирован");
      return navigate("flows");
    }
    if (action === "flow-delete") {
      if (!window.confirm("Удалить отключённый и не привязанный к политике сценарий?")) return;
      const result = await api(`/automation/flows/${id}`, { method: "DELETE" });
      toast("Сценарий удалён", result.message);
      return navigate("flows");
    }
    if (action === "flow-node-add") {
      captureFlowDraftFromDom();
      const node = newFlowNode(id || "question");
      const terminalIndex = state.flowDraft.nodes.findIndex((item) => ["end", "handoff"].includes(item.type));
      if (terminalIndex >= 0) state.flowDraft.nodes.splice(terminalIndex, 0, node);
      else state.flowDraft.nodes.push(node);
      renderFlowEditor();
      return;
    }
    if (action === "flow-node-remove") {
      captureFlowDraftFromDom();
      if (state.flowDraft.nodes.length <= 2) throw new Error("В сценарии должны остаться минимум два шага");
      state.flowDraft.nodes.splice(Number(id), 1);
      renderFlowEditor();
      return;
    }
    if (action === "flow-node-up" || action === "flow-node-down") {
      captureFlowDraftFromDom();
      const index = Number(id);
      const target = action === "flow-node-up" ? index - 1 : index + 1;
      if (target < 0 || target >= state.flowDraft.nodes.length) return;
      [state.flowDraft.nodes[index], state.flowDraft.nodes[target]] = [state.flowDraft.nodes[target], state.flowDraft.nodes[index]];
      renderFlowEditor();
      return;
    }
    if (action === "new-policy") return openPolicyModal();
    if (action === "policy-edit") return openPolicyModal(state.data.policies.find((value) => value.id === id));
    if (action === "policy-delete") {
      if (!window.confirm("Удалить отключённую политику?")) return;
      const result = await api(`/automation/policies/${id}`, { method: "DELETE" }); toast("Политика удалена", result.message); return navigate("automation");
    }
    if (action === "new-provider") return openProviderModal();
    if (action === "provider-edit") return openProviderModal(state.data.providers.find((value) => value.id === id));
    if (action === "provider-test") {
      const result = await api(`/automation/providers/${id}/test`, { method: "POST" });
      return openModal("Результат проверки AI", `<div class="alert alert-success mb-14">Провайдер ответил за ${result.latency_ms} мс.</div><dl class="detail-list"><div><dt>Провайдер</dt><dd>${escapeHtml(result.provider)}</dd></div><div><dt>Модель</dt><dd>${escapeHtml(result.model || "—")}</dd></div></dl><div class="preview-message mt-14">${escapeHtml(result.sample)}</div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`);
    }
    if (action === "provider-delete") {
      if (!window.confirm("Удалить AI-провайдер?")) return;
      const result = await api(`/automation/providers/${id}`, { method: "DELETE" }); toast("Провайдер удалён", result.message); return navigate("automation");
    }
    if (action === "new-knowledge") return openKnowledgeModal();
    if (action === "knowledge-edit") return openKnowledgeModal(state.data.knowledge.find((value) => value.id === id));
    if (action === "knowledge-delete") {
      if (!window.confirm("Удалить статью базы знаний?")) return;
      const result = await api(`/automation/knowledge/${id}`, { method: "DELETE" }); toast("Статья удалена", result.message); return navigate("automation");
    }
    if (action === "new-integration") return openIntegrationModal();
    if (action === "integration-edit") return openIntegrationModal(state.data.integrations.find((value) => value.id === id));
    if (action === "integration-test") {
      const result = await api(`/integrations/${id}/test`, { method: "POST" }); toast("Тест поставлен в outbox", result.id); return navigate("integrations");
    }
    if (action === "integration-delete") {
      if (!window.confirm("Удалить интеграцию и её credentials?")) return;
      const result = await api(`/integrations/${id}`, { method: "DELETE" }); toast("Интеграция удалена", result.message); return navigate("integrations");
    }
    if (action === "outbox-retry") {
      await api(`/integrations/outbox/${id}/retry`, { method: "POST" }); toast("Событие поставлено на повтор"); return navigate("integrations");
    }
    if (action === "new-privacy-request") return openPrivacyModal();
    if (action === "privacy-process") {
      await api(`/privacy/requests/${id}/process`, { method: "POST" }); toast("Privacy-запрос обработан"); return navigate("privacy");
    }
    if (action === "privacy-download") { await downloadFile(`/privacy/requests/${id}/download`, `privacy-export-${id}.json`); toast("Экспорт скачан"); return; }
    if (action === "retention-run") {
      if (!window.confirm("Запустить очистку данных, срок хранения которых истёк?")) return;
      const result = await api("/privacy/retention/run", { method: "POST" });
      toast("Retention завершён", `${result.conversations_scrubbed} диалогов, ${result.updates_scrubbed} updates, ${result.exports_deleted} экспортов`); return navigate("privacy");
    }
    if (action === "new-api-key") return openApiKeyModal();
    if (action === "api-key-revoke") {
      if (!window.confirm("Отозвать API-ключ? Это действие нельзя отменить.")) return;
      const result = await api(`/api-keys/${id}/revoke`, { method: "POST" }); toast("API-ключ отозван", result.message); return navigate("apiKeys");
    }
    if (action === "api-key-created-close") { closeModal(); return navigate("apiKeys"); }
    if (action === "new-bot-connection") return openBotConnectionModal();
    if (action === "new-user-connection") return openUserConnectionModal();
    if (action === "connection-edit") return openConnectionEditModal(state.data.connections.find((x) => x.id === id));
    if (action === "connection-discover") return openConnectionDiscoveryModal(id);
    if (action === "connection-health") {
      const result = await api(`/connections/${id}/health`, { method: "POST" });
      toast(result.ok ? "Подключение работает" : "Проверка не пройдена", result.error || result.identity?.display_name || "OK", result.ok ? "success" : "warning");
      return navigate("connections");
    }
    if (action === "connection-pause") {
      await api(`/connections/${id}/pause`, { method: "POST" }); toast("Подключение приостановлено"); return navigate("connections");
    }
    if (action === "connection-resume") {
      const acknowledge = window.confirm("Вы проверили аккаунт и официальный @SpamBot, если ранее было антиспам-ограничение?");
      await api(`/connections/${id}/resume`, { method: "POST", body: JSON.stringify({ acknowledge_manual_review: acknowledge }) }); toast("Подключение возобновлено"); return navigate("connections");
    }
    if (action === "connection-revoke") {
      if (!window.confirm("Отозвать подключение и уничтожить сохранённые секреты? Это действие необратимо.")) return;
      await api(`/connections/${id}`, { method: "DELETE" }); toast("Подключение отозвано"); return navigate("connections");
    }
    if (action === "new-destination") return openDestinationModal();
    if (action === "new-destination-import") return openDestinationImportModal();
    if (action === "destination-import-preview") return runDestinationImport(true);
    if (action === "destination-import-apply") return runDestinationImport(false);
    if (action === "destination-discovery-apply") return applyConnectionDiscovery();
    if (action === "destination-import-finish") { closeModal(); return navigate("destinations"); }
    if (action === "destination-export") {
      await downloadFile("/destinations/export.csv", "teleflow-destinations.csv");
      toast("Назначения экспортированы", "CSV можно отредактировать и импортировать обратно");
      return;
    }
    if (action === "destination-edit") return openDestinationModal(state.data.destinations.find((x) => x.id === id));
    if (action === "destination-validate") {
      const result = await api(`/destinations/${id}/validate`, { method: "POST" }); toast(result.ok ? "Назначение доступно" : "Проверка требует внимания", JSON.stringify(result.capabilities), result.ok ? "success" : "warning"); return navigate(state.route === "pilot" ? "pilot" : "destinations");
    }
    if (action === "destination-validation-history") return openDestinationValidationHistory(id);
    if (action === "destination-delete") {
      if (!window.confirm("Удалить назначение?")) return;
      const result = await api(`/destinations/${id}`, { method: "DELETE" }); toast("Готово", result.message); return navigate("destinations");
    }
    if (action === "pilot-validate-due") {
      const due = [...new Set((state.data.pilotOverview?.attention || []).filter((item) => item.issue.includes("проверка доступа Telegram")).map((item) => item.destination_id))];
      if (!due.length) { toast("Повторная проверка не требуется"); return; }
      let passed = 0; let failed = 0; let deferred = 0; let writeForbidden = 0;
      for (let index = 0; index < due.length; index += 50) {
        const result = await api("/destinations/validate-batch", { method: "POST", body: JSON.stringify({ destination_ids: due.slice(index, index + 50) }) });
        passed += result.passed; failed += result.failed; deferred += result.deferred; writeForbidden += result.write_forbidden;
        if (result.deferred) break;
      }
      toast("Проверка назначений завершена", `Доступны: ${passed}; ошибки: ${failed}; нет права писать: ${writeForbidden}; отложено: ${deferred}`, failed || writeForbidden || deferred ? "warning" : "success");
      return navigate("pilot");
    }
    if (action === "pilot-readiness") {
      const result = await api(`/pilot/readiness/${id}`, { method: "POST" });
      toast("Отчёт готовности создан", result.status === "blocked" ? `${result.blockers.length} блокирующих причин` : `${result.warnings.length} предупреждений`, result.status === "blocked" ? "warning" : "success");
      await navigate("pilot");
      return openReadinessReport(state.data.readinessReports.find((item) => item.id === result.id) || result);
    }
    if (action === "pilot-readiness-open") return openReadinessReport(state.data.readinessReports.find((item) => item.id === id));
    if (action === "pilot-stage-assess") {
      const requestedStage = state.data.pilotStage?.next_stage;
      if (!requestedStage) { toast("Финальный этап уже достигнут"); return; }
      const result = await api("/pilot/stage/assess", { method: "POST", body: JSON.stringify({ requested_stage: requestedStage }) });
      toast("Оценка этапа создана", result.status === "blocked" ? `${result.blockers.length} блокирующих причин` : `${result.warnings.length} предупреждений`, result.status === "blocked" ? "warning" : "success");
      await navigate("pilot");
      return openStageAssessment(state.data.stageAssessments.find((item) => item.id === result.id) || result);
    }
    if (action === "pilot-stage-assessment-open") return openStageAssessment(state.data.stageAssessments.find((item) => item.id === id));
    if (action === "pilot-stage-advance") {
      const assessment = state.data.stageAssessments.find((item) => item.id === id) || state.data.pilotStage?.latest_assessment;
      if (!assessment) throw new Error("Оценка этапа не найдена");
      const limit = state.data.pilotStage?.stage_limits?.[assessment.requested_stage] ?? "—";
      const phrase = `ПЕРЕЙТИ НА ЭТАП ${limit}`;
      return openModal("Подтвердить повышение масштаба", `<form id="pilot-stage-advance-form" data-assessment-id="${attr(assessment.id)}"><div class="alert alert-warning mb-16"><strong>Это увеличит максимально допустимый маршрут кампании.</strong> Перед подтверждением проверьте live-canary, результат предыдущего этапа и отсутствие жалоб или удалений публикаций.</div><div class="detail-list"><div><dt>Новый этап</dt><dd>${escapeHtml(pilotStageLabels[assessment.requested_stage])}</dd></div><div><dt>Лимит</dt><dd>${escapeHtml(limit)} назначений</dd></div></div><div class="form-group mt-16"><label>Комментарий</label><textarea class="textarea" name="note" required minlength="5" maxlength="1000" placeholder="Пилот проверен, результат приемлем"></textarea></div><div class="form-group mt-12"><label>Введите подтверждение</label><div class="code-box mb-8">${escapeHtml(phrase)}</div><input class="input" name="confirmation" required autocomplete="off"></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Повысить этап</button></div></form>`);
    }
    if (action === "pilot-stage-lower") {
      const current = state.data.pilotStage?.current_stage;
      const currentIndex = pilotStageOrder.indexOf(current);
      const options = pilotStageOrder.slice(0, Math.max(currentIndex, 0)).reverse().map((stage) => `<option value="${attr(stage)}">${escapeHtml(pilotStageLabels[stage])} · лимит ${state.data.pilotStage.stage_limits[stage]}</option>`).join("");
      if (!options) throw new Error("Понижение этапа недоступно");
      return openModal("Понизить допустимый масштаб", `<form id="pilot-stage-lower-form"><div class="alert alert-danger mb-16"><strong>Кампании выше нового лимита будут приостановлены.</strong> Ожидающие задания перейдут в ручную проверку; Telegram-запросы по ним выполняться не будут.</div><div class="form-group"><label>Новый этап</label><select class="select" name="target_stage" required>${options}</select></div><div class="form-group mt-12"><label>Причина</label><textarea class="textarea" name="reason" required minlength="5" maxlength="1000"></textarea></div><div class="form-group mt-12"><label>Подтверждение</label><small>Введите фразу вида «СНИЗИТЬ ЭТАП ДО N», где N — лимит выбранного этапа.</small><input class="input mt-8" name="confirmation" required autocomplete="off"></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Понизить этап</button></div></form>`);
    }
    if (action === "pilot-canary-new") {
      const connectionNames = Object.fromEntries(state.data.connections.map((item) => [item.id, item.name]));
      const eligible = state.data.destinations.filter((item) => item.enabled && item.permission_status === "confirmed");
      const options = eligible.map((item) => `<option value="${attr(item.id)}">${escapeHtml(item.title)} · ${escapeHtml(connectionNames[item.connection_id] || "подключение")}</option>`).join("");
      if (!options) throw new Error("Нет включённых назначений с подтверждённым разрешением");
      return openModal("Служебная canary-проверка", `<form id="pilot-canary-form"><div class="alert alert-warning mb-16"><strong>Будет отправлено одно фиксированное служебное сообщение.</strong> Рекламный текст вводить нельзя, автоматический retry отключён. Используйте только собственную служебную группу, где публикация разрешена.</div><div class="form-group"><label>Назначение</label><select class="select" name="destination_id" required>${options}</select></div><div class="form-group mt-12"><label>Введите подтверждение</label><div class="code-box mb-8">ОТПРАВИТЬ СЛУЖЕБНОЕ СООБЩЕНИЕ</div><input class="input" name="confirmation" required autocomplete="off"></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Отправить один раз</button></div></form>`);
    }
    if (action === "pilot-support-bundle-new") {
      return openModal("Создать диагностический архив", `<form id="pilot-support-bundle-form"><div class="alert alert-info mb-16"><strong>Архив обезличен и имеет ограниченный срок хранения.</strong> Он не содержит Bot token, api_hash, StringSession, тексты сообщений, телефоны, email и персональные имена.</div><div class="form-group"><label>Причина создания</label><textarea class="textarea" name="reason" required minlength="5" maxlength="1000" placeholder="Диагностика ошибки запуска пилота"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать архив</button></div></form>`);
    }
    if (action === "pilot-support-bundle-download") {
      await downloadFile(`/pilot/support-bundles/${id}/download`, `teleflow-support-${id}.zip`);
      toast("Диагностический архив скачан", "Не публикуйте его в открытом доступе");
      return;
    }
    if (action === "pilot-support-bundle-delete") {
      if (!window.confirm("Удалить диагностический архив досрочно?")) return;
      const result = await api(`/pilot/support-bundles/${id}`, { method: "DELETE" });
      toast("Архив удалён", result.message);
      return navigate("pilot");
    }
    if (action === "new-blackout") {
      await loadBase({ connections: true, destinations: true });
      return openBlackoutModal();
    }
    if (action === "blackout-edit") return openBlackoutModal(state.data.blackouts.find((item) => item.id === id));
    if (action === "blackout-toggle") {
      const item = state.data.blackouts.find((value) => value.id === id);
      if (!item) throw new Error("Запрет публикаций не найден");
      await api(`/blackouts/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: !item.enabled }) });
      toast(item.enabled ? "Запрет отключён" : "Запрет включён");
      return navigate("pilot");
    }
    if (action === "blackout-delete") {
      if (!window.confirm("Удалить правило операционного календаря?")) return;
      const result = await api(`/blackouts/${id}`, { method: "DELETE" });
      toast("Правило удалено", result.message);
      return navigate("pilot");
    }
    if (action === "upload-media") return openMediaModal();
    if (action === "media-delete") {
      if (!window.confirm("Удалить медиафайл?")) return;
      const result = await api(`/media/${id}`, { method: "DELETE" }); toast("Готово", result.message); return navigate("media");
    }
    if (action === "new-template") return openTemplateModal();
    if (action === "template-edit") return openTemplateModal(state.data.templates.find((x) => x.id === id));
    if (action === "template-delete") {
      if (!window.confirm("Удалить или отключить шаблон?")) return;
      const result = await api(`/templates/${id}`, { method: "DELETE" }); toast("Готово", result.message); return navigate("templates");
    }
    if (action === "new-campaign") {
      await loadBase({ connections: true, destinations: true, templates: true });
      return openCampaignModal();
    }
    if (action === "campaign-edit") return openCampaignModal(state.data.campaigns.find((item) => item.id === id));
    if (action === "campaign-clone") return openCampaignModal(state.data.campaigns.find((item) => item.id === id), true);
    if (action === "campaign-preview") return openCampaignPreview(id);
    if (action === "campaign-preflight") return openCampaignPreflight(id);
    if (action === "campaign-runs") return openCampaignRuns(id);
    if (action === "run-continue") {
      const [campaignId, runId] = id.split("|");
      return openModal("Разрешить следующий пакет", `<form id="run-checkpoint-form" data-campaign-id="${attr(campaignId)}" data-run-id="${attr(runId)}"><div class="alert alert-warning mb-14"><strong>Перед продолжением проверьте сообщения текущего пакета.</strong> Убедитесь, что нет удалений администраторами, жалоб, неожиданных дублей и заданий в ручной сверке.</div><div class="form-group"><label>Комментарий checkpoint</label><textarea class="textarea" name="note" required minlength="5" maxlength="2000" placeholder="Пакет проверен, результат приемлем"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Разрешить следующий пакет</button></div></form>`);
    }
    if (action === "run-abort") {
      const [campaignId, runId] = id.split("|");
      return openModal("Остановить пакетный запуск", `<form id="run-abort-form" data-campaign-id="${attr(campaignId)}" data-run-id="${attr(runId)}"><div class="alert alert-danger mb-14"><strong>Ожидающие и удержанные задания будут отменены.</strong> Утверждение кампании будет снято, а сама кампания перейдёт на паузу.</div><div class="form-group"><label>Причина</label><textarea class="textarea" name="reason" required minlength="5" maxlength="2000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Остановить запуск</button></div></form>`);
    }
    if (action === "campaign-submit-approval") {
      return openModal("Отправить кампанию на утверждение", `<form id="campaign-approval-submit-form" data-id="${attr(id)}"><div class="alert alert-info mb-14">Будет зафиксирован fingerprint текста, маршрута, расписания и правил групп. Любое последующее изменение потребует нового утверждения.</div><div class="form-group"><label>Комментарий проверяющим</label><textarea class="textarea" name="note" maxlength="4000" placeholder="Что проверено и на что обратить внимание"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Отправить</button></div></form>`);
    }
    if (action === "approval-approve") {
      const result = await api(`/campaigns/approval-requests/${id}/decision`, { method: "POST", body: JSON.stringify({ decision: "approve" }) });
      toast(result.status === "approved" ? "Кампания утверждена" : "Решение записано", `${result.decisions.length}/${result.required_approvals} решений`);
      await refreshChromeState(); return navigate("campaigns");
    }
    if (action === "approval-reject") {
      return openModal("Отклонить кампанию", `<form id="campaign-approval-reject-form" data-id="${attr(id)}"><div class="alert alert-warning mb-14">Отклонение возвращает кампанию в черновик и снимает прежнее разрешение запуска.</div><div class="form-group"><label>Причина</label><textarea class="textarea" name="note" required minlength="3" maxlength="4000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Отклонить</button></div></form>`);
    }
    if (action === "release-local") { const result=await api("/release-attestations/local",{method:"POST"}); toast("Текущая сборка подписана",`TeleFlow ${result.version}`); return navigate("releases"); }
    if (action === "release-verify") { const result=await api(`/release-attestations/${id}/verify`,{method:"POST"}); toast("Release attestation проверен",result.signature_status); return navigate("releases"); }
    if (action === "supply-policy-edit") {
      const policy = state.data.dependencyPolicy || await api("/supply-chain/policy");
      return openModal("Политика зависимостей", `<form id="dependency-policy-form"><div class="alert alert-info mb-14">Изменение политики делает прежние assessments непригодными для production gate до повторной оценки.</div><div class="form-grid"><div class="form-group"><label>&nbsp;</label><label class="checkbox-row"><input type="checkbox" name="require_exact_pins" ${policy.require_exact_pins ? "checked" : ""}> Требовать точные версии</label><label class="checkbox-row"><input type="checkbox" name="allow_prerelease" ${policy.allow_prerelease ? "checked" : ""}> Разрешать prerelease</label></div><div class="form-group"><label>&nbsp;</label><label class="checkbox-row"><input type="checkbox" name="require_vulnerability_scan" ${policy.require_vulnerability_scan ? "checked" : ""}> Требовать vulnerability scan</label><label class="checkbox-row"><input type="checkbox" name="require_trusted_report" ${policy.require_trusted_report ? "checked" : ""}> Требовать доверенную подпись</label></div><div class="form-group"><label>Максимум critical</label><input class="input" name="max_critical" type="number" min="0" max="10000" value="${policy.max_critical}"></div><div class="form-group"><label>Максимум high</label><input class="input" name="max_high" type="number" min="0" max="10000" value="${policy.max_high}"></div><div class="form-group"><label>Максимум medium</label><input class="input" name="max_medium" type="number" min="0" max="10000" value="${policy.max_medium}"></div><div class="form-group"><label>TTL отчёта, часов</label><input class="input" name="report_ttl_hours" type="number" min="1" max="720" value="${policy.report_ttl_hours}"></div><div class="form-group full"><label>Запрещённые пакеты</label><textarea class="textarea" name="denied_packages" placeholder="package-one, package-two">${escapeHtml((policy.denied_packages || []).join(", "))}</textarea><div class="cell-sub">Имена разделяются запятыми, пробелами или новыми строками.</div></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить политику</button></div></form>`, {large:true});
    }
    if (action === "supply-assessment-new") {
      const attestations = state.data.releaseAttestations.length ? state.data.releaseAttestations : await api("/release-attestations");
      if (!attestations.length) throw new Error("Сначала создайте release attestation");
      const options = attestations.map((item) => `<option value="${attr(item.id)}" ${item.id === id ? "selected" : ""}>TeleFlow ${escapeHtml(item.version)} · ${escapeHtml(item.payload_sha256.slice(0, 12))}…</option>`).join("");
      return openModal("Dependency assessment", `<form id="dependency-assessment-form"><div class="alert alert-warning mb-14"><strong>Вставляйте нормализованный результат доверенного scanner.</strong> Платформа не заявляет, что inventory-only выполнял онлайн-поиск CVE.</div><div class="form-grid"><div class="form-group full"><label>Release attestation</label><select class="select" name="attestation_id" required>${options}</select></div><div class="form-group"><label>Тип отчёта</label><select class="select" name="kind"><option value="vulnerability_scan">Vulnerability scan</option><option value="inventory_only">Inventory only</option></select></div><div class="form-group"><label>Scanner</label><input class="input" name="scanner_name" required minlength="2" maxlength="120" value="CI vulnerability scanner"></div><div class="form-group"><label>Версия scanner</label><input class="input" name="scanner_version" maxlength="80" value="1.0"></div><div class="form-group"><label>&nbsp;</label><label class="checkbox-row"><input type="checkbox" name="sign_with_default_key" checked disabled> Подписать основным Ed25519-ключом</label></div><div class="form-group full"><label>Findings JSON</label><textarea class="textarea code-input" name="findings" rows="12" required>[]</textarea><div class="cell-sub">Массив объектов: id, package, installed_version, severity, fixed_versions, aliases, url.</div></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать assessment</button></div></form>`, {large:true});
    }
    if (action === "supply-inventory") {
      const result = await api(`/supply-chain/assessments/${id}/inventory`, { method: "POST", body: JSON.stringify({ scanner_name: "teleflow-inventory", scanner_version: "2.1" }) });
      toast("Inventory assessment создан", result.status, result.status === "blocked" ? "warning" : "success");
      return navigate("supplyChain");
    }
    if (action === "supply-assessment-verify") {
      const result = await api(`/supply-chain/assessments/${id}/verify`, { method: "POST" });
      toast("Assessment проверен", result.status, result.status === "blocked" ? "warning" : "success");
      return navigate("supplyChain");
    }
    if (action === "supply-sbom") { await downloadFile(`/supply-chain/assessments/${id}/sbom`, `teleflow-sbom-${id}.cdx.json`); toast("CycloneDX SBOM скачан"); return; }
    if (action === "supply-sbom-release") { await downloadFile(`/supply-chain/attestations/${id}/sbom`, `teleflow-release-sbom.cdx.json`); toast("CycloneDX SBOM сформирован"); return; }
    if (action === "supply-publish") {
      return openModal("Опубликовать релиз", `<form id="release-transparency-publish-form" data-id="${attr(id)}"><div class="alert alert-warning mb-14">Событие попадёт в append-only transparency chain и будет подписано основным ключом организации.</div><div class="form-group"><label>Комментарий</label><textarea class="textarea" name="reason" maxlength="2000" placeholder="Сборка допущена к управляемому обновлению"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Опубликовать</button></div></form>`);
    }
    if (action === "supply-withdraw") {
      return openModal("Отозвать релиз", `<form id="release-transparency-withdraw-form" data-id="${attr(id)}"><div class="alert alert-danger mb-14">Отзыв блокирует использование этой сборки в новых production upgrades. Историческая запись не удаляется.</div><div class="form-group"><label>Причина отзыва</label><textarea class="textarea" name="reason" required minlength="5" maxlength="2000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Отозвать</button></div></form>`);
    }
    if (action === "supply-transparency-verify") {
      const result = await api("/supply-chain/transparency/verify");
      toast(result.valid ? "Transparency chain подтверждена" : "Нарушена целостность цепочки", result.valid ? `${result.entry_count} записей` : (result.errors || []).join("; "), result.valid ? "success" : "error");
      return navigate("supplyChain");
    }
    if (action === "change-new") {
      let attestations=[]; let assessments=[];
      try { [attestations, assessments] = await Promise.all([api("/release-attestations"), api("/supply-chain/assessments?limit=200")]); } catch (_) {}
      const attestationOptions = [`<option value="">Не привязан</option>`, ...attestations.map((x)=>`<option value="${attr(x.id)}">${escapeHtml(x.version)} · ${escapeHtml(x.signature_status)}</option>`)].join("");
      const assessmentOptions = [`<option value="">Не привязан — pre-check будет заблокирован</option>`, ...assessments.map((x)=>{ const release=attestations.find((item)=>item.id===x.release_attestation_id); return `<option value="${attr(x.id)}">${escapeHtml(release?.version || "—")} · ${escapeHtml(x.status)} · ${escapeHtml(x.report_sha256.slice(0,12))}…</option>`; })].join("");
      return openModal("Новое изменение", `<form id="change-create-form"><div class="form-grid"><div class="form-group"><label>Название</label><input class="input" name="title" required minlength="3" maxlength="180"></div><div class="form-group"><label>Тип</label><select class="select" name="change_type"><option value="upgrade">Обновление</option><option value="configuration">Конфигурация</option><option value="database_migration">Миграция БД</option><option value="infrastructure">Инфраструктура</option></select></div><div class="form-group"><label>Текущая версия</label><input class="input" name="current_version" value="2.5.0"></div><div class="form-group"><label>Целевая версия</label><input class="input" name="target_version" value="2.6.0"></div><div class="form-group full"><label>Release attestation</label><select class="select" name="release_attestation_id">${attestationOptions}</select><div class="cell-sub">Для production upgrade выберите доверенную подпись целевой сборки.</div></div><div class="form-group full"><label>Dependency assessment</label><select class="select" name="release_dependency_assessment_id">${assessmentOptions}</select><div class="cell-sub">Оценка должна относиться к выбранному attestation, соответствовать текущей policy и не быть просроченной.</div></div><div class="form-group full"><label>Причина</label><textarea class="textarea" name="reason" required minlength="5"></textarea></div><div class="form-group full"><label>Риски</label><textarea class="textarea" name="risk_summary" required minlength="5"></textarea></div><div class="form-group full"><label>План отката</label><textarea class="textarea" name="rollback_plan" required minlength="5"></textarea></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать</button></div></form>`, {large:true});
    }
    if (action === "change-approve") { if (!window.confirm("Утвердить изменение? Автор не может утверждать собственный change request.")) return; await api(`/changes/${id}/approve`, {method:"POST"}); toast("Изменение утверждено"); return navigate("changes"); }
    if (action === "change-start") { await api(`/changes/${id}/start`, {method:"POST"}); toast("Изменение начато"); return navigate("changes"); }
    if (action === "change-complete") { return openModal("Завершить изменение", `<form id="change-complete-form" data-id="${attr(id)}"><div class="form-group"><label>Результат</label><textarea class="textarea" name="note" maxlength="4000" placeholder="Что проверено после обновления"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Post-check и завершить</button></div></form>`); }
    if (action === "maintenance-start") { return openModal("Режим обслуживания", `<form id="maintenance-start-form"><div class="alert alert-warning mb-14">Scheduler и Safety Engine заблокируют публикации до завершения обслуживания.</div><div class="form-group"><label>Причина</label><textarea class="textarea" name="reason" required minlength="5" maxlength="1000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-warning" type="submit">Включить</button></div></form>`); }
    if (action === "maintenance-stop") { const result=await api("/changes/maintenance/stop",{method:"POST",body:JSON.stringify({note:"Обслуживание завершено оператором"})}); toast("Режим обслуживания завершён"); return navigate("changes"); }
    if (action === "continuity-new") {
      const execution = state.data.continuityExecution || await api("/execution/overview");
      const options = execution.sites.filter((item) => item.enabled && !item.is_active_site && item.online).map((item) => `<option value="${attr(item.site_key)}">${escapeHtml(item.display_name)} · ${escapeHtml(item.site_key)}</option>`).join("");
      if (!options) throw new Error("Нет доступной standby-площадки со свежим heartbeat");
      return openModal("Новое continuity-учение", `<form id="continuity-create-form"><div class="alert alert-warning mb-14"><strong>Live drill реально переключает active lease.</strong> Simulation не создаёт failover request и не вызывает Telegram.</div><div class="form-grid"><div class="form-group"><label>Режим</label><select class="select" name="mode"><option value="simulation">Simulation без сети</option><option value="live">Live failover/failback</option></select></div><div class="form-group"><label>Standby-площадка</label><select class="select" name="target_site_key" required>${options}</select></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Создать черновик</button></div></form>`);
    }
    if (action === "continuity-policy-edit") {
      const policy = state.data.continuityOverview?.policy || await api("/continuity/policy");
      return openModal("Политика непрерывности", `<form id="continuity-policy-form"><div class="alert alert-info mb-14">Изменение политики аннулирует незавершённые учения, связанные со старым fingerprint. В production live drill и независимая приёмка обязательны.</div><div class="form-grid"><div class="form-group"><label>Максимальный RTO, сек.</label><input class="input" type="number" name="max_rto_seconds" min="30" max="86400" value="${attr(policy.max_rto_seconds)}" required></div><div class="form-group"><label>Срок evidence, дней</label><input class="input" type="number" name="evidence_valid_days" min="1" max="365" value="${attr(policy.evidence_valid_days)}" required></div><div class="form-group full"><label class="checkbox-row"><input type="checkbox" name="enabled" ${policy.enabled ? "checked" : ""}> Политика включена</label><label class="checkbox-row mt-10"><input type="checkbox" name="require_live_drill" ${policy.require_live_drill ? "checked" : ""}> Для compliance требуется live drill</label><label class="checkbox-row mt-10"><input type="checkbox" name="require_distinct_signoff" ${policy.require_distinct_signoff ? "checked" : ""}> Автор учения не принимает собственное evidence</label></div></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-primary" type="submit">Сохранить</button></div></form>`, { large: true });
    }
    if (action === "continuity-start") {
      const item = state.data.continuityDrills.find((value) => value.id === id);
      if (!item) throw new Error("Continuity drill не найден");
      if (!window.confirm(item.mode === "live" ? "Начать реальный controlled failover? Telegram-публикации перейдут в draining до независимого подтверждения." : "Запустить simulation без изменения lease и сетевых вызовов?")) return;
      const result = await api(`/continuity/drills/${id}/start`, { method: "POST" });
      toast("Учение запущено", continuityDrillStatuses[result.status]?.label || result.status, item.mode === "live" ? "warning" : "success");
      return navigate("continuity");
    }
    if (action === "continuity-failback") {
      if (!window.confirm("Создать controlled failback и вернуть active lease на исходную площадку?")) return;
      const result = await api(`/continuity/drills/${id}/failback`, { method: "POST" });
      toast("Failback запрошен", result.failback_request_id || "Ожидает независимого подтверждения", "warning");
      return navigate("continuity");
    }
    if (action === "continuity-signoff") {
      const item = state.data.continuityDrills.find((value) => value.id === id);
      if (!item) throw new Error("Continuity drill не найден");
      return openModal("Решение по continuity evidence", `<form id="continuity-signoff-form" data-id="${attr(id)}"><div class="alert alert-warning mb-14">Перед приёмкой сервер повторно проверит evidence SHA-256, семантику полей и всю event hash-chain.</div><div class="form-group"><label>Решение</label><select class="select" name="accepted"><option value="true">Принять evidence</option><option value="false">Отклонить</option></select></div><div class="form-group mt-14"><label>Комментарий</label><textarea class="textarea" name="note" required minlength="3" maxlength="4000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Зафиксировать решение</button></div></form>`);
    }
    if (action === "continuity-cancel") {
      if (!window.confirm("Отменить незавершённое учение? После фактического failover сначала требуется failback.")) return;
      await api(`/continuity/drills/${id}/cancel`, { method: "POST" });
      toast("Учение отменено", "Execution lease проверен перед отменой", "warning");
      return navigate("continuity");
    }
    if (action === "continuity-events") {
      const events = await api(`/continuity/drills/${id}/events`);
      const rows = events.map((event) => `<tr><td>${event.sequence}</td><td><div class="cell-title">${escapeHtml(continuityEventLabels[event.event_type] || event.event_type)}</div><div class="cell-sub">${formatDate(event.created_at)}</div></td><td><code>${escapeHtml(event.previous_hash.slice(0, 12))}…</code></td><td><code>${escapeHtml(event.event_hash.slice(0, 12))}…</code></td><td><pre class="code-box">${escapeHtml(JSON.stringify(event.payload || {}, null, 2))}</pre></td></tr>`).join("");
      return openModal("Hash-linked история continuity", `<div class="table-wrap"><table><thead><tr><th>#</th><th>Событие</th><th>Previous</th><th>Hash</th><th>Payload</th></tr></thead><tbody>${rows}</tbody></table></div><div class="form-actions"><button class="btn btn-primary" data-action="close-modal">Закрыть</button></div>`, { large: true });
    }
    if (action === "continuity-verify") {
      const result = await api(`/continuity/drills/${id}/verify`);
      toast(result.valid ? "Continuity chain подтверждена" : "Continuity chain повреждена", result.valid ? `${result.checked_events} событий · ${result.last_hash.slice(0, 16)}…` : result.error, result.valid ? "success" : "error");
      return;
    }
    if (action === "execution-failover-new") {
      const overview = state.data.executionOverview || await api("/execution/overview");
      const options = overview.sites.filter((item) => item.online && !item.is_active_site && item.enabled).map((item) => `<option value="${attr(item.site_key)}">${escapeHtml(item.display_name)} · ${escapeHtml(item.site_key)}</option>`).join("");
      if (!options) throw new Error("Нет доступной standby-площадки со свежим heartbeat");
      return openModal("Переключить active-площадку", `<form id="execution-failover-create-form"><div class="alert alert-danger mb-14"><strong>Публикации перейдут в draining.</strong> Переключение завершит только другой Owner/Admin после проверки отсутствия активных и неопределённых доставок.</div><div class="form-group"><label>Целевая площадка</label><select class="select" name="target_site_key" required>${options}</select></div><div class="form-group mt-12"><label>Причина</label><textarea class="textarea" name="reason" required minlength="10" maxlength="2000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Начать draining</button></div></form>`);
    }
    if (action === "execution-failover-approve") {
      const item = state.data.failovers.find((value) => value.id === id);
      if (!item) throw new Error("Failover request не найден");
      const phrase = `ПЕРЕКЛЮЧИТЬ НА ${item.target_site_key}`;
      return openModal("Подтвердить failover", `<form id="execution-failover-approve-form" data-id="${attr(id)}"><div class="alert alert-warning mb-14">Будет создан новый fencing epoch, а предыдущие worker потеряют право выполнять Telegram-вызовы.</div><div class="form-group"><label>Введите точную фразу</label><input class="input" name="confirmation" required autocomplete="off" placeholder="${attr(phrase)}"></div><div class="cell-sub mt-8"><code>${escapeHtml(phrase)}</code></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Переключить</button></div></form>`);
    }
    if (action === "execution-failover-cancel") {
      if (!window.confirm("Отменить draining и вернуть исходную площадку в active-режим?")) return;
      await api(`/execution/failovers/${id}/cancel`, { method: "POST" });
      toast("Failover отменён", "Исходная площадка возвращена в active-режим", "warning");
      return navigate("execution");
    }
    if (action === "publishing-pause") {
      return openModal("Аварийно остановить публикации", `<form id="publishing-pause-form"><div class="alert alert-danger mb-14"><strong>Новые и ожидающие задания будут остановлены.</strong> Уже начатый сетевой запрос невозможно отозвать.</div><div class="form-group"><label>Причина остановки</label><textarea class="textarea" name="reason" required minlength="5" maxlength="1000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Остановить всё</button></div></form>`);
    }
    if (action === "publishing-resume") {
      return openModal("Возобновить публикации", `<form id="publishing-resume-form"><div class="alert alert-warning mb-14">Возобновляйте очередь только после проверки причины остановки и критических уведомлений.</div><div class="form-group"><label>Комментарий</label><textarea class="textarea" name="note" maxlength="1000"></textarea></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-success" type="submit">Возобновить</button></div></form>`);
    }
    if (action === "notification-read") { await api(`/notifications/${id}/read`, { method: "POST" }); await refreshChromeState(); return navigate("notifications"); }
    if (action === "notification-ack") {
      if (!window.confirm("Подтвердить, что событие проверено оператором?")) return;
      await api(`/notifications/${id}/acknowledge`, { method: "POST" }); await refreshChromeState(); toast("Событие подтверждено"); return navigate("notifications");
    }
    if (action === "notifications-read-all") { await api("/notifications/read-all", { method: "POST" }); await refreshChromeState(); return navigate("notifications"); }
    if (action === "audit-verify") return navigate("audit");
    if (action === "campaign-run-now") {
      if (!window.confirm("Поставить кампанию в очередь на ближайший цикл scheduler?")) return;
      await api(`/campaigns/${id}/run-now`, { method: "POST" }); closeModal(); toast("Запуск поставлен в очередь"); return navigate("campaigns");
    }
    if (action === "campaign-pause") {
      await api(`/campaigns/${id}/pause`, { method: "POST" }); toast("Кампания на паузе"); return navigate("campaigns");
    }
    if (action === "campaign-resume") {
      await api(`/campaigns/${id}/resume`, { method: "POST" }); toast("Кампания возобновлена"); return navigate("campaigns");
    }
    if (action === "campaign-cancel") {
      if (!window.confirm("Отменить кампанию и все ожидающие задания?")) return;
      const result = await api(`/campaigns/${id}/cancel`, { method: "POST" }); toast("Кампания отменена", result.message); return navigate("campaigns");
    }
    if (action === "job-resolve") return openDeliveryReview(id);
    if (action === "job-retry") {
      await api(`/jobs/${id}/retry`, { method: "POST" }); toast("Задание поставлено на повтор"); return navigate("jobs");
    }
    if (action === "job-cancel") {
      await api(`/jobs/${id}/cancel`, { method: "POST" }); toast("Задание отменено"); return navigate("jobs");
    }
    if (action === "totp-start") {
      const result = await api("/auth/totp/start", { method: "POST" }); return openTotpConfirmModal(result);
    }
    if (action === "totp-disable") {
      return openModal("Отключить 2FA", `<form id="totp-disable-form"><div class="alert alert-warning mb-14">Отключение снижает защиту административной панели.</div><div class="form-group"><label>Пароль</label><input class="input" name="password" type="password" required></div><div class="form-group mt-12"><label>Текущий код TOTP</label><input class="input" name="code" inputmode="numeric" maxlength="6" required></div><div class="form-actions"><button class="btn btn-secondary" type="button" data-action="close-modal">Отмена</button><button class="btn btn-danger" type="submit">Отключить</button></div></form>`);
    }
    if (action === "new-user") return openUserModal();
    if (action === "user-edit") return openUserModal(state.data.users.find((x) => x.id === id));
    if (action === "user-delete") {
      if (!window.confirm("Отключить пользователя?")) return;
      const result = await api(`/users/${id}`, { method: "DELETE" }); toast("Пользователь отключён", result.message); return navigate("users");
    }
  } catch (error) {
    toast("Операция не выполнена", error.message, "error");
  }
}

/**
 * Выполнить submitform, явно сохраняя побочные эффекты UI или worker.
 */
async function submitForm(form) {
  const submit = form.querySelector("button[type=submit]");
  if (submit) submit.disabled = true;
  try {
    if (form.id === "capacity-policy-form") {
      const data = formDataObject(form);
      const payload = {
        enabled: form.elements.enabled.checked,
        max_active_jobs: Number(data.max_active_jobs),
        max_ready_jobs: Number(data.max_ready_jobs),
        max_processing_jobs: Number(data.max_processing_jobs),
        max_active_runs: Number(data.max_active_runs),
        max_jobs_per_run: Number(data.max_jobs_per_run),
        max_network_starts_per_minute: Number(data.max_network_starts_per_minute),
        max_network_starts_per_hour: Number(data.max_network_starts_per_hour),
        max_estimated_drain_seconds: Number(data.max_estimated_drain_seconds),
        warning_utilization_percent: Number(data.warning_utilization_percent),
        admission_block_utilization_percent: Number(data.admission_block_utilization_percent),
        assessment_ttl_minutes: Number(data.assessment_ttl_minutes),
        gate_admission: form.elements.gate_admission.checked,
        gate_dispatch: form.elements.gate_dispatch.checked,
      };
      if (!(payload.max_processing_jobs <= payload.max_ready_jobs && payload.max_ready_jobs <= payload.max_active_jobs)) throw new Error("Требуется processing ≤ ready ≤ active");
      if (payload.max_jobs_per_run > payload.max_active_jobs) throw new Error("Jobs одного run не могут превышать общий active-лимит");
      if (payload.max_network_starts_per_hour < payload.max_network_starts_per_minute) throw new Error("Часовой сетевой лимит не может быть меньше минутного");
      if (payload.warning_utilization_percent >= payload.admission_block_utilization_percent) throw new Error("Warning-порог должен быть меньше admission block");
      await api("/capacity/policy", { method: "PATCH", body: JSON.stringify(payload) });
      closeModal(); toast("Capacity policy сохранена", "Следующая оценка будет связана с новым policy SHA-256", "warning"); return navigate("capacity");
    }
    if (form.id === "continuity-create-form") {
      const data = formDataObject(form);
      const result = await api("/continuity/drills", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Учение создано", `${continuityModes[result.mode]} · ${result.source_site_key} → ${result.target_site_key}`); return navigate("continuity");
    }
    if (form.id === "continuity-policy-form") {
      const data = formDataObject(form);
      await api("/continuity/policy", { method: "PATCH", body: JSON.stringify({
        enabled: form.elements.enabled.checked,
        require_live_drill: form.elements.require_live_drill.checked,
        max_rto_seconds: Number(data.max_rto_seconds),
        evidence_valid_days: Number(data.evidence_valid_days),
        require_distinct_signoff: form.elements.require_distinct_signoff.checked,
      }) });
      closeModal(); toast("Continuity policy сохранена", "Незавершённые evidence со старым fingerprint аннулированы", "warning"); return navigate("continuity");
    }
    if (form.id === "continuity-signoff-form") {
      const data = formDataObject(form);
      const result = await api(`/continuity/drills/${form.dataset.id}/signoff`, { method: "POST", body: JSON.stringify({ accepted: data.accepted === "true", note: data.note }) });
      closeModal(); toast(result.status === "passed" ? "Evidence принято" : "Evidence отклонено", continuityDrillStatuses[result.status]?.label || result.status, result.status === "passed" ? "success" : "warning"); return navigate("continuity");
    }
    if (form.id === "execution-failover-create-form") {
      const data = formDataObject(form);
      const result = await api("/execution/failovers", { method: "POST", body: JSON.stringify(data) });
      closeModal();
      toast("Draining включён", `${result.source_site_key} → ${result.target_site_key}`, "warning");
      return navigate("execution");
    }
    if (form.id === "execution-failover-approve-form") {
      const data = formDataObject(form);
      const result = await api(`/execution/failovers/${form.dataset.id}/approve`, { method: "POST", body: JSON.stringify({ confirmation: data.confirmation }) });
      closeModal();
      toast("Active-площадка переключена", `${result.target_site_key} · epoch ${result.target_epoch}`, "success");
      return navigate("execution");
    }
    if (form.id === "slo-policy-form") {
      const data = formDataObject(form);
      const payload = {
        enabled: form.elements.enabled.checked,
        evaluation_window_hours: Number(data.evaluation_window_hours),
        delivery_success_target_bps: Math.round(Number(data.delivery_success_target_percent) * 100),
        minimum_delivery_sample_size: Number(data.minimum_delivery_sample_size),
        max_queue_age_seconds: Number(data.max_queue_age_seconds),
        max_worker_heartbeat_age_seconds: Number(data.max_worker_heartbeat_age_seconds),
        max_unresolved_delivery_reviews: Number(data.max_unresolved_delivery_reviews),
        max_open_critical_incidents: Number(data.max_open_critical_incidents),
        error_budget_warning_percent: Number(data.error_budget_warning_percent),
        error_budget_critical_percent: Number(data.error_budget_critical_percent),
        assessment_ttl_minutes: Number(data.assessment_ttl_minutes),
        gate_publishing: form.elements.gate_publishing.checked,
        gate_changes: form.elements.gate_changes.checked,
        auto_create_incidents: form.elements.auto_create_incidents.checked,
        auto_resolve_incidents: form.elements.auto_resolve_incidents.checked,
        suppress_incidents_during_maintenance: form.elements.suppress_incidents_during_maintenance.checked,
      };
      if (payload.error_budget_warning_percent >= payload.error_budget_critical_percent) throw new Error("Warning-порог error budget должен быть меньше critical-порога");
      await api("/operations/slo-policy", { method: "PATCH", body: JSON.stringify(payload) });
      closeModal(); toast("Политика SLO сохранена", "Предыдущая оценка стала неактуальной", "warning"); return navigate("operations");
    }
    if (form.id === "incident-create-form") {
      const data = formDataObject(form);
      if (!data.impact) delete data.impact;
      const result = await api("/operations/incidents", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Инцидент создан", result.title, result.severity === "critical" ? "warning" : "success"); return navigate("operations");
    }
    if (form.id === "incident-action-form") {
      const data = formDataObject(form);
      const payload = { note: data.note };
      if (data.root_cause) payload.root_cause = data.root_cause;
      if (data.postmortem_url) payload.postmortem_url = data.postmortem_url;
      const result = await api(`/operations/incidents/${form.dataset.id}/${form.dataset.incidentAction}`, { method: "POST", body: JSON.stringify(payload) });
      closeModal(); toast("Инцидент обновлён", `${incidentStatuses[result.status]?.label || result.status}: ${result.title}`); return navigate("operations");
    }
    if (form.id === "dependency-policy-form") {
      const data = formDataObject(form);
      const denied = String(data.denied_packages || "").split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean);
      await api("/supply-chain/policy", { method: "PATCH", body: JSON.stringify({
        require_exact_pins: form.elements.require_exact_pins.checked,
        allow_prerelease: form.elements.allow_prerelease.checked,
        require_vulnerability_scan: form.elements.require_vulnerability_scan.checked,
        require_trusted_report: form.elements.require_trusted_report.checked,
        max_critical: Number(data.max_critical),
        max_high: Number(data.max_high),
        max_medium: Number(data.max_medium),
        report_ttl_hours: Number(data.report_ttl_hours),
        denied_packages: denied,
      }) });
      closeModal(); toast("Политика зависимостей сохранена", "Для изменённой политики требуется актуальный assessment", "warning"); return navigate("supplyChain");
    }
    if (form.id === "dependency-assessment-form") {
      const data = formDataObject(form);
      let findings;
      try { findings = JSON.parse(data.findings); } catch { throw new Error("Findings должны быть корректным JSON-массивом"); }
      if (!Array.isArray(findings)) throw new Error("Findings должны быть JSON-массивом");
      let attestation = state.data.releaseAttestations.find((item) => item.id === data.attestation_id);
      if (!attestation) {
        const attestations = await api("/release-attestations");
        attestation = attestations.find((item) => item.id === data.attestation_id);
      }
      if (!attestation) throw new Error("Release attestation не найден");
      const sbom = await api(`/supply-chain/attestations/${data.attestation_id}/sbom`);
      const sbomSha256 = await sha256Hex(canonicalJson(sbom));
      const report = {
        schema_version: 1,
        kind: data.kind,
        release_payload_sha256: attestation.payload_sha256,
        sbom_sha256: sbomSha256,
        generated_at: new Date().toISOString(),
        scanner: { name: data.scanner_name, version: data.scanner_version || null },
        findings,
      };
      const result = await api(`/supply-chain/assessments/${data.attestation_id}`, { method: "POST", body: JSON.stringify({ report, sign_with_default_key: true }) });
      closeModal(); toast("Dependency assessment создан", result.status, result.status === "blocked" ? "warning" : "success"); return navigate("supplyChain");
    }
    if (form.id === "release-transparency-publish-form" || form.id === "release-transparency-withdraw-form") {
      const data = formDataObject(form);
      const withdraw = form.id === "release-transparency-withdraw-form";
      const result = await api(`/supply-chain/transparency/${form.dataset.id}/${withdraw ? "withdraw" : "publish"}`, { method: "POST", body: JSON.stringify({ reason: data.reason || null }) });
      closeModal(); toast(withdraw ? "Релиз отозван" : "Релиз опубликован", `sequence ${result.sequence}`, withdraw ? "warning" : "success"); return navigate("supplyChain");
    }
    if (form.id === "change-create-form") {
      const data=formDataObject(form); if (!data.release_attestation_id) data.release_attestation_id=null; if (!data.release_dependency_assessment_id) data.release_dependency_assessment_id=null; const result=await api("/changes",{method:"POST",body:JSON.stringify(data)}); closeModal(); toast("Change request создан",result.title); return navigate("changes");
    }
    if (form.id === "maintenance-start-form") { const data=formDataObject(form); await api("/changes/maintenance/start",{method:"POST",body:JSON.stringify({reason:data.reason})}); closeModal(); toast("Режим обслуживания включён"); return navigate("changes"); }
    if (form.id === "change-complete-form") { const data=formDataObject(form); await api(`/changes/${form.dataset.id}/complete`,{method:"POST",body:JSON.stringify({note:data.note||null})}); closeModal(); toast("Изменение завершено"); return navigate("changes"); }
    if (form.id === "recovery-policy-form") {
      const data = formDataObject(form);
      const payload = {
        enabled: form.elements.enabled.checked,
        rpo_hours: Number(data.rpo_hours),
        rto_minutes: Number(data.rto_minutes),
        restore_drill_max_age_days: Number(data.restore_drill_max_age_days),
        minimum_retained_backups: Number(data.minimum_retained_backups),
        require_encrypted_backup: form.elements.require_encrypted_backup.checked,
        require_trusted_signature: form.elements.require_trusted_signature.checked,
        require_restore_drill: form.elements.require_restore_drill.checked,
      };
      await api("/recovery/policy", { method: "PATCH", body: JSON.stringify(payload) });
      closeModal(); toast("Политика восстановления сохранена"); return navigate("recovery");
    }
    if (form.id === "recovery-backup-import-form" || form.id === "recovery-drill-import-form") {
      const file = form.elements.file.files?.[0];
      if (!file) throw new Error("Выберите JSON receipt");
      const data = new FormData(); data.append("file", file);
      const backup = form.id === "recovery-backup-import-form";
      const result = await api(backup ? "/recovery/backups/import" : "/recovery/drills/import", { method: "POST", body: data });
      closeModal(); toast(backup ? "Backup receipt зарегистрирован" : "Restore drill зарегистрирован", backup ? `${result.backup_id} · ${result.status}` : `${result.drill_id} · ${result.status}`, result.status === "failed" ? "warning" : "success"); return navigate("recovery");
    }
    if (form.id === "artifact-key-generate-form") {
      const data = formDataObject(form);
      const result = await api("/artifact-signing-keys/generate", { method: "POST", body: JSON.stringify({
        name: data.name,
        make_default: form.elements.make_default.checked,
        trusted_for_import: form.elements.trusted_for_import.checked,
        note: data.note || null,
      }) });
      closeModal(); toast("Ключ подписи создан", `${result.fingerprint.slice(0, 20)}…`, "success"); return navigate("artifactTrust");
    }
    if (form.id === "artifact-key-import-form") {
      const data = formDataObject(form);
      const result = await api("/artifact-signing-keys/import", { method: "POST", body: JSON.stringify({
        name: data.name,
        public_key: data.public_key,
        trusted_for_import: form.elements.trusted_for_import.checked,
        note: data.note || null,
      }) });
      closeModal(); toast("Публичный ключ добавлен", `${result.fingerprint.slice(0, 20)}…`, result.trusted_for_import ? "success" : "warning"); return navigate("artifactTrust");
    }
    if (form.id === "artifact-key-revoke-form") {
      const data = formDataObject(form);
      await api(`/artifact-signing-keys/${form.dataset.keyId}/revoke`, { method: "POST", body: JSON.stringify({ reason: data.reason }) });
      closeModal(); toast("Ключ отозван", "Новые подписи и доверенный импорт этим ключом заблокированы", "warning"); return navigate("artifactTrust");
    }
    if (form.id === "artifact-verify-form") {
      const file = form.elements.file.files?.[0];
      if (!file) throw new Error("Выберите ZIP или JSON артефакт");
      const data = new FormData(); data.append("file", file);
      const result = await api("/artifact-signing-keys/verify-artifact", { method: "POST", body: data });
      return openArtifactInspectionModal(result);
    }
    if (form.id === "analytics-filter-form") {
      const data = formDataObject(form);
      if (data.date_from > data.date_to) throw new Error("Дата начала не может быть позже даты окончания");
      state.analyticsRange = {
        date_from: data.date_from,
        date_to: data.date_to,
        timezone_name: data.timezone_name,
      };
      return navigate("analytics");
    }
    if (form.id === "pilot-stage-advance-form") {
      const data = formDataObject(form);
      const result = await api("/pilot/stage/advance", { method: "POST", body: JSON.stringify({ assessment_id: form.dataset.assessmentId, confirmation: data.confirmation, note: data.note }) });
      closeModal(); toast("Этап повышен", `${pilotStageLabels[result.current_stage]} · лимит ${result.current_limit}`, "success"); return navigate("pilot");
    }
    if (form.id === "pilot-stage-lower-form") {
      const data = formDataObject(form);
      const expected = `СНИЗИТЬ ЭТАП ДО ${state.data.pilotStage.stage_limits[data.target_stage]}`;
      if (data.confirmation.trim() !== expected) throw new Error(`Введите точную фразу: ${expected}`);
      const result = await api("/pilot/stage/lower", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Этап понижен", `${pilotStageLabels[result.current_stage]} · кампании выше лимита приостановлены`, "warning"); return navigate("pilot");
    }
    if (form.id === "pilot-canary-form") {
      const data = formDataObject(form);
      const result = await api("/pilot/canaries", { method: "POST", body: JSON.stringify(data) });
      closeModal();
      toast(result.status === "sent" ? "Canary отправлена" : "Canary не отправлена", result.status === "sent" ? `${result.marker}${result.is_fake ? " · fake mode" : ""}` : `${result.error_code || "BLOCKED"}: ${result.error_message || "проверка заблокирована"}`, result.status === "sent" ? (result.is_fake ? "warning" : "success") : "warning");
      return navigate("pilot");
    }
    if (form.id === "pilot-support-bundle-form") {
      const data = formDataObject(form);
      const result = await api("/pilot/support-bundles", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast(result.status === "ready" ? "Диагностический архив создан" : "Архив создать не удалось", result.status === "ready" ? `Действует до ${formatDate(result.expires_at)}` : result.error_message || "Ошибка", result.status === "ready" ? "success" : "warning"); return navigate("pilot");
    }
    if (form.id === "pilot-program-form") {
      const data = formDataObject(form);
      const stageSizes = String(data.stage_sizes || "").split(/[;,\s]+/).filter(Boolean).map((value) => Number(value));
      if (!stageSizes.length || stageSizes.some((value) => !Number.isInteger(value))) throw new Error("Укажите целые размеры этапов");
      const payload = {
        name: data.name,
        campaign_id: data.campaign_id,
        stage_sizes: stageSizes,
        require_distinct_signoff: form.elements.require_distinct_signoff.checked,
        notes: data.notes || null,
      };
      await api("/pilot/programs", { method: "POST", body: JSON.stringify(payload) });
      closeModal(); toast("Программа пилота создана"); return navigate("commissioning");
    }
    if (form.id === "pilot-stage-run-form") {
      const data = formDataObject(form);
      await api(`/pilot/programs/${form.dataset.programId}/stages/${form.dataset.stageId}/attach-run`, { method: "POST", body: JSON.stringify({ campaign_run_id: data.campaign_run_id, note: data.note || null }) });
      closeModal(); toast("Доказательства запуска сохранены"); return navigate("commissioning");
    }
    if (form.id === "pilot-stage-signoff-form") {
      const data = formDataObject(form);
      await api(`/pilot/programs/${form.dataset.programId}/stages/${form.dataset.stageId}/signoff`, { method: "POST", body: JSON.stringify({ decision: data.decision, note: data.note }) });
      closeModal(); toast(data.decision === "passed" ? "Этап принят" : "Этап отклонён", data.note, data.decision === "passed" ? "success" : "warning"); return navigate("commissioning");
    }
    if (form.id === "bundle-export-form") {
      const result = await api("/configuration-bundles/export", { method: "POST", body: JSON.stringify({ include_media: form.elements.include_media.checked }) });
      closeModal();
      await downloadFile(`/configuration-bundles/${result.id}/download`, result.filename);
      toast("Безопасный архив создан", `${formatBytes(result.size_bytes)} · ${result.sha256.slice(0, 16)}…`);
      return navigate("commissioning");
    }
    if (form.id === "bundle-import-form") {
      const file = form.elements.file.files?.[0];
      if (!file) throw new Error("Выберите ZIP-архив");
      const previewData = new FormData(); previewData.append("file", file);
      const preview = await api("/configuration-bundles/preview", { method: "POST", body: previewData });
      const entityCount = Object.values(preview.summary || {}).reduce((sum, value) => sum + Number(value || 0), 0);
      const message = `Архив проверен. Объектов: ${entityCount}; конфликтов: ${preview.conflicts.length}; предупреждений: ${preview.warnings.length}. Продолжить безопасный импорт?`;
      if (!window.confirm(message)) return;
      const importData = new FormData(); importData.append("file", file);
      const result = await api(`/configuration-bundles/import?conflict_mode=${encodeURIComponent(form.elements.conflict_mode.value)}`, { method: "POST", body: importData });
      closeModal();
      const created = Object.values(result.created || {}).reduce((sum, value) => sum + Number(value || 0), 0);
      toast("Конфигурация импортирована", `${created} объектов создано; всё требующее credentials и разрешений оставлено выключенным`, "warning");
      return navigate("commissioning");
    }
    if (form.id === "flow-form") {
      if (form.dataset.readOnly === "true") throw new Error("Активный сценарий доступен только для просмотра");
      const definition = buildFlowDefinition();
      const payload = {
        name: state.flowDraft.name.trim(),
        description: state.flowDraft.description.trim() || null,
        is_active: state.flowDraft.is_active,
        definition,
      };
      if (!payload.name) throw new Error("Укажите название сценария");
      if (form.dataset.id) {
        await api(`/automation/flows/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      } else {
        await api("/automation/flows", { method: "POST", body: JSON.stringify(payload) });
      }
      closeModal(); toast("Сценарий сохранён"); return navigate("flows");
    }
    if (form.id === "bot-connection-form") {
      const data = formDataObject(form);
      data.min_interval_seconds = numberOrNull(data.min_interval_seconds);
      data.daily_cap = numberOrNull(data.daily_cap);
      data.destination_cooldown_minutes = numberOrNull(data.destination_cooldown_minutes);
      data.require_manual_approval = form.elements.require_manual_approval.checked;
      data.stop_on_flood = form.elements.stop_on_flood.checked;
      await api("/connections/bot", { method: "POST", body: JSON.stringify(data) }); closeModal(); toast("Бот подключён"); return navigate("connections");
    }
    if (form.id === "user-connection-start-form") {
      const data = formDataObject(form);
      data.api_id = Number(data.api_id); data.min_interval_seconds = numberOrNull(data.min_interval_seconds); data.daily_cap = numberOrNull(data.daily_cap); data.destination_cooldown_minutes = numberOrNull(data.destination_cooldown_minutes);
      data.require_manual_approval = true; data.stop_on_flood = true;
      const result = await api("/connections/user/start", { method: "POST", body: JSON.stringify(data) }); return openUserCodeModal(result);
    }
    if (form.id === "user-connection-complete-form") {
      const data = formDataObject(form); if (!data.password) delete data.password;
      await api("/connections/user/complete", { method: "POST", body: JSON.stringify(data) }); closeModal(); toast("Аккаунт авторизован"); return navigate("connections");
    }
    if (form.id === "connection-edit-form") {
      const data = formDataObject(form); data.min_interval_seconds = Number(data.min_interval_seconds); data.daily_cap = Number(data.daily_cap); data.destination_cooldown_minutes = Number(data.destination_cooldown_minutes); data.require_manual_approval = form.elements.require_manual_approval.checked; data.stop_on_flood = form.elements.stop_on_flood.checked;
      await api(`/connections/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) }); closeModal(); toast("Настройки сохранены"); return navigate("connections");
    }
    if (form.id === "destination-form") {
      const data = formDataObject(form);
      data.allowed_weekdays = [...form.querySelectorAll('input[name="allowed_weekdays"]:checked')].map((element) => Number(element.value));
      data.allowed_start_time = data.allowed_start_time || null;
      data.allowed_end_time = data.allowed_end_time || null;
      data.timezone_name = data.timezone_name || null;
      data.cooldown_minutes_override = numberOrNull(data.cooldown_minutes_override);
      data.permission_expires_at = data.permission_expires_at ? new Date(data.permission_expires_at).toISOString() : null;
      if ((data.allowed_start_time === null) !== (data.allowed_end_time === null)) throw new Error("Начало и окончание временного окна задаются вместе");
      if (data.allowed_start_time && data.allowed_start_time === data.allowed_end_time) throw new Error("Начало и окончание окна не должны совпадать");
      if (form.dataset.editing === "true") {
        data.enabled = form.elements.enabled.checked;
        if (!data.rules_url) data.rules_url = null;
        await api(`/destinations/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
      } else {
        data.telegram_chat_id = numberOrNull(data.telegram_chat_id); data.topic_id = numberOrNull(data.topic_id); data.permission_confirmed = form.elements.permission_confirmed.checked;
        if (data.permission_expires_at && !data.permission_confirmed) throw new Error("Срок разрешения можно указать только после подтверждения разрешения");
        if (!data.username) delete data.username; if (!data.title) delete data.title; if (!data.rules_url) delete data.rules_url;
        await api("/destinations", { method: "POST", body: JSON.stringify(data) });
      }
      closeModal(); toast("Назначение сохранено"); return navigate("destinations");
    }
    if (form.id === "blackout-form") {
      const data = formDataObject(form);
      const payload = {
        title: data.title,
        reason: data.reason,
        scope: data.scope,
        kind: data.kind,
        enabled: form.elements.enabled.checked,
      };
      if (data.scope === "connection") {
        if (!data.connection_id) throw new Error("Выберите Telegram-подключение");
        payload.connection_id = data.connection_id;
      } else if (data.scope === "destination") {
        if (!data.destination_id) throw new Error("Выберите группу или канал");
        payload.destination_id = data.destination_id;
      }
      if (data.kind === "one_time") {
        if (!data.starts_at || !data.ends_at) throw new Error("Укажите начало и окончание разового окна");
        payload.starts_at = new Date(data.starts_at).toISOString();
        payload.ends_at = new Date(data.ends_at).toISOString();
        if (payload.ends_at <= payload.starts_at) throw new Error("Окончание должно быть позже начала");
        payload.weekdays = [];
      } else {
        payload.timezone_name = data.timezone_name;
        payload.weekdays = [...form.querySelectorAll('input[name="blackout_weekdays"]:checked')].map((element) => Number(element.value));
        payload.start_time = data.start_time || null;
        payload.end_time = data.end_time || null;
        if (!payload.timezone_name) throw new Error("Укажите часовой пояс еженедельного окна");
        if (!payload.weekdays.length) throw new Error("Выберите дни еженедельного окна");
        if (!payload.start_time || !payload.end_time) throw new Error("Укажите начало и окончание еженедельного окна");
        if (payload.start_time === payload.end_time) throw new Error("Начало и окончание окна не должны совпадать");
      }
      if (form.dataset.id) await api(`/blackouts/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      else await api("/blackouts", { method: "POST", body: JSON.stringify(payload) });
      closeModal(); toast("Операционный календарь обновлён"); return navigate("pilot");
    }
    if (form.id === "media-form") {
      const data = new FormData(form);
      await api("/media", { method: "POST", body: data }); closeModal(); toast("Файл загружен"); return navigate("media");
    }
    if (form.id === "template-form") {
      const data = formDataObject(form); data.link_preview = form.elements.link_preview.checked; if (!data.media_asset_id) data.media_asset_id = null;
      if (form.dataset.id) { data.is_active = form.elements.is_active.checked; await api(`/templates/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) }); }
      else await api("/templates", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Шаблон сохранён"); return navigate("templates");
    }
    if (form.id === "campaign-form") {
      const data = formDataObject(form);
      data.spacing_seconds = Number(data.spacing_seconds);
      data.rollout_batch_size = Number(data.rollout_batch_size);
      data.rollout_pause_seconds = Number(data.rollout_pause_seconds);
      data.rollout_require_checkpoint = form.elements.rollout_require_checkpoint.checked;
      data.rollout_failure_threshold_percent = Number(data.rollout_failure_threshold_percent);
      data.duplicate_guard_minutes = Number(data.duplicate_guard_minutes);
      data.secondary_template_id = data.secondary_template_id || null;
      data.secondary_template_weight = data.secondary_template_id ? Number(data.secondary_template_weight) : 0;
      data.destination_ids = [...form.querySelectorAll('input[name="destination_ids"]:checked')].map((el) => el.value);
      data.weekdays = [...form.querySelectorAll('input[name="weekdays"]:checked')].map((el) => Number(el.value));
      data.end_at = data.end_at || null;
      if (!data.notes) data.notes = null;
      if (!data.destination_ids.length) throw new Error("Выберите хотя бы одно разрешённое назначение");
      if (data.schedule_type === "weekly" && !data.weekdays.length) throw new Error("Для еженедельного расписания выберите дни недели");
      if (data.end_at && data.end_at <= data.schedule_at) throw new Error("Окончание должно быть позже начала");
      if (form.dataset.mode === "edit") {
        const patch = {
          name: data.name,
          secondary_template_id: data.secondary_template_id,
          secondary_template_weight: data.secondary_template_weight,
          schedule_type: data.schedule_type,
          schedule_at: data.schedule_at,
          timezone_name: data.timezone_name,
          weekdays: data.weekdays,
          spacing_seconds: data.spacing_seconds,
          rollout_mode: data.rollout_mode,
          rollout_batch_size: data.rollout_batch_size,
          rollout_pause_seconds: data.rollout_pause_seconds,
          rollout_require_checkpoint: data.rollout_require_checkpoint,
          rollout_failure_threshold_percent: data.rollout_failure_threshold_percent,
          duplicate_guard_minutes: data.duplicate_guard_minutes,
          end_at: data.end_at,
          notes: data.notes,
        };
        await api(`/campaigns/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(patch) });
        await api(`/campaigns/${form.dataset.id}/destinations`, { method: "PUT", body: JSON.stringify({ destination_ids: data.destination_ids }) });
        closeModal(); toast("Кампания обновлена", "После изменений требуется новое утверждение"); return navigate("campaigns");
      }
      await api("/campaigns", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast(form.dataset.mode === "clone" ? "Копия кампании создана" : "Черновик кампании создан"); return navigate("campaigns");
    }
    if (form.id === "conversation-settings-form") {
      const data = formDataObject(form);
      data.ai_enabled = form.elements.ai_enabled.checked;
      if (!data.vacancy_key) data.vacancy_key = null;
      await api(`/conversations/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
      closeModal(); toast("Диалог обновлён"); return navigate("conversations");
    }
    if (form.id === "conversation-reply-form") {
      const data = formDataObject(form);
      await api(`/conversations/${form.dataset.id}/reply`, { method: "POST", body: JSON.stringify(data) });
      toast("Ответ отправлен"); return openConversationModal(form.dataset.id);
    }
    if (form.id === "candidate-form") {
      const data = formDataObject(form);
      data.age = numberOrNull(data.age);
      data.consent_to_storage = form.elements.consent_to_storage.checked;
      if (!data.phone) delete data.phone;
      if (!data.email) delete data.email;
      for (const key of ["full_name", "city", "experience", "schedule", "vacancy_key", "summary"]) if (data[key] === "") data[key] = null;
      await api(`/candidates/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
      closeModal(); toast("Карточка кандидата сохранена"); return navigate("candidates");
    }
    if (form.id === "policy-form") {
      const data = formDataObject(form);
      data.enabled = form.elements.enabled.checked;
      data.require_consent_before_ai = form.elements.require_consent_before_ai.checked;
      data.max_auto_replies_per_day = Number(data.max_auto_replies_per_day);
      data.handoff_keywords = textToList(data.handoff_keywords);
      data.stop_words = textToList(data.stop_words);
      data.allowed_chat_types = ["private"];
      data.vacancy_detection_rules = {};
      try { data.active_hours = JSON.parse(data.active_hours || "{}"); } catch { throw new Error("Активные часы должны быть корректным JSON"); }
      if (!data.ai_provider_config_id) data.ai_provider_config_id = null;
      if (!data.automation_flow_id) data.automation_flow_id = null;
      if (form.dataset.id) {
        delete data.telegram_connection_id;
        await api(`/automation/policies/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
      } else {
        await api("/automation/policies", { method: "POST", body: JSON.stringify(data) });
      }
      closeModal(); toast("Политика сохранена"); return navigate("automation");
    }
    if (form.id === "provider-form") {
      const data = formDataObject(form);
      data.enabled = form.elements.enabled.checked;
      data.timeout_seconds = Number(data.timeout_seconds);
      data.max_output_tokens = Number(data.max_output_tokens);
      data.temperature = Number(data.temperature);
      data.allowed_models = [];
      if (!data.base_url) delete data.base_url;
      if (!data.model_name) delete data.model_name;
      if (!data.api_key) delete data.api_key;
      if (form.dataset.id) {
        delete data.kind;
        await api(`/automation/providers/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
      } else {
        if (data.kind === "rule_based") { delete data.base_url; delete data.model_name; delete data.api_key; }
        await api("/automation/providers", { method: "POST", body: JSON.stringify(data) });
      }
      closeModal(); toast("AI-провайдер сохранён"); return navigate("automation");
    }
    if (form.id === "knowledge-form") {
      const data = formDataObject(form);
      data.tags = textToList(data.tags);
      data.is_active = form.elements.is_active.checked;
      if (!data.vacancy_key) data.vacancy_key = null;
      if (form.dataset.id) await api(`/automation/knowledge/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) });
      else await api("/automation/knowledge", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Статья сохранена"); return navigate("automation");
    }
    if (form.id === "integration-form") {
      const data = formDataObject(form);
      const payload = { name: data.name, event_types: textToList(data.event_types), is_active: form.elements.is_active.checked };
      if (form.dataset.id) {
        await api(`/integrations/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      } else {
        payload.kind = data.kind;
        if (data.kind === "webhook") payload.config = { url: data.webhook_url };
        else if (data.kind === "google_sheets") {
          let account;
          try { account = JSON.parse(data.service_account_json || "{}"); } catch { throw new Error("Service account должен быть корректным JSON"); }
          payload.config = { spreadsheet_id: data.spreadsheet_id, service_account: account, range: "Candidates!A:Z" };
        } else payload.config = { relative_path: data.relative_path || "integrations/events.csv" };
        await api("/integrations", { method: "POST", body: JSON.stringify(payload) });
      }
      closeModal(); toast("Интеграция сохранена"); return navigate("integrations");
    }
    if (form.id === "privacy-form") {
      const data = formDataObject(form);
      await api("/privacy/requests", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Privacy-запрос создан"); return navigate("privacy");
    }
    if (form.id === "api-key-form") {
      const data = formDataObject(form);
      data.scopes = [...form.querySelectorAll('input[name="scopes"]:checked')].map((element) => element.value);
      if (data.expires_at) data.expires_at = new Date(data.expires_at).toISOString(); else delete data.expires_at;
      const result = await api("/api-keys", { method: "POST", body: JSON.stringify(data) });
      return openModal("API-ключ создан", `<div class="alert alert-warning mb-14"><strong>Скопируйте ключ сейчас.</strong> После закрытия он больше не будет показан.</div><div class="code-box secret-box">${escapeHtml(result.secret)}</div><div class="form-actions"><button class="btn btn-primary" data-action="api-key-created-close">Я сохранил ключ</button></div>`);
    }
    if (form.id === "delivery-review-form") {
      const data = formDataObject(form);
      if (data.resolution === "confirmed_sent") {
        if (!data.telegram_message_id) throw new Error("Укажите Telegram message ID найденного сообщения");
      } else {
        delete data.telegram_message_id;
      }
      await api(`/jobs/${form.dataset.id}/resolve`, { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Результат сверки сохранён", "Автоматический повтор не выполнялся без вашего решения"); return navigate("jobs");
    }
    if (form.id === "run-checkpoint-form") {
      const data = formDataObject(form);
      const run = await api(`/campaigns/${form.dataset.campaignId}/runs/${form.dataset.runId}/continue`, { method: "POST", body: JSON.stringify({ note: data.note }) });
      closeModal(); toast("Следующий пакет разрешён", `Активный пакет ${run.active_batch} из ${run.total_batches}`); return navigate("campaigns");
    }
    if (form.id === "run-abort-form") {
      const data = formDataObject(form);
      await api(`/campaigns/${form.dataset.campaignId}/runs/${form.dataset.runId}/abort`, { method: "POST", body: JSON.stringify({ reason: data.reason }) });
      closeModal(); toast("Запуск остановлен", "Удержанные и ожидающие задания отменены", "warning"); return navigate("campaigns");
    }
    if (form.id === "campaign-approval-submit-form") {
      const data = formDataObject(form);
      const result = await api(`/campaigns/${form.dataset.id}/submit-approval`, { method: "POST", body: JSON.stringify({ note: data.note || null }) });
      closeModal(); toast("Кампания отправлена на утверждение", `Требуется решений: ${result.required_approvals}`); await refreshChromeState(); return navigate("campaigns");
    }
    if (form.id === "campaign-approval-reject-form") {
      const data = formDataObject(form);
      await api(`/campaigns/approval-requests/${form.dataset.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "reject", note: data.note }) });
      closeModal(); toast("Кампания отклонена", "Она возвращена в черновик", "warning"); await refreshChromeState(); return navigate("campaigns");
    }
    if (form.id === "publishing-pause-form") {
      const data = formDataObject(form);
      state.data.organization = await api("/organization/publishing/pause", { method: "POST", body: JSON.stringify({ reason: data.reason }) });
      closeModal(); await refreshChromeState(); renderShell(); toast("Публикации остановлены", data.reason, "warning"); return navigate(state.route);
    }
    if (form.id === "publishing-resume-form") {
      const data = formDataObject(form);
      state.data.organization = await api("/organization/publishing/resume", { method: "POST", body: JSON.stringify({ note: data.note || null }) });
      closeModal(); await refreshChromeState(); renderShell(); toast("Публикации возобновлены"); return navigate(state.route);
    }
    if (form.id === "approval-policy-form") {
      const data = formDataObject(form);
      data.require_distinct_campaign_approver = form.elements.require_distinct_campaign_approver.checked;
      data.high_risk_destination_threshold = Number(data.high_risk_destination_threshold);
      data.high_risk_required_approvals = Number(data.high_risk_required_approvals);
      data.approval_request_ttl_hours = Number(data.approval_request_ttl_hours);
      state.data.organization = await api("/organization", { method: "PATCH", body: JSON.stringify(data) });
      toast("Политика утверждений сохранена"); renderShell(); return navigate("organization");
    }
    if (form.id === "organization-form") {
      const data = formDataObject(form);
      data.retention_days = Number(data.retention_days);
      data.ai_enabled = form.elements.ai_enabled.checked;
      state.data.organization = await api("/organization", { method: "PATCH", body: JSON.stringify(data) });
      toast("Настройки организации сохранены"); renderShell(); return navigate("organization");
    }
    if (form.id === "password-form") {
      const data = formDataObject(form); const result = await api("/auth/change-password", { method: "POST", body: JSON.stringify(data) }); form.reset(); state.user.must_change_password = false; toast("Пароль изменён", result.message); return;
    }
    if (form.id === "totp-confirm-form") {
      const data = formDataObject(form); const result = await api("/auth/totp/confirm", { method: "POST", body: JSON.stringify(data) }); state.user.totp_enabled = true; closeModal(); toast("2FA включена", result.message); return navigate("security");
    }
    if (form.id === "totp-disable-form") {
      const data = formDataObject(form); const result = await api("/auth/totp/disable", { method: "POST", body: JSON.stringify(data) }); state.user.totp_enabled = false; closeModal(); toast("2FA отключена", result.message, "warning"); return navigate("security");
    }
    if (form.id === "user-form") {
      const data = formDataObject(form);
      if (form.dataset.id) { data.is_active = form.elements.is_active.checked; data.must_change_password = form.elements.must_change_password.checked; await api(`/users/${form.dataset.id}`, { method: "PATCH", body: JSON.stringify(data) }); }
      else await api("/users", { method: "POST", body: JSON.stringify(data) });
      closeModal(); toast("Пользователь сохранён"); return navigate("users");
    }
  } catch (error) {
    toast("Форма не сохранена", error.message, "error");
  } finally {
    if (submit) submit.disabled = false;
  }
}

document.addEventListener("dragstart", (event) => {
  const handle = event.target.closest?.("[data-flow-drag-node]");
  if (!handle || state.flowDraft?.readOnly) return;
  const step = handle.closest(".flow-step");
  if (!step) return;
  captureFlowDraftFromDom();
  state.flowDragNodeId = step.dataset.nodeId;
  step.classList.add("is-dragging");
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", state.flowDragNodeId);
  }
});

document.addEventListener("dragover", (event) => {
  if (!state.flowDragNodeId) return;
  const editor = event.target.closest?.("#flow-editor");
  const target = event.target.closest?.(".flow-step");
  if (!editor || !target) return;
  const dragged = [...editor.querySelectorAll(".flow-step")].find((element) => element.dataset.nodeId === state.flowDragNodeId);
  if (!dragged || target === dragged) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  const bounds = target.getBoundingClientRect();
  const placeAfter = event.clientY > bounds.top + bounds.height / 2;
  editor.querySelectorAll(".flow-step").forEach((element) => element.classList.remove("drag-over-before", "drag-over-after"));
  target.classList.add(placeAfter ? "drag-over-after" : "drag-over-before");
  editor.insertBefore(dragged, placeAfter ? target.nextSibling : target);
});

document.addEventListener("drop", (event) => {
  if (!state.flowDragNodeId) return;
  event.preventDefault();
  finishFlowDrag();
});

document.addEventListener("dragend", () => finishFlowDrag());

document.addEventListener("click", (event) => {
  const route = event.target.closest("[data-route]");
  if (route) {
    event.preventDefault();
    location.hash = `#/${route.dataset.route}`;
    navigate(route.dataset.route);
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "close-modal" || (action === "modal-backdrop" && event.target === target)) return closeModal();
  if (action === "mobile-menu") return document.getElementById("sidebar")?.classList.toggle("open");
  perform(action, target.dataset.id || "");
});

document.addEventListener("submit", (event) => {
  if (event.target.id === "login-form") return;
  event.preventDefault();
  submitForm(event.target);
});

document.addEventListener("change", (event) => {
  if (event.target.id === "discovery-select-all") {
    document.querySelectorAll('input[name="discovery_item"]:not(:disabled)').forEach((element) => { element.checked = event.target.checked; });
    return;
  }
  if (event.target.matches("[data-flow-node-type]")) {
    captureFlowDraftFromDom();
    const index = Number(event.target.dataset.index);
    const node = state.flowDraft?.nodes[index];
    if (node) {
      node.type = event.target.value;
      if (node.type === "choice" && !node.optionsText) node.optionsText = "Полный день|полный\nСменный|смены";
      if (node.type === "end") node.completion_mode ||= "handoff";
      renderFlowEditor();
    }
    return;
  }
  if (event.target.id === "campaign-secondary-template") {
    const weight = document.getElementById("campaign-secondary-weight");
    if (weight) weight.disabled = !event.target.value;
  }
  if (event.target.id === "campaign-connection") {
    document.getElementById("campaign-destinations").innerHTML = destinationChecks(event.target.value);
  }
  if (event.target.id === "campaign-schedule-type") {
    document.getElementById("weekdays-group").classList.toggle("hidden", event.target.value !== "weekly");
  }
  if (event.target.id === "campaign-rollout-mode") {
    document.getElementById("campaign-rollout-settings")?.classList.toggle("hidden", event.target.value !== "staged");
  }
  if (event.target.id === "delivery-review-resolution") {
    const group = document.getElementById("delivery-review-message-id-group");
    const input = group?.querySelector('input[name="telegram_message_id"]');
    const needsId = event.target.value === "confirmed_sent";
    group?.classList.toggle("hidden", !needsId);
    if (input) { input.required = needsId; if (!needsId) input.value = ""; }
  }
  if (["destination-permission-status", "destination-permission-confirmed"].includes(event.target.id)) {
    const expiry = document.getElementById("destination-permission-expiry");
    const confirmed = event.target.id === "destination-permission-status" ? event.target.value === "confirmed" : event.target.checked;
    if (expiry) { expiry.disabled = !confirmed; if (!confirmed) expiry.value = ""; }
  }
  if (event.target.id === "provider-kind") toggleProviderFields(event.target.value);
  if (event.target.id === "integration-kind") toggleIntegrationFields(event.target.value);
});

window.addEventListener("hashchange", () => {
  if (!state.user) return;
  const route = location.hash.replace(/^#\//, "") || "dashboard";
  navigate(route);
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && modalRoot.innerHTML) closeModal();
});

/**
 * Выполнить boot, явно сохраняя побочные эффекты UI или worker.
 */
async function boot() {
  registerServiceWorker();
  try {
    state.user = await api("/auth/me", {}, false);
  } catch {
    const refreshed = await refreshSession();
    if (!refreshed) {
      renderLogin();
      return;
    }
  }
  await refreshChromeState();
  const route = location.hash.replace(/^#\//, "") || "dashboard";
  state.route = routeMeta[route] ? route : "dashboard";
  renderShell();
  await navigate(state.route);
}

/**
 * Выполнить registerserviceworker, явно сохраняя побочные эффекты UI или worker.
 */
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  const isSecure = location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname);
  if (!isSecure) return;
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
    // PWA is an optional shell enhancement. Authentication and API operation do not depend on it.
  });
}

boot();
