# Контрольные 10 чатов: получение, оценка и публикация

**Стоп-код: 8 ноября 2026, 12:00 по Алматы (UTC+5), то есть 07:00 UTC.**
Дата и время подтверждены пользователем. По приложению организаторов контроль
выдают за час до стоп-кода; ожидаемое окно — 11:00–12:00 по Алматы. Фактический
доступ пока не подтверждён. Время живого демо после отбора неизвестно.

Источник требований — `CQA_Student_Guide_Appendix_Results.pdf`, стр. 1–3, и
студенческий гайд, стр. 3–4. Подробности формата: [SUBMISSION.md](SUBMISSION.md).

## До контрольного часа

- Установить зависимости по README, подготовить локальный DEV и сохранённую
  исходную конфигурацию `codex_context/checklist.json` из восьми критериев.
  Версия для показа нового критерия хранится отдельно и не используется
  вместо официальной конфигурации экспорта.
- Поднять закреплённую модель и SSH-туннель по [BREV_SETUP.md](BREV_SETUP.md).
  Выгрузить именно её tokenizer/template локально. Успешно выполнить маленький
  синтетический запрос; одного `/api/health=ready` недостаточно.
- В `.env` сохранить отдельный inference-ключ. `BREV_API_KEY` управляет GPU и
  не становится `LLM_API_KEY`. `scripts/cqa.py` читает `.env` через существующий
  безопасный загрузчик backend и вызывает прежний CLI; значения ключей не печатает.
- Для контрольного запуска использовать `LLM_TIMEOUT_SECONDS=300` и
  `LLM_MAX_ATTEMPTS=1`. Число повторов ограничено; автоматического перебора
  оценок до удобного результата нет. Тарифы за токены неизвестны и остаются пустыми.
- Согласование каждой новой обычной GPU-сессии не требуется по последнему
  разрешению пользователя. Всё равно контролировать фактический тариф, диски,
  длительность и остаток доступного бюджета; не оставлять ресурс бессрочно.

### План доступности контрольной модели

Запускать контрольную сессию около **10:45 по Алматы** с конечным лимитом
**105 минут и $4**, после новой проверки котировки. При известной каталожной
оценке GPU плюс диск $1.756896/час весь лимит составляет около **$3.075**;
это тарифный расчёт, не фактический счёт и не подтверждение будущей цены.

```bash
# Будущий план на 10:45 Алматы; эта 105-минутная сессия ещё не выполнялась.
cqa_control_gpu_run="artifacts/brev/control-inference-$(date -u +%Y%m%dT%H%M%SZ)"
backend/.venv/bin/python scripts/brev/session.py quote
backend/.venv/bin/python scripts/brev/session.py create \
  --run-dir "$cqa_control_gpu_run" --minutes 105 --budget-usd 4
```

Подготовку модели, туннеля, tokenizer и синтетический smoke выполнить по README.
Сначала проверить существующие ресурсы: не создавать второй подходящий сервер.
Если котировка превышает выбранный предел, команда не должна создавать ресурс.
Плановое начало не гарантирует мгновенного выделения GPU; готовность подтвердить
реальным запросом до контрольного inference.

У helper очистка начинается за 15 минут до полного лимита. Для старта в 10:45:

| Событие | Алматы | UTC |
|---|---|---|
| Начало создания | 10:45 | 05:45 |
| Ожидаемое контрольное окно | 11:00–12:00 | 06:00–07:00 |
| Начало автоматической остановки/удаления | 12:15 | 07:15 |
| Полный предел с запасом на удаление | 12:30 | 07:30 |

Обычный пример `--minutes 60` запускает очистку через 45 минут. Если поднять
такую сессию в 10:45, она начнёт выключаться в **11:30**, посреди контрольного
часа. Для этой задачи его нельзя копировать как подходящий лимит.

Фактическая ограниченная сессия `final-delivery-20261108` с лимитом 60 минут/$3
предназначена для трёх DEV-recovery и технических проверок; после них ресурс
удаляется. Она не объявляется контрольной или готовой к будущему демо.
Демонстрация будет только после отбора, её точное время неизвестно. Для неё
нужен отдельный конечный интервал по подтверждённому времени, проверенная
доступность модели и учёт расходов; выключенный сервер не считается готовым.

## Получение настоящего набора

Использовать только контрольную ссылку/`group_id`, выданные организаторами.
Кнопка в PDF — **DEV**, её полная ссылка содержит подпись доступа. Не менять
значение наугад и не публиковать URL с подписью. Если организаторы передали
файл, сохранить его без изменения исходных ID как `codex_context/data/prod.json`.
Этот путь исключён из Git. Если файл ещё не выдан, остановиться на этом внешнем
условии; не создавать фиктивный результат сдачи.

Перед inference проверить происхождение файла, `isSuccess=true`, десять
уникальных записей и канал `agent chat` у всех. Схема сама по себе не доказывает,
что десять произвольных записей являются настоящим контролем.

## Команды из корня репозитория, Bash/Linux

Ниже процедура для **будущего настоящего PROD**. Она не означает, что контроль
уже получен или оценён. Синтаксис CLI проверен через `--help`; preflight и
валидация проверяются офлайн. Реальную сеть и оценки выполнять после выдачи данных.
Вместо пути tokenizer ниже выбрать каталог активной сессии, совпадающий с сервером.
Не класть inspect/preflight-файлы внутрь нового batch-каталога: он должен быть пуст.

```bash
umask 077
set -euo pipefail
cqa_control_input=codex_context/data/prod.json
cqa_control_root="artifacts/cqa/control-$(date -u +%Y%m%dT%H%M%SZ)"
cqa_control_run="$cqa_control_root/batch"
cqa_control_tokenizer=artifacts/brev/ACTIVE_SESSION/tokenizer
mkdir -p "$cqa_control_root"

backend/.venv/bin/python scripts/cqa.py inspect-dev \
  --input "$cqa_control_input" --out "$cqa_control_root/inspect.json"

backend/.venv/bin/python scripts/cqa.py batch --preflight \
  --input "$cqa_control_input" --expected-count 10 \
  --checklist codex_context/checklist.json --run-dir "$cqa_control_run" \
  --tokenizer "$cqa_control_tokenizer" \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --context-window 32768 --max-output-tokens 4096 --context-margin 512 \
  --out "$cqa_control_root/preflight.json"
```

Preflight должен вернуть `ready=true`, `records=10`, без oversized ID. Это
проверка полного контекста, а не разрешение молча обрезать длинные разговоры.
При ошибке входа/контекста сначала исправить причину; модель не вызывается.

Затем создать durable manifest и десять pending-записей **без обращения к API**.
Это сохраняет происхождение даже при слишком поздней выдаче данных. Дальнейший
inference выполняется с `--resume` на том же manifest, без смешивания настроек.

```bash
backend/.venv/bin/python scripts/cqa.py batch --initialize \
  --input "$cqa_control_input" --expected-count 10 \
  --checklist codex_context/checklist.json --run-dir "$cqa_control_run" \
  --tokenizer "$cqa_control_tokenizer" \
  --chat-template-kwargs '{"enable_thinking":false}' \
  --context-window 32768 --max-output-tokens 4096 --context-margin 512
```

## Ограничение времени inference

Оставить минимум 15 минут на экспорт, проверку и Git; целевой push — до 11:55,
последние пять минут — запас. Не ждать окончания часа для первого экспорта.
Следующая команда рассчитывает остаток по подтверждённому календарному сроку,
выбирает не больше 40 минут и учитывает 30 секунд принудительного завершения.
При поздней выдаче окно автоматически уменьшается. Пустое окно — повод выгрузить
явные ошибки уже сохранённых незавершённых записей, а не начать новый длинный
запуск. Предварительный `--initialize` обеспечивает manifest для такого экспорта;
одного preflight для этого недостаточно. Модельные ответы вручную не создаются.

```bash
cqa_eval_seconds="$(backend/.venv/bin/python - <<'PY'
from datetime import datetime
from zoneinfo import ZoneInfo
zone = ZoneInfo('Asia/Almaty')
deadline = datetime(2026, 11, 8, 12, 0, tzinfo=zone)
remaining = int((deadline - datetime.now(zone)).total_seconds()) - 15 * 60 - 30
print(max(0, min(40 * 60, remaining)))
PY
)"

if (( cqa_eval_seconds > 0 )); then
  if timeout --signal=INT --kill-after=30s "${cqa_eval_seconds}s" \
    backend/.venv/bin/python scripts/cqa.py batch --resume \
    --input "$cqa_control_input" --expected-count 10 \
    --checklist codex_context/checklist.json --run-dir "$cqa_control_run" \
    --tokenizer "$cqa_control_tokenizer" \
    --chat-template-kwargs '{"enable_thinking":false}' \
    --context-window 32768 --max-output-tokens 4096 --context-margin 512; then
    cqa_batch_exit=0
  else
    cqa_batch_exit=$?
  fi
else
  cqa_batch_exit=window_closed
fi
```

Не интерпретировать код 2/124/130/137 или `window_closed` как успешную полную оценку. Проверить
`summary.json` и сохранённые попытки. Остановка может оставить уже оплаченный
запрос с неизвестным исходом; не повторять его автоматически. При исправленном
техническом препятствии и достаточном времени продолжение делается явным
`--resume` на том же входе/модели/prompt. Повтор ошибок или прерванных запросов
требует отдельных явных retry-флагов и учёта предыдущих попыток.

## Экспорт, проверка и публикация

Успешный полный запуск экспортируется обычным `export-results`. Если время
истекло, `--allow-unprocessed` явно представляет pending/interrupted как error.
Это разрешённая форма ошибки, не N/A, не нулевая оценка и не скрытый пропуск ID.
Итоговое число записей всегда десять. Экспорт не вызывает модель.

```bash
backend/.venv/bin/python scripts/cqa.py export-results \
  --dataset prod --input "$cqa_control_input" --expected-count 10 \
  --checklist codex_context/checklist.json --run-dir "$cqa_control_run" \
  --team-name squad --model-mode local \
  --model-name Qwen/Qwen3-14B-AWQ --served-model-name doomsday-qwen3-14b-awq \
  --allow-unprocessed --out "$cqa_control_root/submission/results.json"

backend/.venv/bin/python - "$cqa_control_input" "$cqa_control_root/submission/results.json" <<'PY'
from collections import Counter
import sys
from cqa_eval.data import load_dev, read_json
from cqa_eval.submission import validate_submission
value = read_json(sys.argv[2])
validate_submission(value, load_dev(sys.argv[1]), 'prod')
print({'records': len(value['results']), 'statuses': dict(Counter(r['status'] for r in value['results']))})
PY
```

Проверить происхождение, статус каждого error и отсутствие секретов, аудиоссылок
и полного транскрипта. Разрешённый `rationale` может содержать короткую цитату.
Не переписывать сгенерированные ответы вручную ради прохождения проверки.

Для первой контрольной публикации скопировать проверенный файл с запретом
перезаписи. Если официальный путь уже существует, сохранить обе версии и
разобрать происхождение прежде чем менять его; следующая команда остановится.

```bash
backend/.venv/bin/python - "$cqa_control_root/submission/results.json" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1])
destination = Path('results/prod/results.json')
destination.parent.mkdir(parents=True, exist_ok=True)
with destination.open('xb') as stream:
    stream.write(source.read_bytes())
PY

git diff --check
git add -- results/prod/results.json
git diff --cached --check
backend/.venv/bin/python - <<'PY'
import subprocess
staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], text=True).splitlines()
if staged != ['results/prod/results.json']:
    raise SystemExit('Review staged files before committing; unrelated changes must be preserved')
PY
git commit -m "Add control-set results"
git push origin HEAD
git rev-parse HEAD
```

Проверить удалённый SHA ветки и наличие `results/prod/results.json` в GitHub
по ссылке репозитория, переданной организаторам. Наличие файла только локально
или commit без push не является сдачей. Не делать автоматический merge,
rebase, force push или массовый `git add -A`. Не добавлять raw PROD/DEV,
SQLite, `.env`, runtime-журналы, tokenizer и прочие `artifacts/`.

После расчётов сохранить отчёты и остановить/удалить созданный GPU, если он
больше не нужен. Если ресурс нужен для ближайшего живого демо, установить
конечное время остановки и заранее проверить настоящий inference. Выключенный
сервер или просмотр сохранённых результатов не обозначать как живую оценку.

## Что реально проверено до получения PROD

В этой подготовке исполнены `scripts/cqa.py inspect-dev --help`,
`batch --help`, `export-results --help`, справка GNU `timeout`, preflight
десяти копий **публичного синтетического** диалога тем же локальным tokenizer,
offline initialize (десять pending, ноль попыток), экспорт этих десяти записей
как явных error с `--allow-unprocessed` и проверка через `validate_submission`.
Все проверки офлайн, модель не вызывалась. Временные fixtures находятся только в `/tmp`, не в
`results/prod/` и не выдаются за контрольные предсказания.

Bash-блоки проверены синтаксически; нулевая ветка времени отдельно проверена:
`timeout` и inference не запускаются, сохраняется состояние `window_closed`.
Настоящие получение PROD, inference,
контрольный commit/push ещё не исполнены: для них отсутствует выданный набор.
