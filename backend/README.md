# Backend DOOMSDAY

FastAPI + SQLite, один процесс и один фоновый worker. Все маршруты соответствуют
`codex_context/team/HTTP_API_CONTRACT.md`. ML-код и frontend не изменяются.
Фактический статус интеграции и внешние блокеры: [STATUS.md](STATUS.md).

## Windows PowerShell

Из корня репозитория, Python 3.11+:

```powershell
py -3 -m venv backend/.venv
& .\backend\.venv\Scripts\python.exe -m pip install -r requirements-backend.txt -e .
& .\backend\.venv\Scripts\python.exe -m backend --host 127.0.0.1 --port 8000
```

API: `http://127.0.0.1:8000/api/health`, список:
`http://127.0.0.1:8000/api/conversations`, OpenAPI: `/docs`.
Активация venv не нужна. Если `py` отсутствует, замените `py -3` полным путём
к установленному Python. Не используйте системный Python без модуля venv.

При запуске `codex_context/data/dev.json` импортируется атомарно. Повторный запуск
пропускает неизменившиеся записи. Если файла нет, сервер запускается с пустым
списком: получите DEV у команды и сохраните по этому пути либо импортируйте JSON:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/conversations/import `
  -ContentType 'application/json; charset=utf-8' `
  -InFile codex_context/data/dev.json
```

Поддерживаются оболочка организаторов `isSuccess=true/data.records`, массив
или одна запись. Пакет целиком проверяется до записи; конфликт ID отменяет всю
транзакцию. Одинаковые ID внутри пакета сворачиваются; imported/skipped считаются
по уникальным принятым ID. Если локальный DEV изменён под существующим ID,
автоимпорт явно завершит запуск ошибкой. Запустите с `--no-dev` для работы с уже
сохранённой базой и разберите конфликт через HTTP-импорт.

База: `backend/data/doomsday.sqlite3` (игнорируется Git). Настройка:
`--db <путь>` или переменная `DOOMSDAY_DB_PATH`. Сохраняются разговоры, версии,
задачи, исходные/финальные оценки, снимки и журнал апелляций. Для другого файла
DEV: `--dev <путь>`. Число разговоров не ограничено 65.

## Linux / текущая среда

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r requirements-backend.txt -e .
backend/.venv/bin/python -m backend --host 127.0.0.1 --port 8000
```

В текущей машине системный Python не содержит ensurepip. Окружение создано так:

```bash
/home/dakonya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m venv backend/.venv
```

## Модель и общий расчёт

`backend/ml_adapter.py` вызывает публичные `cqa_eval.evaluate_dialogue`,
`validate_response`, `calculate_score`. Scorer общий после модели и после
апелляции; своей формулы в backend нет. Decimal преобразуются в JSON numbers;
метаданные переименовываются в HTTP-формат. Usage суммируется по всем попыткам,
неизвестные стоимость/токены остаются null. Кэш не реализован.

Проверка конфигурации редактора автономна: импорт, чтение и изменение чек-листа
работают даже при отсутствии ML-пакета. Если он установлен, конфигурация
дополнительно проверяется его общим валидатором. Расчёт результатов требует ML.

Backend читает `.env` из корня репозитория до создания ML-адаптера. Сам
`cqa_eval` по-прежнему не читает файлы окружения. Файл необязателен: без него
работают импорт, чтение и редактор. Для подготовки файла в PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item backend/env.example .env }
```

Заполните `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_STYLE` локально
по настройкам, которые передал ML-участник; описание — в `CQA_README.md`.
Не вставляйте ключи в frontend. Переменные процесса имеют приоритет над `.env`,
включая явно пустые значения; переменные `$...`/`${...}` в файле не раскрываются.
Неизвестные цены оставляйте пустыми. После изменения настроек перезапустите сервер.

Другой путь или отключение чтения в PowerShell:

```powershell
& .\backend\.venv\Scripts\python.exe -m backend --env-file backend/.env
& .\backend\.venv\Scripts\python.exe -m backend --no-env-file
```

В Linux используются те же флаги после `backend/.venv/bin/python -m backend`.
Поиск `.env` в родительских каталогах не выполняется. Отсутствующий default
допустим; явно выбранный несуществующий/некорректный файл даёт безопасную ошибку
запуска до загрузки значений. Для приложения-фабрики:
`create_app(env_file=path)` либо `create_app(load_env_file=False)`.

Health проверяет только наличие/валидность настроек, без сети. Статус `ready`
не доказывает, что модель доступна у провайдера. Без настроек импорт, чтение,
редактор чек-листов и пересчёт существующих апелляций доступны; запуск оценки
возвращает 503 без создания фиктивного результата. Тестовый оценщик доступен
только через явное внедрение зависимости в тестах, CLI его не включает.

POST оценки отвечает 202/queued сразу; GET задания показывает
queued/running/completed/needs_review/failed. Ошибки модели сохраняются в задаче.
Незавершённые задания после перезапуска становятся failed/SERVER_RESTARTED без
автоматического повторного платного вызова. При штатной остановке сервер ждёт
текущий вызов и отменяет ещё не запущенные локальные задания. Запускайте один
процесс, без `--workers` и hot reload: очередь и восстановление рассчитаны на
один локальный backend.

Оригинал оценки и её checklist_snapshot неизменяемы. Изменение конфигурации
создаёт версию с optimistic concurrency по base_version. Апелляции проверяют
expected_revision, допустимость полной replacement, доказательства и флаги;
пересчёт и решение фиксируются одной транзакцией. Флаг не обнуляет балл.
Другие роли можно импортировать без потери, однако текущий ML принимает только
client/operator/bot/system и отклоняет оценку иных ролей.

## Frontend

Совместная интеграция выполняется на **одном компьютере**. Frontend уже
опубликован в `main` через PR #4: HTML/CSS/JavaScript в `frontend/public/`,
отдельная сборка не нужна. Ветка `feat/backend` сама по себе его не содержит;
для общего запуска нужны frontend из `main` и изменения backend из PR #3.
Backend не требует frontend для запуска API.

Для участника frontend порядок такой:

1. Установить backend командами выше. Получить у команды DEV и сохранить
   `codex_context/data/dev.json`, либо начать с `--no-dev` и загрузить новый JSON
   через интерфейс. DEV и SQLite не передаются через Git.
2. Использовать согласованную копию проекта с frontend из `main`. Проверить
   наличие `frontend/public/index.html`; команды сборки npm не требуются.
3. API-клиент frontend уже использует относительный `/api`. Запустить публичный
   каталог через backend и открыть `http://127.0.0.1:8000/`.

PowerShell:

```powershell
Test-Path frontend/public/index.html
& .\backend\.venv\Scripts\python.exe -m backend --host 127.0.0.1 --port 8000 --frontend frontend/public
```

Linux:

```bash
backend/.venv/bin/python -m backend --host 127.0.0.1 --port 8000 --frontend frontend/public
```

Путь можно заменить абсолютным путём к публичному каталогу на этом же компьютере. Явно
неверный `--frontend` останавливает запуск с понятной ошибкой. По умолчанию
backend раздаёт `frontend/public/`, если такой каталог существует. Укажите
правильный build-каталог явно, если `public/` содержит лишь исходные ассеты.

Из консоли браузера на том же origin можно проверить подключение:

```javascript
await fetch('/api/health').then(response => response.json())
await fetch('/api/conversations?limit=5').then(response => response.json())
```

Не открывайте HTML через `file://`. Если frontend-участник использует отдельный
dev-server, например на `127.0.0.1:5173`, он должен проксировать все `/api/...`
на `http://127.0.0.1:8000`, сохраняя путь. Запросы браузера при этом остаются
относительными к frontend origin. CORS в backend не включён. Опубликованный
frontend отдельного dev-server не требует и раздаётся с API с одного адреса.

Раздаётся только публичный каталог; не указывайте корень репозитория.
DEV, `codex_context`, `.env` и база не должны находиться внутри сборки.
Для SPA проверяйте маршрутизацию: root/index и hash-навигация обслуживаются,
автоматического fallback вложенных history-маршрутов на `index.html` сейчас нет.
Если frontend не подключён, `/` даёт 404, а API и `/docs` работают.

### Краткая карта API для подключения

Полные формы ответов описаны в [HTTP API v1](../codex_context/team/HTTP_API_CONTRACT.md).
Рядом опубликованы [синтетические HTTP-примеры](../codex_context/team/fixtures/http_examples.json).
Для запуска проверок доступен `/docs`: каждый POST имеет JSON-редактор,
импорт — явно синтетический пример. Swagger позволяет проверять API отдельно от UI.

| Действие | Запрос |
| --- | --- |
| Импорт JSON | `POST /api/conversations/import`, JSON выгрузки, массива или одной записи |
| Список и карточка | `GET /api/conversations`, `GET /api/conversations/{id}` |
| Текущая версия/история | `GET /api/checklists/current`, `GET /api/checklists`, `GET /api/checklists/{version}` |
| Новая версия | `POST /api/checklists`, `{base_version, checklist}` — полный изменённый объект со старой version |
| Запуск | `POST /api/conversations/{id}/evaluations`, `{checklist_version}` |
| Результат/статус | `GET /api/evaluations/{evaluation_id}` |
| Подать апелляцию | `POST /api/evaluations/{evaluation_id}/appeals`, `{criterion_id, comment, expected_revision}` |
| Очередь апелляций | `GET /api/appeals?status=open` или `resolved`/`all` |
| Рассмотреть | `POST /api/appeals/{appeal_id}/resolve`, `{decision, reason, replacement, expected_revision}` |

Все ID кодировать через `encodeURIComponent`. Для нового запуска сначала читать
current.version, а не зашивать `"1"`. После 202 опрашивать GET задачи примерно
раз в секунду, пока queued/running не сменится на completed/needs_review/failed;
GET не запускает повторный вызов. Ошибка — `{error:{code,message,details}}`.
503 показывается как недоступность оценщика, 409 требует перечитать актуальную
версию/ревизию формы. Автоматическое переключение на fixtures при ошибке API
не допускается. `synthetic=true` всегда показывается в интерфейсе.

Основания берутся из `final.assessment.criteria`: ссылки `evidence_message_ids`,
`checked_range`, `violations`, причины N/A/uncertainty. Они сопоставляются с
`messages[].message_id` карточки. Сырые ответы и взвешенные баллы отображать
отдельно; итог backend повторно не вычислять. При null показывать неизвестность.
Таблица исторической оценки строится из её `checklist_snapshot`, не current.
Смещения аудио не вычислять из timestamps; при null использовать обычный плеер.

`confirm` требует `replacement:null`; `change` — полную исправленную строку
критерия из `final.assessment.criteria` с тем же criterion_id, основаниями,
доказательствами, N/A/uncertainty и violations. После решения перечитать оценку:
revision увеличивается, original сохраняется, final пересчитывается сервером.
После сохранения новой конфигурации старые оценки не меняются; нужна новая
оценка. Каждый такой запуск обращается к модели, когда провайдер подключён.

### Приёмка совместной демонстрации

В совместной копии проекта выполнить в интерфейсе: импорт нового разговора;
запуск и открытие результата; переход к сообщениям-доказательствам; подачу и
рассмотрение апелляции; изменение/добавление критерия; новую оценку на новой
версии. Остановить и снова запустить backend с тем же `--db`, затем проверить
оба результата, original/final, версии и решения. До подключения провайдера
проверять UI импорта/чтения/редактора и честное отображение 503. Один реальный
smoke через backend выполняется только после настройки ML-участником; остальные
проверки API используют явную тестовую зависимость.

## Офлайн-проверки

```powershell
& .\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
& .\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

На Linux используйте `backend/.venv/bin/python` с теми же аргументами.
Backend-тесты используют временные базы и явно синтетическую зависимость,
сохраняя настоящий ML-валидатор/scorer. Они не вызывают модель и не проверяют
качество её предсказаний. Полный существующий ML-suite требует локальный DEV.
Секреты, DEV, база, результаты и аудио не входят в Git. Реальный платный smoke
выполняет ML-участник отдельно по `CQA_README.md`.

На первом этапе 7 ноября 2026 прошли 37 backend-тестов (22 HTTP, 9 адаптера,
6 импортера) и 34 существующих ML-теста. В этой среде HTTP-тесты
потребовали запуска вне сетевой песочницы из-за внутреннего соединения TestClient.
Зависимости согласованы (`pip check`). Сервер запущен на 127.0.0.1:8000:
первый импорт сохранил 65 разговоров, перезапуск пропустил 65 без дубликатов;
health, список, карточка, чек-лист проверены реальными HTTP-запросами.
Без LLM-настроек POST оценки вернул 503; приватные пути вернули 404.
Реальных модельных вызовов выполнено 0; точность модели не измерялась.

На этапе подключения frontend после изменения backend повторно прошли все
**53 backend-теста**: 22 HTTP, 1 последовательный demo flow, 13 окружения,
2 Swagger/frontend handoff, 6 импорта и 9 адаптера. 34 ML-теста повторно не
запускались, потому что ML-код не менялся. Новый CLI перезапущен; реальный HTTP
подтвердил `/docs`, пять POST JSON-редакторов, список 65 DEV, чек-лист и health.
Точные актуальные блокеры и следующий шаг перечислены в `STATUS.md`.

Новый сквозной API-сценарий (без модели) можно повторить отдельно:

```powershell
& .\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_demo_flow -v
```

Он использует временную SQLite и явно синтетический оценщик; проверяет две
оценки на разных версиях, evidence, решения апелляций и сохранность после
перезапуска. Это API-проверка, совместный browser demo ещё не подтверждён.
