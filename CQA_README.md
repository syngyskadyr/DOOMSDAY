# ИИ-оценщик DOOMSDAY

Python 3.11+, пакет `src/cqa_eval`. Канонические входы — только `codex_context/`.
Старые `dev.json`, `checklist.json`, `BUSINESS_RULES.md`, `CODEX_PROMPT.md` в корне
не используются. Материалы комплекта и общий README не изменены.

## Установка в Windows PowerShell

После получения кода из Git отдельно получите у команды исходный DEV и сохраните
его как `codex_context/data/dev.json`. Исходные диалоги, аудио, секреты и
`artifacts/` не входят в репозиторий. Полный набор тестов и команды для DEV
требуют этого локального файла: `isSuccess=true`, 65 записей в `data.records`.
Синтетические примеры в `codex_context/eval/` включены в Git и доступны сразу.
`README_START.md` и `PACK_CHECK.json` описывают исходный комплект подготовки;
актуальная инструкция по реализованному компоненту — этот файл.

Из корня DOOMSDAY, при установленном Python:

```powershell
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-cqa.txt
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Активация окружения не нужна. Если `py` отсутствует, передайте полный путь к
`python.exe` вместо `py -3`. В среде разработки этот путь оказался таким:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
```

`requirements-cqa.txt` выделен для компонента. REST использует стандартную
библиотеку Python, поэтому нет скрытых повторов SDK. Схема и инструкция в
`src/cqa_eval/resources/` — точные копии соответствующих канонических файлов,
нужные для установки пакета независимо от текущего каталога.

## Шесть сценариев CLI

У каждого сценария есть `--help`. Глобальный `--context` ставится **перед**
названием сценария; его значение по умолчанию — `codex_context`.
`--dev`, `--checklist`, `--dialogue` задают явные альтернативные входы.
В обычном режиме `--dev` — оболочка с `isSuccess=true` и `data.records`;
`--dialogue` — отдельная запись. Без `--out` JSON выводится в stdout.

```powershell
& .\.venv\Scripts\python.exe -m cqa_eval --help
& .\.venv\Scripts\python.exe -m cqa_eval inspect-dev --id p1-ru-001 --out artifacts/cqa/inspect.json
& .\.venv\Scripts\python.exe -m cqa_eval dry-run --id p1-ru-001 --out artifacts/cqa/dry-run.json

# Именно СИНТЕТИЧЕСКИЙ пример формата, не предсказание на DEV:
& .\.venv\Scripts\python.exe -m cqa_eval validate --dialogue codex_context/eval/synthetic_dialogue.json --response codex_context/eval/synthetic_response.json --out artifacts/cqa/synthetic-validation.json
& .\.venv\Scripts\python.exe -m cqa_eval score --dialogue codex_context/eval/synthetic_dialogue.json --response codex_context/eval/synthetic_response.json --out artifacts/cqa/synthetic-score.json

# Пять стартовых debug-диалогов, ответы оставлены пустыми:
& .\.venv\Scripts\python.exe -m cqa_eval eval prepare --out artifacts/cqa/review.json
& .\.venv\Scripts\python.exe -m cqa_eval eval compare --labels artifacts/cqa/review.json --out artifacts/cqa/quality-empty.json

# После одного реального запуска и независимой ручной разметки:
& .\.venv\Scripts\python.exe -m cqa_eval validate --id p1-ru-001 --response artifacts/cqa/p1-ru-001.json
& .\.venv\Scripts\python.exe -m cqa_eval score --id p1-ru-001 --response artifacts/cqa/p1-ru-001.json
& .\.venv\Scripts\python.exe -m cqa_eval eval compare --labels artifacts/cqa/review.json --predictions artifacts/cqa/p1-ru-001.json --out artifacts/cqa/quality.json
```

Коды выхода: `0` — обработано, `2` — ошибка ввода/конфигурации/валидации/API.
`evaluate` требует `--id` и `--save`, обрабатывает ровно один диалог. При сбое
API сохраняет ошибку с метаданными вместо оценки. Остальные команды не вызывают
API. Dry-run показывает полный запрос и список отсутствующих настроек, без ключа.
Вывод inspect и запрос модели исключают `audio_url`; исходный загрузчик его
сохраняет для бэкенда. Аудио не скачивается. ISO timestamps не преобразуются
в позиции записи.

## Подключение модели

Сначала выбрать провайдера и доступную в его аккаунте текстовую модель. Заполнить
переменные по его официальной документации. Пакет не выбирает поставщика,
endpoint, модель или цену по бюджету. `.env` автоматически **не читается**;
передавайте переменные окружения процесса или загрузите их на стороне бэкенда.
Это также исключает случайное чтение чужих настроек проекта.

```powershell
$env:LLM_BASE_URL = '<API base URL из документации, включая /v1 если требуется>'
$env:LLM_MODEL = '<точное доступное имя модели>'
$env:LLM_API_STYLE = 'chat_completions' # либо responses, если поддерживается
$env:LLM_STRUCTURED_OUTPUT = 'json_schema' # либо документированный json_object
$env:LLM_TIMEOUT_SECONDS = '90'
$env:LLM_MAX_ATTEMPTS = '2' # всего попыток, то есть максимум один повтор
# Необязательно: известные ставки USD/миллион токенов выбранной модели.
# Если не заданы, стоимость null, а не 0.
$env:LLM_INPUT_USD_PER_MILLION = ''
$env:LLM_OUTPUT_USD_PER_MILLION = ''
$key = Read-Host 'LLM API key' -AsSecureString
$env:LLM_API_KEY = [System.Net.NetworkCredential]::new('', $key).Password
Remove-Variable key

& .\.venv\Scripts\python.exe -m cqa_eval dry-run --id p1-ru-001 --out artifacts/cqa/dry-run.json
& .\.venv\Scripts\python.exe -m cqa_eval evaluate --id p1-ru-001 --save artifacts/cqa/p1-ru-001.json
Remove-Item Env:LLM_API_KEY
```

HTTP-адаптер добавляет `/chat/completions` или `/responses` к явно заданному
API base URL. Форматы запросов сверены с [официальной документацией
Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
7 ноября 2026: `response_format` для Chat Completions и `text.format` для
Responses. Это описание двух форматов адаптера; совместимость конкретного
стороннего сервиса нужно проверять по его документации. Настроенного провайдера
в исходном окружении не было.

`json_schema` передаёт упрощённую структурную схему со `strict=true`; условные
и семантические ограничения проверяются полной схемой локально.
`json_object` включайте явно, если документация выбранной модели поддерживает
только JSON mode. Неподдерживаемый режим приводит к явной ошибке, скрытого
платного переключения нет. `temperature` и другие параметры генерации
не добавляются автоматически.

Таймаут — на сетевую операцию, от 0 до 300 секунд. Повторяются только ошибки
транспорта и HTTP 408/429/500/502/503/504, максимум одна повторная попытка.
JSON-ошибки, отказ модели, обрезанный ответ и ошибки авторизации не повторяются.
HTTP-перенаправления запрещены. Настройки `LLM_CONCURRENCY` и
`LLM_MAX_LIVE_DIALOGUES` из примера не расширяют одиночный режим CLI: пакет
не запускает очередь и не обрабатывает все 65 записей автоматически.

Метаданные содержат каждый фактический вызов: запрошенную/полученную модель,
usage, длительность, ошибку и известную оценку стоимости. При отсутствии usage
или цен итоговая стоимость `null`; даже успешный повтор не делает неизвестную
стоимость предыдущей попытки нулевой. Настроенные ставки дают оценку стоимости
токенов, а не подтверждённый счёт провайдера; скидки за кэш и другие начисления
в формуле не предполагаются. API-ключ не пишется в JSON, логи или Git.

## Интерфейсы для бэкенда

```python
from cqa_eval import evaluate_dialogue, validate_response, calculate_score
from cqa_eval.data import load_dev, read_json

dialogue = load_dev('codex_context/data/dev.json')[0]
checklist = read_json('codex_context/checklist.json')

# Только этот вызов обращается к модели:
result = evaluate_dialogue(dialogue, checklist)
# result: assessment, calculation, critical_error, checklist_snapshot,
# evidence (точный текст исходных сообщений), metadata

# Повторный расчёт, в том числе после решения по апелляции:
validated = validate_response(result['assessment'], dialogue, checklist)
calculation = calculate_score(checklist, validated)
```

`validate_response` возвращает неизменяемый `ValidatedAssessment`, связанный с
hash чек-листа и диалога. Scorer принимает именно этот объект, не упрощённый
словарь ответов. Для изменённого снимка проверку нужно выполнить заново.
Историческую оценку пересчитывайте по сохранённому `checklist_snapshot`.
Содержимое и версия чек-листа входят в запрос, поэтому новая формулировка,
вес или добавленный критерий не требуют веток в Python. Некорректный конфиг
отклоняется до отправки. Кэша в пакете нет, `cache_hit=false` всегда.

Модельный JSON строгий: `conversation_id`, `checklist_version`, `criteria`.
Метаданные и расчёт находятся только в обёртке. Функция `calculate_score` чистая:
без сети, модели, чтения файлов или ручных меток. Использует `Decimal` с
точностью 50 знаков и `ROUND_HALF_UP` только для итогового балла. Все денежные
и балльные Decimal в CLI сериализуются как десятичные **строки**, например
`"100.00"`; неизвестные значения остаются JSON `null`. Python возвращает Decimal.
Вклад шкалы — `answer/scale_max`; нормирование глобальное. Флаг не имеет веса.
Если есть неизвестный весовой ответ, численный итог `null`. Неизвестный флаг
сохраняется `null`, но уже подтверждённые эпизоды дают `true` даже при другом
неразрешённом эпизоде. Только известное отсутствие всех нарушений даёт `false`.
Секция, содержащая только флаги, отсутствует в балльных суммах.

Для любого `binary=no` реализация требует `checked_range`, чтобы не принимать
пропуск действия с одной случайной ссылкой. Для остальных решений требуется
evidence или диапазон. Для `na` проверяется разрешение, наличие `na_rule` в
конфиге и непустая `na_reason`. Применимость условия в естественном языке,
действительное отсутствие действия и смысловая связь доказательств требуют
оценки модели и ручного контроля: наличие корректного ID само по себе этого
не доказывает. Цитаты в `evidence` извлекаются из исходника, а не генерируются.

## Проверка качества

`eval prepare` создаёт самостоятельную форму на пяти debug-диалогах; можно
выбрать другие через `--ids`. Человек независимо заполняет все поля критериев:
допустимые `answer`, `reason`, evidence/checked_range, при N/A — `na_reason`,
при флаге — `violations`. После согласования отметить
`annotation_status="human_reviewed"` и указать непустой `reviewer`.
Неразрешённые ответы и частичная разметка не становятся эталоном.

Команда compare проверяет ручные ответы тем же валидатором. Только полные
проверенные записи входят в метрики. Незаполненный шаблон даёт
`reviewed_records=0`, `quality_metrics=null`, с объяснением. Отчёт показывает
точное совпадение по критериям, языкам и их сочетаниям, ошибки N/A, TP/FP/FN
и precision/recall каждого флага, MAE и число численных пар, coverage и ошибки
обработки. При нулевых знаменателях метрика `null`. Пропавшие или невалидные
предсказания остаются в знаменателях accuracy и coverage. Неразрешённый флаг
учитывается как отсутствие подтверждённого положительного предсказания, а
не как утверждение «Нет». Отдельно выводится число неизвестных предсказаний.

`evidence_review` — необязательная последующая ручная проверка **доказательств
предсказания**, отдельно от независимого заполнения эталонных ответов.
Укажите `checked=true`, `supported=true/false` только после такой проверки.
До неё количество проверенных доказательств равно нулю.

Все mock-ответы находятся только в тестах. `starter_cases.json` не загружается
production-оценщиком, гипотезы из него не участвуют в prompt или метриках.
Синтетические fixture-тесты проверяют код, а не точность модели. Пять debug
примеров не являются независимым holdout. Для него отложите новые сценарии;
переводы одного шаблона оставляйте в одной группе.

## Область ответственности и текущая проверка

Компонент предоставляет оценивание, CLI, проверку ответа и scorer.
Бэкенд отвечает за хранение версий, задач и апелляций, UI — за просмотр и
навигацию. Подписанные audio URL остаются во входном объекте бэкенда;
пакет их не логирует. Результаты с текстами разговоров и локальные отчёты
складывайте в игнорируемый `artifacts/cqa/`.

В поставленной среде не были заданы `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_STYLE`; реальных вызовов не выполнялось. Следующий шаг — заполнить
четыре настройки по документации выбранного провайдера и выполнить ровно
одну команду `evaluate` выше для `p1-ru-001`. Ни точность, ни работа с конкретной
моделью до такого запуска не подтверждены.
