# ИИ-оценщик DOOMSDAY

Актуальный запуск всего продукта и статус сдачи — в [README.md](README.md).
Активная инструкция v2.1 уточняет JSON-контракт; прежний v2 сохранён как baseline,
смысловой кандидат не активирован. Brev-модель проверена реальными запросами,
включая браузерный сценарий и сохранение после перезапуска. Исторические цифры
проверок ниже относятся к указанным итерациям, а не к текущей точности.
Для CLI с загрузкой корневого `.env` используйте `python scripts/cqa.py`;
прямой `python -m cqa_eval` по-прежнему читает только окружение процесса.

Актуальная подготовка prompt v2 и независимой разметки 15 диалогов описана в
[PROMPT_CALIBRATION.md](codex_context/eval/PROMPT_CALIBRATION.md).
Формула баллов сохранена; улучшение точности новой инструкции ещё не измерено.

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

## Сценарии CLI

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
API, кроме нового `batch`, описанного ниже. Dry-run показывает полный запрос и список отсутствующих настроек, без ключа.
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
`LLM_MAX_LIVE_DIALOGUES` из старого примера не используются. Для всего набора
есть отдельная явная команда `batch`; она всегда выполняет запросы последовательно.

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

Настройки локального `.env` заполнены для Qwen3-14B-AWQ на Brev через SSH.
Реальные одиночные и пакетные вызовы, UI-сценарий и сохранение проверены;
результаты и ограничения описаны в корневом README. При удалённом GPU адрес
в `.env` остаётся конфигурацией, а новые оценки недоступны. Смысловая точность
относительно независимой GT не измерена.

## Массовая обработка DEV и PROD

`batch` использует существующие `evaluate_dialogue`, `validate_response` и
`calculate_score`. Формула и бизнес-правила не меняются; версия инструкции
фиксируется для каждого запуска. Ручные метки production-оценщик не читает.
Вход — та же оболочка `isSuccess=true`, `data.records`; размер и ID произвольные.
`--expected-count 65` или `10` — дополнительная проверка выбранного входа.
`--input` — синоним `--dev`. Никакие ручные ответы не читаются.

Каждый диалог отправляется целиком отдельным запросом. До первого запроса
проверяется весь набор: число входных токенов с chat template + лимит генерации
+ запас `--context-margin` (по умолчанию 512) должны помещаться в реальное окно
сервера. Текст не сокращается и не объединяется с другими диалогами.
`--max-output-tokens` передаётся как `max_tokens` для Chat Completions; он
включает бюджет thinking. Обрезанный ответ не принимается как оценка.

Для живого batch обязателен `--tokenizer`: каталог локального токенизатора
той же модели и chat template, что у сервера. Загружается только токенизатор,
с `local_files_only=True`, `trust_remote_code=False`, без весов модели и GPU.
При нестандартном серверном template его нужно отразить в локальном каталоге
токенизатора. Дополнительные переменные шаблона передаются JSON-объектом
`--chat-template-kwargs '{"enable_thinking":false}'` (пример для non-thinking
Qwen). Флаг требует `--tokenizer`, сохраняет в том числе значение `false` и
не разрешает менять служебные параметры токенизации, обрезания или шаблон.
Эти переменные попадают в preflight и manifest: изменение thinking блокирует
resume даже при одинаковом числе токенов. Без флага сохраняются прежние
defaults токенизатора и совместимость manifest существующих прогонов.
Флаг не меняет HTTP-запросы или настройки inference: те же переменные должны
быть установлены на сервере. Автоматически проверить скрытые серверные настройки невозможно;
после smoke сверяйте `usage.input_tokens` с preflight. Несовпадение требует
проверки template/режима thinking до массового запуска.
Основание: [Hugging Face chat templates](https://huggingface.co/docs/transformers/chat_templating)
и [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html).
Batch пока требует `LLM_API_STYLE=chat_completions`; одиночный интерфейс
по-прежнему поддерживает и Responses.

Без токенизатора `--preflight` показывает только грубую оценку по UTF-8 байтам.
Она **не является реальным подсчётом токенов**, не доказывает переполнение
и не разрешает живой batch: `ready=false`, код выхода 2. Это полезная локальная
диагностика без сети. Для полной проверки установите зависимости токенизатора
в CPU-окружение либо используйте уже подготовленное окружение Brev:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-cqa-batch.txt
& .\.venv\Scripts\python.exe -m cqa_eval batch --input codex_context/data/dev.json --expected-count 65 --run-dir artifacts/cqa/dev-run --context-window 32768 --max-output-tokens 4096 --preflight --out artifacts/cqa/dev-preflight.json
```

32768 и 4096 здесь — диагностические значения, не утверждение о параметрах
вашего сервера. Для живого запуска используйте фактический `max-model-len`.

### Следующий запуск на Brev (Bash)

Команды не создают и не запускают GPU-инстанс. Выполнять на уже работающем
сервере после получения этого кода. Сохранить рабочие `LLM_API_KEY`,
`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_STYLE`, `LLM_STRUCTURED_OUTPUT` из
успешного одиночного запуска; `.env` сам по себе не загружается. Имена модели,
endpoint и режим thinking не подменять. Три переменные ниже задаются оператором
по фактической конфигурации: локальный каталог токенизатора, окно сервера,
выбранный лимит ответа (например, 4096, если достаточен по smoke).

```bash
python -m pip install -e .
# При отсутствии transformers: python -m pip install -r requirements-cqa-batch.txt
: "${QWEN_TOKENIZER_DIR:?Set the local serving tokenizer directory}"
: "${QWEN_CONTEXT_WINDOW:?Set the actual server max-model-len}"
: "${QWEN_MAX_OUTPUT_TOKENS:?Set the generation budget including thinking}"
export LLM_TIMEOUT_SECONDS=300
export LLM_MAX_ATTEMPTS=1
run_dir="artifacts/cqa/dev-qwen-$(date -u +%Y%m%dT%H%M%SZ)"
batch_args=(--input codex_context/data/dev.json --expected-count 65
  --run-dir "$run_dir" --tokenizer "$QWEN_TOKENIZER_DIR"
  --context-window "$QWEN_CONTEXT_WINDOW" --max-output-tokens "$QWEN_MAX_OUTPUT_TOKENS")

# Для non-thinking сервера из docs/BREV_SETUP.md добавить к тем же аргументам:
batch_args+=(--chat-template-kwargs '{"enable_thinking":false}')

# Без API: должен быть ready=true, oversized_ids=[] и missing_settings=[].
python -m cqa_eval batch "${batch_args[@]}" --preflight --out "${run_dir}-preflight.json"

# Ровно один новый диалог. Код 2 ожидаем, т.к. остальные ещё не обработаны.
python -m cqa_eval batch "${batch_args[@]}" --limit 1
python -m cqa_eval validate --id p1-ru-001 --response "$run_dir/records/0001/attempt-0001/result.json"
python -m cqa_eval score --id p1-ru-001 --response "$run_dir/records/0001/attempt-0001/result.json"

# После проверки smoke: оставшиеся диалоги одной командой.
python -m cqa_eval batch "${batch_args[@]}" --resume

# После сбоя используйте ТОТ ЖЕ run_dir и те же параметры.
python -m cqa_eval batch "${batch_args[@]}" --resume

# Только после выяснения причин ошибки: явное разрешение новых вызовов.
python -m cqa_eval batch "${batch_args[@]}" --resume --retry-errors
# Для прерванного запроса исход оплаты неизвестен; возможна повторная оплата:
python -m cqa_eval batch "${batch_args[@]}" --resume --retry-interrupted

# Офлайн-сборка полного внутреннего пакета. Выходной файл должен быть новым.
python -m cqa_eval batch-collect --input codex_context/data/dev.json --expected-count 65 \
  --run-dir "$run_dir" --out "${run_dir}-results.json"
```

Если smoke не нужен, первая команда `batch` без `--limit` и без `--resume`
обработает весь набор. Успешно сохранённый smoke входит в тот же запуск и
повторно не оплачивается. Не выполняйте весь блок механически: smoke — точка
проверки реального сервера, контекста, времени генерации и качества ответа.
Для 10 PROD-диалогов код остаётся прежним:

```bash
python -m cqa_eval batch --input codex_context/data/prod.json --expected-count 10 \
  --run-dir artifacts/cqa/prod-qwen-run-01 --tokenizer "$QWEN_TOKENIZER_DIR" \
  --context-window "$QWEN_CONTEXT_WINDOW" --max-output-tokens "$QWEN_MAX_OUTPUT_TOKENS"
```

### Хранение, возобновление и ограничения

В `run-dir` лежат `manifest.json`, `summary.json`,
`records/0001/attempt-0001/result.json` (или `error.json`) и журналы вызовов.
Соответствие порядкового номера и ID — в manifest/summary. Имена файлов не
строятся из недоверенных ID. Обёртка result сохраняет существующий ML-контракт:
`assessment`, `calculation`, `critical_error`, `checklist_snapshot`, `evidence`,
`metadata`. Каждый ответ проходит прежнюю схему/семантический валидатор и scorer.

Перед отправкой сохраняется отметка начала; полученный JSON провайдера
сохраняется до валидации, включая непригодные ответы. Результат пишется через
временный файл, flush/fsync и атомарную замену, до перехода к следующему диалогу.
Ошибка диска останавливает обработку. Блокировка каталога не допускает два
одновременных batch-процесса и освобождается ОС при завершении процесса.
Для надёжности используйте локальный диск с поддержкой блокировок/atomic rename.

Возобновление повторно валидирует сохранённые оценки и расчёты. Изменение
диалогов, чек-листа, prompt, модели, endpoint или параметров запуска блокирует
reuse; для другого эксперимента создайте новый каталог. Старые ошибки и
попытки остаются на месте. Повреждённый результат не заменяется автоматически.
Сохраняйте весь каталог запуска при переносе между машинами.

Абсолютной гарантии exactly-once у удалённого API нет: при разрыве связи или
убийстве процесса между ответом сервера и записью на диск возможна потеря
последнего ответа. `interrupted` не повторяется без `--retry-interrupted`;
перед повтором проверьте сохранённые `response_received-*.json` — там уже может
быть ответ. Автоматического восстановления wrapper из такого журнала пока нет.
Для незавершённого вызова usage/стоимость остаются неизвестными. Даже транспортный
retry может быть повторно оплачен; `LLM_MAX_ATTEMPTS=1` исключает автоматический retry.
Явные повторные запуски увеличивают расходы; лимит попыток действует на один
запуск оценивания диалога, а не на всю историю ручных retry.

HTTP 400/401/403/404/422 останавливают текущий batch, чтобы не повторять явную
ошибку конфигурации на всех записях. Остальные ошибки сохраняются по диалогу,
обработка продолжается. `summary.json` пересчитывается из сохранённых результатов
при resume; после жёсткого прерывания последняя копия summary может отставать.
В нём есть coverage, `needs_review`, `not_calculable`, число начатых/законченных
попыток, usage и стоимость по всей истории. Цена Brev за GPU-время не выводится
из токенов: её надо отдельно взять из фактического времени и тарифа инстанса.

`complete=true` означает наличие валидных ответов на все записи.
`ready_for_handoff=true` дополнительно требует отсутствия `needs_review` и
невычислимых оценок. Такие неопределённости не заменяются N/A или нулём.
`batch-collect` отказывает при неполном наборе/неопределённых оценках.
Код batch: 0 — ready, 2 — не готов/ошибка (включая ограниченный smoke),
130 — Ctrl+C. Summary и индивидуальные файлы остаются доступны.

### Формат сдачи организаторам

Предоставленное 8 ноября приложение `CQA_Student_Guide_Appendix_Results.pdf`
задаёт обязательный **JSON 1.0**: `results/dev/results.json` для 65 DEV и
`results/prod/results.json` для 10 контрольных чатов, включая ошибки.
Контрольный набор выдаётся за час до стоп-кода; за этот час нужно получить
данные, выполнить оценку, проверку, commit и push. Локального файла или commit
без push недостаточно.

Для сдачи предназначена отдельная офлайн-команда `export-results`:
она сопоставляет 01–08 с кодами `contact`–`critical`, yes/no/na с
true/false/null и message_id с номерами исходных реплик с 1. Балл повторно
рассчитывается общим scorer и записывается JSON number. Ошибки сохраняются
с исходным ID, `status="error"`, пустым `criteria`, `score=null` и причиной.
Команда не вызывает модель и не публикует файлы в Git.

`batch-collect` сохраняет прежний внутренний формат `cqa-batch-results-1`;
его нельзя переименовывать в официальный файл сдачи. Decimal в ML CLI по-прежнему
сериализуются строками; это отдельный контракт. Импорта batch-результатов в
backend нет: результаты интерфейса получаются обычным оцениванием через API.
`schema_version: "1.0"` в checklist.json относится к конфигурации, а не к сдаче.

Поля, команды DEV/PROD и порядок проверки — [docs/SUBMISSION.md](docs/SUBMISSION.md).

Все результаты и журналы — приватные локальные данные внутри игнорируемого
`artifacts/`; исходный PROD храните в игнорируемом `codex_context/data/`.
Ручная разметка, включая reserved-выборку, массовым оценщиком не читается и не
перезаписывается. Офлайн-тесты с синтетическими ответами проверяют техническую
надёжность, а не качество Qwen.

Проверка 07.11.2026: `python -m unittest discover -s tests -v` — 57 тестов
успешно (18 новых для batch); `python -m pip check` — без конфликтов.
В тестах весь DEV (65 записей) и 10 новых PROD-подобных ID обработаны через
mock HTTP. Проверены CLI/сборка, прерывание/resume, сохранение до следующего
вызова, ошибки диска/API, история retry, блокировки, изменение входов/настроек,
лимит контекста и отказ выдавать неполные оценки. Отдельный офлайн-smoke
`MLAdapter.result_state` из `origin/main` прошёл на синтетическом assessment
и вернул числовой JSON-балл. Это проверка внутреннего адаптера, не экспортёра
организаторов. Настоящий токенизатор Qwen в локальном окружении отсутствует;
реальная токенизация и GPU-вызовы в эту проверку не входят.
