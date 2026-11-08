# Сдача результатов: JSON 1.0

Источник: предоставленное организаторами приложение
`CQA_Student_Guide_Appendix_Results.pdf`, страницы 1–3. Оно дополняет основной
гайд и задаёт обязательный формат файлов результатов.

В открытом репозитории до стоп-кода должны быть:

- `results/dev/results.json`: все 65 DEV-диалогов — 60 чатов и 5 звонков;
- `results/prod/results.json`: ровно 10 контрольных чатов, включая ошибки.

Час между выдачей контрольного набора и стоп-кодом включает получение данных,
оценивание, проверку JSON, commit и push. Локального файла или одного commit
недостаточно. Сам экспортёр не обращается к модели и не выполняет Git-команды.

## Формат

Корень: `schema_version="1.0"`, `team_name`, `model={mode,name}`, `results`.
`mode`: `local`, `external_api` или `hybrid`; собственный vLLM на Brev указывается
как `local`, имя — `Qwen/Qwen3-14B-AWQ`. Внутренний inference alias передаётся
отдельно при проверке происхождения результатов.

| Внутренний ID | Код сдачи | Значения |
| --- | --- | --- |
| 01 | contact | true / false |
| 02 | discovery | true / false / null |
| 03 | identification | true / false / null |
| 04 | consultation | 0 / 5 / 10 / 15 / 20 / null |
| 05 | empathy | true / false |
| 06 | dialogue | 0 / 4 / 7 / 10 |
| 07 | closure | true / false / null |
| 08 | critical | true / false |

Для `status="ok"` нужны ровно восемь критериев, каждый один раз, с `rationale`
и `evidence_turns`. Нумерация реплик — с 1 по исходному массиву, включая
клиента, бота и системные события. `null` означает только разрешённый N/A.
Итог `score.final_score` — JSON number, рассчитанный общим scorer и округлённый
до двух знаков. `score.critical_flag` совпадает со значением `critical` и не
обнуляет балл автоматически.

При ошибке сохраняется исходный ID, `status="error"`, `criteria=[]`,
`score=null` и краткая причина в `error`. Ошибку нельзя скрывать или заменять
нулевой оценкой. Неопределённый/неполный результат также нельзя выдавать за
успешную оценку.

## Экспорт без повторных вызовов модели

Для загрузки локального `.env` команды ниже можно вызывать через
`backend/.venv/bin/python scripts/cqa.py` вместо `-m cqa_eval`.

Сначала завершить batch и сохранить его локальный run-каталог. Не подставлять
alias из примера запуска: ранний сервер мог называться `qwen3-14b-awq`, а
поздние скрипты репозитория задают `doomsday-qwen3-14b-awq`. Для экспорта важен
alias фактического сохранённого запуска, не имя сегодняшнего сервера.
Извлеките его из manifest; экспортёр дополнительно сверит requested/actual
model в каждой сохранённой оценке:

```bash
SERVED_MODEL_NAME="$(python -c 'import sys; from cqa_eval.data import read_json; print(read_json(sys.argv[1])["model"])' artifacts/cqa/dev-run/manifest.json)"
```

Для пакета без run-каталога используйте его metadata, не меняя сам пакет:

```bash
SERVED_MODEL_NAME="$(python -c 'import sys; from cqa_eval.data import read_json; b=read_json(sys.argv[1]); names={r["metadata"]["model"] for r in b["results"]}; assert len(names)==1 and all(isinstance(n,str) and n.strip() for n in names); print(next(iter(names)))' artifacts/cqa/dev-collected.json)"
```

Затем из корня:

```bash
backend/.venv/bin/python -m cqa_eval export-results \
  --dataset dev --input codex_context/data/dev.json --expected-count 65 \
  --run-dir artifacts/cqa/dev-run --team-name DOOMSDAY \
  --model-name Qwen/Qwen3-14B-AWQ --served-model-name "$SERVED_MODEL_NAME" \
  --model-mode local --out results/dev/results.json
```

Для контрольного набора использовать `--dataset prod`, явный
`--input codex_context/data/prod.json`, `--expected-count 10`, его собственный
run-каталог и `--out results/prod/results.json`. Команда не подставляет DEV
вместо отсутствующего PROD. Существующий файл назначения не перезаписывается.

### Отдельный повтор исторических ошибок DEV

Старый run сохраняется целиком. Новый run содержит ровно исходные ошибочные
ID в исходном порядке, с теми же данными/моделью/официальным checklist,
одной свежей попыткой на ID и без автоматических повторов. Изменение инструкции
фиксируется новым instruction_hash; общий однородный manifest не подделывается.

```bash
backend/.venv/bin/python scripts/cqa.py export-results \
  --dataset dev --input codex_context/data/dev.json --expected-count 65 \
  --run-dir artifacts/brev/approved-smoke-20261108/dev-run-version-bound \
  --recovery-run-dir artifacts/brev/final-delivery-20261108/recovery \
  --team-name DOOMSDAY --model-name Qwen/Qwen3-14B-AWQ \
  --served-model-name "$SERVED_MODEL_NAME" --model-mode local \
  --provenance-out artifacts/brev/final-delivery-20261108/submission/provenance.json \
  --out artifacts/brev/final-delivery-20261108/submission/results.json
```

Экспорт требует полное покрытие исходных ошибок, проверяет raw response против
saved assessment и сохраняет источник каждой записи в приватном audit.
62 исторических успеха не переоцениваются и не заменяются. Новый неуспех
остаётся явным error; выбирать удобный ответ из нескольких нельзя. Перед
заменой опубликованного DEV сохранить прежний файл, проверить новый и только
затем перенести его в `results/dev/results.json` с отдельным commit.

Экспорт повторно проверяет происхождение сохранённых результатов, чек-лист,
схему ответов, ссылки и арифметику. Незавершённые записи по умолчанию запрещены;
`--allow-unprocessed` явно включает их как ошибки, если часть набора не удалось
обработать до срока. Внутренний `batch-collect` сохраняет свой прежний формат
`cqa-batch-results-1`; для сдачи используется `export-results`.

### Экспорт уже собранного файла batch-collect

`export-results` принимает два взаимоисключающих источника: `--run-dir` для
каталога исходного запуска либо `--collected` для готового JSON-пакета команды
`batch-collect`. Формат ML-пакета не изменён: `format="cqa-batch-results-1"`,
`organizer_format_verified=false`, `manifest_hash`, массив `results` с обёртками
`assessment/calculation/critical_error/checklist_snapshot/metadata/evidence`
и `summary` полного запуска. Совместимость проверяется сквозными офлайн-тестами
`run_batch → collect_results → export_collected_submission` на 65 и 10 записях.

Экспортёр сверяет ID и хеши с исходным набором, официальный checklist и модель,
повторно запускает существующий валидатор и общий scorer, проверяет сохранённые
баллы и summary. Порядок результата восстанавливается по исходному набору.
Пути из summary не открываются; каталог запуска для этого режима не нужен.
Хеш manifest в пакете — ссылка на происхождение, а не независимое подтверждение:
без оригинального каталога нельзя сверить его байты и raw-журналы. Для полного
аудита и recovery используйте прежний `--run-dir`.

```bash
python -m cqa_eval export-results \
  --dataset dev --input codex_context/data/dev.json --expected-count 65 \
  --collected artifacts/cqa/dev-collected.json --team-name DOOMSDAY \
  --model-name Qwen/Qwen3-14B-AWQ --served-model-name "$SERVED_MODEL_NAME" \
  --model-mode local --out results/dev/results.json

python -m cqa_eval export-results \
  --dataset prod --input codex_context/data/prod.json --expected-count 10 \
  --collected artifacts/cqa/prod-collected.json --team-name DOOMSDAY \
  --model-name Qwen/Qwen3-14B-AWQ --served-model-name "$SERVED_MODEL_NAME" \
  --model-mode local --out results/prod/results.json
```

Имя модели и alias должны соответствовать сохранённым metadata. В PowerShell
используйте `& .\.venv\Scripts\python.exe` и одну строку вместо Bash-переносов.
Официальный DEV уже существует в актуальном main: команда намеренно откажется
его перезаписывать. Для проверки нового экспорта задайте новый путь в `artifacts/`,
сохраните прежнюю версию перед отдельной заменой официального файла. Экспорт сам
ничего не коммитит и не отправляет.

**Ошибки обработки:** `batch-collect` по существующему контракту не создаёт
пакеты с ошибками или `needs_review`. Не удаляйте такие диалоги для получения
«полного» пакета. Экспортируйте исходный run через `--run-dir`: error/interrupted/
unresolved станут `status="error", criteria=[], score=null` с безопасной причиной;
для ещё не начатых диалогов нужно явное `--allow-unprocessed`. Потерянный или
повторный ID в собранном пакете отклоняется, а не подменяется придуманной ошибкой.
Режим `--collected` не принимает recovery/provenance/allow-unprocessed.

В актуальной `.gitignore` уже разрешены оба точных пути
`results/dev/results.json` и `results/prod/results.json`; исходники, журналы,
окружение и прочие файлы результатов остаются исключёнными. Это проверяется
тестом `git check-ignore --no-index`, включая уже отслеживаемый DEV-файл.

### Проверки совместимости на Windows

`python -m unittest discover -s tests -p '*submission*.py'` проверяет оба входа
экспортёра, N/A, все числовые ступени, evidence с исходными номерами, ошибки,
дубликаты/пропуски ID, неизменность score и отсутствие перезаписи файлов.
`python -m unittest discover -s backend/tests` проверяет backend отдельно.

Снимок артефактов экспорта исключает только корневой служебный `.lock`:
Windows запрещает читать удерживаемый байт блокировки даже собственному процессу.
Все содержательные файлы запуска по-прежнему хешируются и проверяются.
Тест отказа соединения backend использует детерминированный HTTP mock;
проверки реальных локальных HTTP-ответов и таймаута сохранены.

`tests/test_brev_session.py` относится к Linux-инструменту управления Brev и
импортирует `fcntl`; на Windows этот модуль недоступен. Для запуска остальных
тестов на Windows без изменения облачного инструмента:

```python
import unittest
from pathlib import Path
loader = unittest.TestLoader()
suite = unittest.TestSuite(
    loader.discover("tests", pattern=p.name)
    for p in sorted(Path("tests").glob("test_*.py"))
    if p.name != "test_brev_session.py"
)
result = unittest.TextTestRunner().run(suite)
raise SystemExit(not result.wasSuccessful())
```

На Linux используйте полный `python -m unittest discover -s tests`.

## Финальная проверка и отправка DEV / PROD

Команды ниже — Bash из корня репозитория с установленным актуальным пакетом.
Они не запускают модель. Исходные run-каталоги и наборы должны быть получены
у владельца ML-запуска; нельзя восстанавливать их из опубликованных баллов.
`--served-model-name` определяется сохранёнными metadata: имя текущего
запущенного vLLM само по себе не подтверждает имя исторического запуска.

### Полностью успешный набор: collect → export → validation

Для PROD установите `DATASET=prod`, `EXPECTED=10` и его собственный `RUN_DIR`.
Если есть ошибки, этот блок намеренно останавливается на `batch-collect`;
переходите к следующему блоку, не удаляя записи и не редактируя assessments.

```bash
set -euo pipefail
DATASET=dev
EXPECTED=65
RUN_DIR=artifacts/cqa/dev-run # фактический каталог от ML-разработчика
SOURCE="codex_context/data/$DATASET.json"
OUT_DIR="artifacts/submission-check/$(date -u +%Y%m%dT%H%M%SZ)-$DATASET"
mkdir -p artifacts/submission-check
mkdir "$OUT_DIR" # новый каталог; совпадение имени считается ошибкой
SERVED_MODEL_NAME="$(python -c 'import sys; from cqa_eval.data import read_json; print(read_json(sys.argv[1])["model"])' "$RUN_DIR/manifest.json")"

python -m cqa_eval batch-collect --input "$SOURCE" --expected-count "$EXPECTED" \
  --run-dir "$RUN_DIR" --out "$OUT_DIR/collected.json"
python -m cqa_eval export-results --dataset "$DATASET" --input "$SOURCE" \
  --expected-count "$EXPECTED" --collected "$OUT_DIR/collected.json" \
  --team-name DOOMSDAY --model-name Qwen/Qwen3-14B-AWQ \
  --served-model-name "$SERVED_MODEL_NAME" --model-mode local \
  --out "$OUT_DIR/results.json"
python -m cqa_eval validate-results --dataset "$DATASET" --input "$SOURCE" \
  --response "$OUT_DIR/results.json" --out "$OUT_DIR/validation.json"
```

### Текущий DEV с одной ошибкой: безопасный путь через run-dir

Сохранённый DEV содержит 64 `ok` и одну запись `p1-ru-023`:
`status="error", criteria=[], score=null`, опубликованная причина —
«Не удалось получить валидную оценку модели.»
По [DEV_PROVENANCE.md](DEV_PROVENANCE.md) завершённый recovery-ответ содержит
`violations` у нефлагового критерия. Соответствующее правило валидатора —
`Only flags may contain violations`. Это техническая ошибка контракта, не N/A
и не оценка качества разговора. Без raw-журнала нельзя подтвердить конкретный
criterion_id и повторно воспроизвести отказ; править ответ вручную нельзя.

Текущий официальный DEV составлен из baseline и отдельного recovery.
Для воспроизведения всех 64 успехов нужны **оба** каталога: экспорт только
baseline вернёт старые три ошибки, а не текущий итог. После получения каталогов
в указанные ниже места используйте новый путь:

```bash
set -euo pipefail
RUN_DIR=artifacts/brev/approved-smoke-20261108/dev-run-version-bound
RECOVERY_DIR=artifacts/brev/final-delivery-20261108/recovery
DATASET=dev
OUT_DIR="artifacts/submission-check/$(date -u +%Y%m%dT%H%M%SZ)-dev-recheck"
mkdir -p artifacts/submission-check
mkdir "$OUT_DIR"
SERVED_MODEL_NAME="$(python -c 'import sys; from cqa_eval.data import read_json; print(read_json(sys.argv[1])["model"])' "$RUN_DIR/manifest.json")"
sha256sum results/dev/results.json > "$OUT_DIR/published-before.sha256"

python -m cqa_eval export-results --dataset dev \
  --input codex_context/data/dev.json --expected-count 65 \
  --run-dir "$RUN_DIR" --recovery-run-dir "$RECOVERY_DIR" \
  --team-name DOOMSDAY --model-name Qwen/Qwen3-14B-AWQ \
  --served-model-name "$SERVED_MODEL_NAME" --model-mode local \
  --provenance-out "$OUT_DIR/provenance.json" --out "$OUT_DIR/results.json"
python -m cqa_eval validate-results --dataset dev --input codex_context/data/dev.json \
  --response "$OUT_DIR/results.json" --out "$OUT_DIR/validation.json"
sha256sum -c "$OUT_DIR/published-before.sha256"
```

Неверный alias или несовместимые источники дадут отказ, а не подмену metadata.
Если оба manifest содержат `qwen3-14b-awq`, именно он используется автоматически;
если содержат `doomsday-qwen3-14b-awq`, используется этот alias. Разные alias
двух источников требуют разбора с владельцем ML-запуска; не переписывайте hashes.
Для обычного PROD без recovery используйте `--run-dir` без двух recovery-флагов;
если до срока остались pending, добавьте явный `--allow-unprocessed`.

### Перенос проверенных файлов и Git

После просмотра нового файла выполните для каждого набора (`DATASET` и
`OUT_DIR` относятся к только что проверенному экспорту):

```bash
set -euo pipefail
DEST="results/$DATASET/results.json"
mkdir -p "results/$DATASET"
if [ -e "$DEST" ]; then
  test ! -e "$OUT_DIR/results.before.json"
  cp -- "$DEST" "$OUT_DIR/results.before.json"
fi
cp -- "$OUT_DIR/results.json" "$DEST"
python -m cqa_eval validate-results --dataset "$DATASET" \
  --input "codex_context/data/$DATASET.json" --response "$DEST"
```

Когда оба файла готовы, из выбранной командой ветки сдачи:

```bash
set -euo pipefail
python -m cqa_eval validate-results --dataset dev --input codex_context/data/dev.json --response results/dev/results.json
python -m cqa_eval validate-results --dataset prod --input codex_context/data/prod.json --response results/prod/results.json
test -z "$(git diff --cached --name-only)" # не включать чужие staged-изменения
git add -- results/dev/results.json results/prod/results.json
git diff --cached --check
git diff --cached --name-only | python -c 'import sys; p=set(sys.stdin.read().splitlines()); assert p and p <= {"results/dev/results.json","results/prod/results.json"}'
git diff --cached --stat
git commit -m "chore: submit DEV and PROD results"
git push origin HEAD
git ls-remote --heads origin "$(git branch --show-current)"
git rev-parse HEAD
```

Сверьте локальный hash с удалённым. Оба файла должны быть доступны организаторам
в **открытом** репозитории до стоп-кода; один локальный commit недостаточен.
Исходники, collected-пакеты, provenance, raw responses, .env и логи не добавляются.
Ни одна команда проверки/экспорта сама не выполняет push, merge или force push.

Перед публикацией проверить готовый JSON и разрешить в Git только указанные
файлы сдачи. Исходные DEV/PROD, журналы модели, SQLite, записи, `.env` и ключи
остаются приватными. `rationale` содержит объяснение модели и может включать
короткие цитаты; полный транскрипт и `audio_url` экспортёр не добавляет.
