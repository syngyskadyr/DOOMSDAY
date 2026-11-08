// Configuration health alone never establishes that a model is reachable.
export const apiAvailable = (health) => health?.status === "ok" && health.api_version === "1";

export function modelPresentation(model) {
  if (model?.model_status === "ready" && model.generation_tested === false) {
    return { label: "Модель: доступна", tone: "success", ready: true };
  }
  if (model?.model_status === "unconfigured") {
    return { label: "Модель: не настроена", tone: "warning", ready: false };
  }
  if (model?.model_status === "unavailable") {
    return { label: "Модель: недоступна", tone: "danger", ready: false };
  }
  return { label: "Модель: готовность неизвестна", tone: "neutral", ready: false };
}

export function evaluationGate(apiHealth, model, { submitting = false, active = false } = {}) {
  if (submitting) return { disabled: true, reason: "Запуск оценки…" };
  if (active) return { disabled: true, reason: "Оценка по этой версии уже выполняется" };
  if (!apiAvailable(apiHealth)) return { disabled: true, reason: "Доступность API не подтверждена. Новое оценивание недоступно." };
  if (!modelPresentation(model).ready) {
    return { disabled: true, reason: model?.message || "Готовность модели неизвестна. Обновите статус перед оцениванием." };
  }
  return { disabled: false, reason: "Сервер модели доступен. Проверка статуса не запускала генерацию." };
}
