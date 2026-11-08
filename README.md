# DOOMSDAY

**Команда: squad**

Рабочее пространство контроля качества клиентских диалогов: оценка оператора,
доказательства в транскрипте, N/A, грубые нарушения, апелляции и версии правил.
Поддерживаются RU/KZ/EN и предоставленные текстовые расшифровки звонков.
Учитывается контекст client/bot/system; собственного ASR нет.

## Возможности и архитектура

- Реестр и загрузка диалогов, карточка, история оценок и переходы к evidence.
- Редактирование формулировок, весов, шкал, N/A и новых критериев через UI.
- Апелляции с сохранением оригинала и пересчётом общим scorer.
- SQLite и сохранение после перезапуска, отдельные статусы API и модели.
- Проверка JSON/контракта, ограниченные попытки, batch-журналы и экспорт JSON 1.0.

```text
HTML/CSS/JavaScript → FastAPI → SQLite
                         ↓
               cqa_eval → validator → Decimal scorer
                         ↓
           SSH localhost:8001 → Brev / vLLM / Qwen
```

Frontend статический, сборка не требуется. Исходные восемь критериев сохранены
в `codex_context/checklist.json`; демо-изменения создают отдельные версии.
Флаг нарушения не имеет веса и не обнуляет итог. Только разрешённый N/A
исключается из знаменателя; неизвестность и ошибки не становятся N/A или нулём.

Модель **Qwen/Qwen3-14B-AWQ**, revision
`31c69efc29464b6bb0aee1398b5a7b50a99340c3`, alias
`doomsday-qwen3-14b-awq`. Проверены L40S 48 GB, vLLM 0.8.5.post1,
torch 2.6.0+cu124, non-thinking, контекст 32768, предел генерации 4096.

## Статус технической подготовки

- DEV подготовлен в формате JSON 1.0: **65 ID, 64 `ok`, 1 `error`**.
  Три прежние ошибки получили по одной отдельной попытке: две валидны,
  `p1-ru-023` снова нарушил JSON-контракт и остался явной ошибкой.
  62 прежних успешных результата сохранены; это не метрика точности.
- Проверки: **114 ML/CLI, 64 backend, 18 frontend**; корпоративные браузерные
  сценарии/состояния — 18, дополнительно реальные model-up и model-down
  GET-only проверки — по 3, без генерации.
- Последний GPU удалён с API-подтверждением. Новые оценки **сейчас недоступны**;
  для PROD/демо требуется запуск модели и backend по процедуре ниже.
- Настоящий PROD пока не выдан. Контрольная сдача не завершена; фиктивного
  `results/prod/results.json` нет.

Происхождение обновления — [DEV_PROVENANCE.md](docs/DEV_PROVENANCE.md).

## Установка и настройки

Локально нужны Python 3.11+ (проверен 3.12.14), для frontend-тестов Node.js
22+ (проверен 24.22.1). Веса и GPU-зависимости локально не устанавливаются.
Из корня репозитория, Linux:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r requirements-backend.txt -r requirements-cqa-batch.txt
backend/.venv/bin/python -m pip install -e .
backend/.venv/bin/python -m pip check
```

После клонирования отдельно получить DEV и сохранить в
`codex_context/data/dev.json` (`isSuccess=true`, `data.records`). Частные данные
не входят в Git. Для первой настройки можно скопировать `backend/env.example`
в корневой `.env`; существующий `.env` не перезаписывать.

```dotenv
BREV_API_KEY=<ключ управления Brev, только локально>
LLM_BASE_URL=http://127.0.0.1:8001/v1
LLM_MODEL=doomsday-qwen3-14b-awq
LLM_API_KEY=<отдельный inference-ключ>
LLM_API_STYLE=chat_completions
LLM_STRUCTURED_OUTPUT=json_schema
LLM_TIMEOUT_SECONDS=300
LLM_MAX_ATTEMPTS=1
LLM_INPUT_USD_PER_MILLION=
LLM_OUTPUT_USD_PER_MILLION=
```

`NVIDIA_API_KEY` не требуется. `BREV_API_KEY` используется только для управления
инфраструктурой, inference-ключ генерируется отдельно. Backend и `scripts/cqa.py`
загружают `.env`; базовый `python -m cqa_eval` читает окружение процесса.
Несуществующей переменной `.env` для лимита генерации нет.

## Живая модель и приложение

Предварительно установить официальный Brev CLI; в проверенной среде v0.6.337
находится в `backend/.venv/bin/brev`. Сначала проверить цену и ресурсы.
Backend сам платный GPU не создаёт. Для новой сессии нужен новый каталог:

```bash
backend/.venv/bin/python scripts/brev/session.py quote
backend/.venv/bin/python scripts/brev/session.py list --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py create --run-dir artifacts/brev/demo --minutes 60 --budget-usd 3
backend/.venv/bin/python scripts/brev/session.py prepare --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py forward --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py configure-env --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py tokenizer --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py ready --run-dir artifacts/brev/demo
```

Если уже есть сервер, создание второго блокируется. `prepare` устанавливает
совместимые пакеты и веса **на Brev**. После старта процесса дождаться
`model_status=ready`; затем проверить реальную генерацию синтетикой:

```bash
backend/.venv/bin/python scripts/brev/smoke_inference.py --live --stage small --run-dir artifacts/brev/demo/small
backend/.venv/bin/python scripts/brev/smoke_inference.py --live --stage cqa --run-dir artifacts/brev/demo/cqa
backend/.venv/bin/python -m backend --host 127.0.0.1 --port 8000
```

Открыть `http://127.0.0.1:8000`. Backend импортирует локальный DEV при старте;
база по умолчанию `backend/data/doomsday.sqlite3`. Другую задаёт `--db PATH`,
отключение стартового импорта — `--no-dev`.

`GET /api/health` показывает backend и конфигурацию. Отдельный
`GET /api/model-health` ограниченными GET проверяет vLLM `/health` и `/v1/models`:
доступность engine и нужного alias, **без генерации**. Кэш 4 секунды, UI
обновляет статус раз в 15 секунд. Это не пробная генерация и не гарантия качества.
При неизвестной/недоступной модели новое оценивание блокируется. Без GPU
сохранённые оценки, evidence, апелляции и версии остаются доступны.

Перед завершением сохранить runtime и удалить только созданный ресурс:

```bash
backend/.venv/bin/python scripts/brev/session.py capture --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py delete --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py status --run-dir artifacts/brev/demo
```

CLI exit 0 не доказывает удаление: дождаться `environment_absent=true`.
Watchdog начинает завершение за 15 минут до выбранного лимита. Выход из SSH
не прекращает аренду. Детали — [BREV_SETUP.md](docs/BREV_SETUP.md).
Для демо запускать модель заранее; выключенный сервер не считается готовым.

## Оценка и экспорт результатов

Проверенный CLI с безопасной загрузкой `.env`:

```bash
backend/.venv/bin/python scripts/cqa.py inspect-dev --out artifacts/cqa/inspection.json
backend/.venv/bin/python scripts/cqa.py batch --help
backend/.venv/bin/python scripts/cqa.py export-results --help
```

Preflight использует скопированный с сервера tokenizer/template:
`--context-window 32768 --max-output-tokens 4096 --context-margin 512`
и `--chat-template-kwargs '{"enable_thinking":false}'`. Разговоры не обрезаются.
`batch --initialize` сохраняет manifest/pending без HTTP; тот же запуск
с `--resume` выполняет запросы. Все ошибки и прерывания остаются в журналах.

Файлы сдачи:

- `results/dev/results.json`: 65 исходных ID, включая явные ошибки.
- `results/prod/results.json`: 10 настоящих контрольных ID после выдачи.

Экспорт проверяет восемь официальных кодов, шкалы, evidence, N/A, scorer и
происхождение. Для повторов ошибочных DEV-записей используются
`--recovery-run-dir` и приватный `--provenance-out`; прежние успехи не заменяются.
Команды — [SUBMISSION.md](docs/SUBMISSION.md) и
[CONTROL_RUNBOOK.md](docs/CONTROL_RUNBOOK.md).

Стоп-код: **8 ноября 2026, 12:00 Алматы (UTC+5), 07:00 UTC**, подтверждён
участником. Контрольный час включает inference, экспорт, валидацию, commit
и push. Настоящий PROD и официальный доступ пока не предоставлены;
DEV не заменяет PROD.

## Проверки и качество

```bash
backend/.venv/bin/python -m unittest discover -s tests -q
backend/.venv/bin/python -m unittest discover -s backend/tests -q
node --test frontend/tests/*.test.js
backend/.venv/bin/python scripts/brev/smoke_inference.py
```

Браузерный сценарий с реальной моделью прошёл 8/8, сохранение после перезапуска
4/4. Отдельный синтетический UI-тест подтвердил апелляцию с пересчётом
100 → 92,31 и сохранением оригинала. Это инженерные проверки, **не Ground Truth**.

Активная инструкция v2.1 уточняет существующий JSON-контракт. Смысловой кандидат
не активирован. Независимой человеческой разметки нет; точность относительно GT
не измерена. AI-разбор выявил ошибки приветствий, N/A, завершения и пропуск
критичного нарушения. Тесты и успешность обработки не доказывают смысловую
правильность. [Разбор](docs/AI_QUALITY_REVIEW.md) ·
[Формы и инструкция](docs/ANNOTATION_GUIDE.md).

Исходные DEV/PROD, ключи, базы, журналы и артефакты не публикуются. Git разрешает
только два официальных файла результатов; rationale может содержать короткие
цитаты, полный transcript и audio URL экспорт не добавляет.

## Затраты и защита

Наблюдавшийся тариф L40S — $1.74/час; с повторным консервативным учётом fixed
128 GB диска — $1.756896/час. Новая цена проверяется перед созданием.
Сессия 16 минут 26 секунд оценена по тарифу примерно в $0.48; фактический счёт
и остаток кредита API не предоставил. Цены за токены и полная стоимость одной
оценки неизвестны. Общая аренда включает подготовку и простой; её деление на
число оценок не используется как unit cost.

[Питч и план слайдов](docs/PITCH.md) · [Демо до пяти минут](docs/DEMO_SCRIPT.md) ·
[Аудит требований](docs/REQUIREMENTS_AUDIT.md). PowerPoint готовится отдельно.
Dynamics 365, авторизация, ролевые кабинеты и расширенная аналитика — дальнейшее
развитие; в текущую финализацию они не входят.
