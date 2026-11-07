# HTTP API для frontend и backend — версия 1

Это HTTP-контракт версии 1 существующего приложения DOOMSDAY. Маршруты реализованы в `backend/app.py`; интерактивное описание доступно по `/docs` запущенного сервера. Документ описывает внешнюю границу приложения, а не формат организаторов. Изменения контракта согласуйте с командой и проверяйте вместе с действующей интеграцией.

`../CONTRACT.md` описывает Python-оценщик и строгий ответ LLM. Этот документ описывает HTTP-обёртку приложения. Backend преобразует фактический результат Python-модуля в эту обёртку через один адаптер. Frontend не зависит от внутренних имён Python-функций и классов. `fixtures/http_examples.json` содержит только выдуманные примеры формата.

## Общие правила

- Базовый путь `/api`, JSON UTF-8, имена полей в `snake_case`. ID и версии — строки; даты событий приложения — UTC ISO 8601. Исходные timestamps реплик сохраняются без преобразования.
- Если проект пустой: один Python backend, SQLite и каталог `frontend/` с HTML/CSS/JS. Backend отдаёт UI и API с одного адреса. Существующий стек сохраняйте. Авторизация и отдельный ML-микросервис для MVP не нужны.
- Данные разговора хранятся отдельно от оценок. Одна оценка закреплена за снимком чек-листа. Каждая новая оценка получает новый `evaluation_id`.
- `null` не равен нулю, отрицательному ответу или N/A. Грубое нарушение — отдельный трёхзначный флаг `true/false/null`.
- Конфигурация и критерии динамические: исходные восемь — только старт. Frontend строит таблицу из сохранённого снимка чек-листа конкретной оценки.
- `synthetic: true` означает выдуманный материал для разработки. Такая метка всегда видна в UI; она не означает выполненный вызов модели. Production не подменяет ошибку или новый диалог заранее подготовленным ответом.

## Маршруты

| Метод и путь | Запрос | Ответ |
|---|---|---|
| `GET /api/health` | — | `200 Health` |
| `POST /api/conversations/import` | Исходный JSON организаторов, массив записей либо одна запись | `200 ImportResult` |
| `GET /api/conversations` | Фильтры ниже | `200 ConversationPage` |
| `GET /api/conversations/{conversation_id}` | — | `200 ConversationDetail` |
| `GET /api/checklists/current` | — | `200 Checklist` |
| `GET /api/checklists` | — | `200 {items: [{checklist_id, version, title}], current_version}`; новые версии сначала |
| `GET /api/checklists/{version}` | — | `200 Checklist` |
| `POST /api/checklists` | `{base_version, checklist}` | `201 Checklist` новой версии |
| `POST /api/conversations/{conversation_id}/evaluations` | `{checklist_version}` | `202 Evaluation` со статусом `queued` |
| `GET /api/evaluations/{evaluation_id}` | — | `200 Evaluation` |
| `GET /api/appeals` | `status=open|resolved|all`, по умолчанию `open` | `200 {items: Appeal[], total: integer}` |
| `POST /api/evaluations/{evaluation_id}/appeals` | `{criterion_id, comment, expected_revision}` | `201 Appeal` |
| `POST /api/appeals/{appeal_id}/resolve` | `{decision, reason, replacement, expected_revision}` | `200 {appeal: Appeal, evaluation: Evaluation}` |

`current` — служебный маршрут, зарегистрируйте его раньше маршрута переменной версии. Кодируйте ID в URL. Не добавляйте отдельные несовместимые варианты этих маршрутов ради mock-режима.

### Ошибки

Единый формат: `{ "error": { "code": "EVALUATOR_UNAVAILABLE", "message": "Оценщик пока не подключён", "details": null } }`.

Коды HTTP: `400` нечитаемый JSON, `404` неизвестный объект, `409` конфликт версии/повторное несовместимое действие, `422` неверные данные, `503` недоступный оценщик. `details` — null либо массив безопасных описаний полей. Не возвращать ключи, полный prompt, подписанные URL из исключений или внутренний stack trace.

Ошибка модели после постановки задачи сохраняется в `Evaluation.error` и даёт `status=failed`; GET существующей задачи при этом возвращает 200. Отсутствие модели/конфигурации, известное до запуска, даёт 503 без фиктивной оценки.

## Health, импорт и диалоги

`Health`: `{status: "ok", api_version: "1", evaluator_status: "ready"|"unavailable", evaluator_message: string|null}`. Ready означает, что адаптер и необходимые настройки доступны; сетевой вызов модели при health-проверке не выполняется.

Импорт принимает JSON непосредственно в теле; multipart не нужен. Для выгрузки организаторов извлекается `data.records`, только если `isSuccess=true`. Проверяется вся загрузка до записи. Нельзя требовать ровно 65 записей: контрольный набор будет новым. Обязательные поля записи: `conversation_id`, `channel`, `language`, `transcript`; `audio_url` отсутствует либо строка/null. Необязательный `synthetic` имеет тип boolean и явно помечает выдуманные данные. Реплика: `timestamp`, `speaker`, `text`; необязательный `audio_offset_seconds` — неотрицательное конечное число либо null. Принимаются непустые строковые `channel/language`, без жёсткого списка каналов; UI показывает исходные значения. Пустой transcript отклоняется как неоцениваемый импорт.

Сохранить исходный текст, роли, порядок и повторы. Каноническое сравнение содержимого записи игнорирует порядок ключей JSON, но учитывает порядок сообщений. Уже существующий ID с тем же содержимым пропускается; тот же ID с изменённым разговором — 409, без частичного импорта. Повтор одинакового импорта безопасен.

`ImportResult`: `{imported: integer, skipped: integer, conversation_ids: string[]}`. IDs соответствуют принятым уникальным записям, включая пропущенные. Импорт ничего не оценивает и не вызывает LLM.

`ConversationSummary`:

```text
conversation_id, channel, language: string
message_count: integer
has_audio, synthetic, has_open_appeals: boolean
latest_evaluation: EvaluationSummary | null
```

`EvaluationSummary`: `evaluation_id`, `checklist_version`, `status`, `created_at`, `revision`, `total_score: number|null`, `critical_error: boolean|null`. Результат берётся из `final`, если он уже существует. «Последняя» определяется временем создания оценки; новая неудачная попытка не удаляет предыдущие результаты.

`GET /api/conversations`: параметры `language`, `channel`, `status` (один из статусов оценки или `not_evaluated`), `critical_error=true|false|unknown`, `limit` (по умолчанию 100, максимум 200), `offset` (по умолчанию 0). Отсутствующий параметр не фильтрует. Фильтры пересекаются и применяются до пагинации. `unknown` включает отсутствие результата или неизвестный флаг; `false` — только подтверждённый отрицательный флаг. Ответ `ConversationPage`: `{items: ConversationSummary[], total, limit, offset}`; total — число после фильтров. Стабильная сортировка по `conversation_id`.

`ConversationDetail`: `conversation_id`, `channel`, `language`, `audio_url: string|null`, `synthetic: boolean`, `messages: Message[]`, `latest_evaluation_id: string|null`, `evaluation_history: EvaluationSummary[]` (новые сначала).

`Message`: `{message_id, source_index, timestamp, speaker, text, audio_offset_seconds}`. `source_index` начинается с 0. `message_id` — `m001`, `m002` и далее по исходному индексу, включая bot/system; после 999 — `m1000`. `audio_offset_seconds` — неотрицательное число либо null. ISO-время, паузы записи и наличие audio_url не доказывают смещение аудио. Если надёжного соответствия нет, переходите к реплике и предлагайте обычный плеер/ссылку без выдуманного seek.

## Чек-листы

`Checklist` повторяет структуру `../checklist.json`: `schema_version`, `checklist_id`, `version`, `title`, `sections`, `criteria`. Имена и типы полей критерия берите из этого файла. ID уникальны; section_id существует; вес числового/binary критерия конечный и неотрицательный; flag имеет weight=null. Проверяйте согласованность `kind`, `allowed_answers`, `scale_max`, `levels`, `na_allowed`, `na_rule`, `flag_types`. Сумма весов не обязана равняться 100: общий scorer нормализует применимые веса. Отрицательные веса, пустые правила и несовместимые ступени отклоняются до LLM.

При сохранении frontend передаёт полный изменённый checklist с его старой version и `base_version`, равной прочитанной current.version. Backend проверяет совпадение base_version с текущей версией, присваивает следующую строковую версию и сохраняет новый снимок. Старый снимок не меняется. Конфликт — 409 с просьбой перечитать форму; не перезаписывать изменения другого участника.

Новая версия применяется к новым запускам оценивания. Сохранение чек-листа само не запускает LLM и не меняет исторические баллы. Для перерасчёта на новом критерии/правиле пользователь запускает новую оценку. Старый результат продолжает показывать старый checklist_snapshot.

## Evaluation: задача и результат

Поля объекта присутствуют и в queued-ответе, и после завершения:

```text
evaluation_id, conversation_id, checklist_version, created_at: string
status: queued | running | completed | needs_review | failed
revision: integer
synthetic: boolean
checklist_snapshot: Checklist
original: ResultState | null
final: ResultState | null
metadata: Metadata | null
error: {code, message, details} | null
appeals: Appeal[]
```

При постановке задачи снимок чек-листа уже закреплён; `revision=0`, original/final/metadata/error=null. Начальная рабочая реализация может выполнять одну задачу за раз в процессе backend. Храните состояние в SQLite; после перезапуска зависшие queued/running отмечайте failed с понятной причиной. Не делайте автоматический повтор платного запроса при каждом GET. Повтор POST для уже активной задачи того же разговора и версии возвращает 409; явная повторная оценка после завершения получает новый ID.

После успешной валидации `original` и `final` первоначально равны, `revision=1`. Статус completed означает отсутствие неразрешённых `needs_review`; наличие таких строк даёт needs_review. Все применимые веса могут отсутствовать: completed допустим с null score и явным no_applicable_criteria, это не ноль. API/JSON-ошибка даёт failed и original/final=null.

`ResultState`:

```text
assessment: строгий объект из ../schemas/llm_response.schema.json
calculation: Calculation
critical_error: true | false | null
```

`Calculation`:

```text
total_score: number | null
applicable_weight: number | null
no_applicable_criteria: boolean
rows: [{criterion_id, base_weight, effective_weight, earned_points}]
sections: [{section_id, earned_points, possible_points}]
```

Числа весов/баллов допускают null при неизвестности. `effective_weight` — вклад критерия в нормированные 100 баллов; `earned_points` — полученная часть этого вклада. Для разрешённого N/A эффективный вес и баллы 0; для flag оба null и base_weight=null. Секции — суммы глобальных вкладов, не независимые шкалы до 100. При неопределённом знаменателе не выдавать придуманные числовые вклады; использовать null. В UI отличайте сырой ответ шкалы, например 7/10, от взвешенного вклада в итог. Округляется окончательный total_score; frontend может форматировать отображение остальных чисел, но не пересчитывает итог.

`sections` содержит секции с весовыми критериями. Секция только с flag-критериями в расчёт не входит и может отсутствовать в этом массиве; сами flag-строки остаются в `rows` и `assessment.criteria`.

Все ответы, веса, N/A и грубый флаг проверяются/рассчитываются общим ML-модулем по `../CONTRACT.md`. Backend не реализует вторую формулу. Обёртка может переименовать поля, но не менять смысл результата. Если API scorer ещё не готов, фиксируйте это как интеграционную зависимость, а не пишите другой калькулятор.

Если flag-критериев несколько: хотя бы один подтверждённый yes даёт critical_error=true; иначе наличие неизвестного флага даёт null; при завершённых отрицательных ответах — false.

`Metadata`: `model: string|null`, `cache_hit: boolean`, `api_calls: integer`, `retry_count: integer`, `latency_ms: number|null`, `usage: {input_tokens, output_tokens}|null`, `estimated_cost_usd: number|null`, `cost_note: string|null`, `dialogue_hash: string|null`, `checklist_hash: string|null`, `prompt_hash: string|null`. Если usage частичный, соответствующее число может быть null. Стоимость охватывает все вызовы, включая исправление ответа. Неизвестная стоимость — null с пояснением, не 0. У синтетического примера api_calls=0; model=null. Метаданные получаются из реального адаптера, не угадываются по названию подписки Codex.

## Апелляции и итог

Апелляция возможна только по существующему ResultState и критерию его снимка. Непустой comment обязателен; expected_revision должен совпадать с Evaluation.revision. Не более одной открытой апелляции на критерий данной оценки, иначе 409.

`Appeal`: `appeal_id`, `evaluation_id`, `criterion_id`, `comment`, `created_at`, `status: open|resolved`, `based_on_revision: integer`, `before: CriterionResult`, `resolution: null|{decision, reason, replacement, resolved_at, resulting_revision}`. `CriterionResult` — полная строка `assessment.criteria` по схеме, включая доказательства, неопределённость и violations. `before` — неизменяемый снимок final-строки при подаче.

Решение `confirm` сохраняет текущую строку, replacement=null. Решение `change` требует полную replacement с тем же criterion_id; другие ID/контекст подменять нельзя. Backend повторно проверяет строку и полный новый assessment по снимку чек-листа и исходному разговору. Нужны допустимый ответ, основания, причина N/A или неопределённости, а для flag=yes — эпизод нарушения. reason решения обязателен в обоих случаях. expected_revision проверяется в одной транзакции с записью; конфликт — 409.

Backend использует общий scorer, пересчитывает final, обновляет общий статус и увеличивает revision. original и исходные metadata не меняются. Запись решения и новый final сохраняются атомарно; если scorer недоступен, возвращается 503 без частичной фиксации. Нельзя дать positive flag без оснований или обнулить за него общий балл. В UI показываются исходная оценка, текущее решение, итог и история апелляций. Режимы «оператор»/«проверяющий» могут быть простым переключателем интерфейса, без разработки авторизации для демо.

## Сценарии интеграционной проверки

1. Запустить backend с опубликованным `frontend/public/`: импорт, список и карточка должны работать через реальный API без настроек LLM.
2. Проверить видимую ошибку при недоступном API или оценщике. Опубликованный frontend работает с реальным API; отдельного fixture-адаптера в нём нет. JSON fixtures служит примерами формата и сам по себе не является сервером или режимом приложения.
3. После настройки провайдера выполнить один явно согласованный реальный запуск. Frontend опрашивает GET задачи и показывает результат либо ошибку. `evaluator_status=ready` в `/api/health` подтверждает настройки, но не успешный сетевой вызов.
4. Проверить добавление критерия, новую оценку и апелляцию с сохранением после обновления страницы и перезапуска сервера. Результаты тестов и реальные вызовы модели фиксировать отдельно; наличие кода и примеров не заменяет эту проверку.
