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

Сначала завершить batch и сохранить его локальный run-каталог. Затем из корня:

```bash
backend/.venv/bin/python -m cqa_eval export-results \
  --dataset dev --input codex_context/data/dev.json --expected-count 65 \
  --run-dir artifacts/cqa/dev-run --team-name DOOMSDAY \
  --model-name Qwen/Qwen3-14B-AWQ --served-model-name doomsday-qwen3-14b-awq \
  --model-mode local --out results/dev/results.json
```

Для контрольного набора использовать `--dataset prod`, явный
`--input codex_context/data/prod.json`, `--expected-count 10`, его собственный
run-каталог и `--out results/prod/results.json`. Команда не подставляет DEV
вместо отсутствующего PROD. Существующий файл назначения не перезаписывается.

Экспорт повторно проверяет происхождение сохранённых результатов, чек-лист,
схему ответов, ссылки и арифметику. Незавершённые записи по умолчанию запрещены;
`--allow-unprocessed` явно включает их как ошибки, если часть набора не удалось
обработать до срока. Внутренний `batch-collect` сохраняет свой прежний формат
`cqa-batch-results-1`; для сдачи используется `export-results`.

Перед публикацией проверить готовый JSON и разрешить в Git только указанные
файлы сдачи. Исходные DEV/PROD, журналы модели, SQLite, записи, `.env` и ключи
остаются приватными. `rationale` содержит объяснение модели и может включать
короткие цитаты; полный транскрипт и `audio_url` экспортёр не добавляет.
