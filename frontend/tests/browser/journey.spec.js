import { test, expect } from "@playwright/test";

const detail = "/#/dialogues/synthetic-browser-001";
test("saved result → all criteria → evidence → appeal/change persists without model calls", async ({ page }) => {
  const errors = [];
  const launches = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {
    if (request.method() === "POST" && /\/evaluations$/.test(request.url())) launches.push(request.url());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Контроль качества диалогов" })).toBeVisible();
  await page.getByRole("link", { name: "Открыть диалоги", exact: true }).click();
  await page.getByLabel("Поиск по ID").fill("synthetic-browser");
  await page.getByLabel("Статус оценки").selectOption("completed");
  await page.locator(".row-link").click();
  await expect(page.locator(".criterion-result")).toHaveCount(8);
  await expect(page.locator(".criterion-result").filter({ hasText: "N/A" })).toHaveCount(1);
  await expect(page.locator(".total-score")).toContainText("100 / 100");
  const transcript = await page.locator(".message").count();
  expect(transcript).toBeGreaterThan(0);
  await page.locator(".evidence-links button").first().click();
  await expect(page.locator(".message.highlighted")).toHaveCount(1);
  await expect(page.locator(".message.highlighted .message-id")).toHaveText("m002");
  await expect(page.getByRole("button", { name: "Оценить", exact: true })).toBeDisabled();
  await page.screenshot({ path: "test-results/saved-result.png", fullPage: true });
  await page.locator(".criterion-result").first().getByRole("button", { name: "Подать апелляцию" }).click();
  await page.getByLabel("Комментарий", { exact: true }).fill("Синтетическая браузерная проверка изменения оценки.");
  await page.getByRole("button", { name: "Отправить апелляцию" }).click();
  await page.getByRole("button", { name: "Рассмотреть", exact: true }).click();
  await page.getByLabel("Решение проверяющего").selectOption("change");
  await page.getByLabel("Причина решения", { exact: true }).fill("Тестовый пересмотр без вызова модели.");
  await page.getByLabel("Ответ", { exact: true }).selectOption({ label: "Нет" });
  await page.getByLabel("Объяснение итоговой оценки").fill("Синтетическое объяснение изменения.");
  await page.getByRole("button", { name: "Сохранить решение" }).click();
  await expect(page.getByText("Оценка изменена · Нет", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Оценка изменена · Нет", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Открыть диалог", exact: true }).click();
  await expect(page.locator(".original-result")).toHaveCount(1);
  await page.getByRole("link", { name: "Аналитика", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Аналитика качества" })).toBeVisible();
  await expect(page.locator(".stat-card")).toHaveCount(4);
  expect(launches).toEqual([]);
  expect(errors).toEqual([]);
});

test("a failed evaluation GET preserves transcript and retry only reads data", async ({ page }) => {
  await page.route("**/api/evaluations/*", route => route.fulfill({ status: 503, json: { error: { message: "Тестовая ошибка API" } } }));
  await page.goto(detail);
  await expect(page.getByRole("heading", { name: "Транскрипт", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Не удалось загрузить оценку" })).toBeVisible();
  await page.unroute("**/api/evaluations/*");
  await page.getByRole("button", { name: "Загрузить оценку" }).click();
  await expect(page.locator(".criterion-result")).toHaveCount(8);
});

test("checklist failure does not hide saved evaluation", async ({ page }) => {
  await page.route("**/api/checklists", route => route.fulfill({ status: 503, json: { error: { message: "Тестовая ошибка чек-листов" } } }));
  await page.goto(detail);
  await expect(page.locator(".criterion-result")).toHaveCount(8);
  await expect(page.getByRole("button", { name: "Оценить", exact: true })).toBeDisabled();
  await expect(page.getByText("Тестовая ошибка чек-листов", { exact: false })).toBeVisible();
});

test("empty filters, network error, processing and ML failure are explicit", async ({ page }) => {
  await page.goto("/#/dialogues");
  await page.getByLabel("Поиск по ID").fill("nonexistent-id");
  await expect(page.getByRole("heading", { name: "Диалоги не найдены" })).toBeVisible();
  await page.route("**/api/conversations?*", route => route.abort());
  await page.reload();
  await expect(page.getByRole("heading", { name: "Не удалось загрузить данные" })).toBeVisible();
  await page.unroute("**/api/conversations?*");
  for (const status of ["running", "failed"]) {
    await page.route("**/api/evaluations/*", async route => {
      const response = await route.fetch();
      const data = await response.json();
      await route.fulfill({ json: { ...data, status, final: null, error: { message: "Тестовая ошибка вычисления ML" } } });
    });
    await page.goto(detail);
    await expect(page.getByRole("heading", { name: status === "running" ? "Оценивается" : "Оценка завершилась ошибкой", exact: true })).toBeVisible();
    await expect(page.locator(".message").first()).toBeVisible();
    await page.unroute("**/api/evaluations/*");
    await page.goto("/#/dashboard");
  }
});

test("mobile navigation, results and evidence fit viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(detail);
  await expect(page.locator(".criterion-result")).toHaveCount(8);
  await page.locator(".evidence-links button").first().click();
  await expect(page.locator(".message.highlighted")).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
});

test("gross violations stay separate from a nonzero score and retain evidence", async ({ page }) => {
  await page.goto("/#/dialogues/synthetic-critical-002");
  await expect(page.locator(".total-score")).toHaveText("100 / 100");
  await expect(page.locator(".assessment-summary").getByText("Выявлена", { exact: true })).toBeVisible();
  const critical = page.locator(".criterion-result").last();
  await expect(critical.locator(".violation-list li")).toHaveCount(1);
  await critical.locator(".violation-list button").click();
  await expect(page.locator(".message.highlighted .message-id")).toHaveText("m002");
});
