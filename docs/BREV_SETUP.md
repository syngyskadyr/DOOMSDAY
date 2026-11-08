# Живой inference на NVIDIA Brev

Модель работает на отдельном GPU, backend и frontend — локально. Проверена
конфигурация Crusoe `l40s-48gb.1x`, L40S 48 GB, fixed 128 GB, Ubuntu/Python3.10,
драйвер 565.57.01. Используются Qwen/Qwen3-14B-AWQ revision
`31c69efc29464b6bb0aee1398b5a7b50a99340c3`, vLLM0.8.5.post1,
torch2.6.0+cu124, transformers4.51.3 и huggingface-hub0.30.2.

## Ключи и зависимости

Локальные зависимости — requirements-backend.txt и requirements-cqa-batch.txt,
пакет установлен через `pip install -e .`. Официальный Brev CLI v0.6.337 в этой
среде установлен в `backend/.venv/bin/brev`; бинарник/venv не входят в Git.
`BREV_API_KEY` читается из корневого `.env` без печати и передаётся CLI/дочернему
API-клиенту только через окружение. Login сохранять не требуется, `--token` не
используется. `DEBUG_HTTP` удаляется; аналитика отключена.

Inference-ключ генерируется отдельно, хранится в приватном run-каталоге 0600,
копируется на **новый принадлежащий сессии сервер** по SSH. `VLLM_API_KEY`
передаётся серверу окружением. BREV-ключ на сервер не копируется и не становится
`LLM_API_KEY`. Веса устанавливаются только удалённо, локально копируется tokenizer.

## Один воспроизводимый запуск

Все команды из корня. Для новой сессии выбрать новый каталог; `demo` ниже —
пример имени. Команды создания вызывают платные ресурсы явно.

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

`create` сначала проверяет инвентарь и актуальный тариф с диском. Существующие
ресурсы блокируют автоматическое создание второго. Создаётся ровно один сервер,
собственный ledger связывает случайное имя с environment ID. До создания
watchdog подтверждает старт; за 15 мин до выбранного лимита он начинает stop/delete.
Ошибки создания вызывают раннюю очистку, а не новый create. Долгий провижининг
или установка входят в лимит. Не запускать тесты после начала cleanup.

`prepare` использует существующие скрипты install/start/serve; драйвер не
обновляется. `python3-dev` и compiler нужны xgrammar/Triton. Запуск проверяет
GPU, минимальный драйвер 550.54.15, версии и приватность inference-файла.
`forward` открывает localhost8001→удалённыйlocalhost8000. Web-console Tunnel,
который требует браузерной авторизации, не используется.

Процесс может грузить веса после завершения `prepare`. `ready` проверяет
GET `/health` и `/v1/models`, без генерации. Только `model_status=ready`
подтверждает наблюдаемую доступность engine/alias. Затем реальный smoke:

```bash
backend/.venv/bin/python scripts/brev/smoke_inference.py --live --stage small --run-dir artifacts/brev/demo/small
backend/.venv/bin/python scripts/brev/smoke_inference.py --live --stage cqa --run-dir artifacts/brev/demo/cqa
backend/.venv/bin/python -m backend --host 127.0.0.1 --port 8000
```

Каждый smoke требует новый каталог и не повторяется автоматически. Первый
проверяет чистый JSON и finish_reason=stop, второй — существующий адаптер,
валидатор и scorer на публичной синтетике. Это не метрика точности.

## Ограничения генерации и preflight

Launcher закрепляет оригинальный tokenizer и добавляет
`{%- set enable_thinking = false -%}` в его chat template. Сохранённый template
одинаков для сервера и локального preflight; reasoning parser не нужен.
Вывод JSON из message.content принимается только при finish_reason=stop.
Контекст 32768, server max_new_tokens4096, V0 eager, max_num_seqs1,
guided-decoding xgrammar. Temperature0.7/top_p0.8/top_k20/min_p0.0 сохранены.
Параметры известны, побитовое воспроизведение случайных генераций не обещается.

Перед любым новым набором использовать копию `RUN/tokenizer` и тот же
transformers4.51.3. Передаваемые CLI параметры:

```text
--tokenizer artifacts/brev/demo/tokenizer
--chat-template-kwargs '{"enable_thinking":false}'
--context-window 32768 --max-output-tokens 4096 --context-margin 512
```

Каждый полный диалог должен помещаться вместе с 4096+512. Обрезание запрещено.
CLI bridge `scripts/cqa.py` загружает `.env`; не вставлять секреты в shell/argv.
Серверный предел не задаётся вымышленной переменной `.env`.

## Расходы и завершение

Актуальная котировка проверяется командой quote. Наблюдавшийся тариф GPU 1.74 USD/hr;
консервативно 128GB×0.000132USD/GB/hr добавляется повторно, хотя CLI считает fixed
диск включённым: итого 1.756896USD/hr. Тарифная оценка не является invoice.
Остаток кредита недоступен через проверенные orgGET, поэтому не объявляется 50USD
по факту наблюдения. Автопополнение/покупка кредитов не включаются.

После нужных вычислений сохранить аудит и удалить сервер:

```bash
backend/.venv/bin/python scripts/brev/session.py capture --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py delete --run-dir artifacts/brev/demo
backend/.venv/bin/python scripts/brev/session.py status --run-dir artifacts/brev/demo
```

Stop и accepted DELETE не доказывают удаление: ожидается raw API 404 и
`environment_absent=true`. Скрипт проверяет name/ID до мутаций, повторяет только
свой target ограниченно и сохраняет proof. Watchdog продолжает наблюдение после
запроса удаления. Выход из SSH и остановка vLLM аренду не завершают.

Для живого демо выбрать время запуска заранее: удалённая установка в проверенной
сессии заняла несколько минут, затем загрузка модели. При выключенном GPU
сохранённые оценки и апелляции доступны, новые оценки — нет. Демо после отбора
команд пока без подтверждённого времени; бессрочный GPU ради ожидания не оставляется.

## Подтверждения и ограничения

Сессия MVP прошла реальные tiny/CQA, браузер 8/8 и restart4/4. Первый CQA наv2
отклонён из-за отсутствия evidence/range у отрицательного флага; ответ сохранён.
Форматный v2.1 не меняет scorer/веса/checklist/provider schema и прошёл синтетический
smoke и два UI-вызова. Смысловой кандидат не активирован; независимой GT нет.
Журналы, базы, токенизаторы и ключи находятся в игнорируемом artifacts/.

Технические первоисточники: [vLLM pinned code](https://github.com/vllm-project/vllm/tree/v0.8.5.post1),
[CUDA12.4 driver](https://docs.nvidia.com/cuda/archive/12.4.1/cuda-toolkit-release-notes/index.html),
[Qwen3 model](https://huggingface.co/Qwen/Qwen3-14B-AWQ),
[Brev CLI](https://github.com/brevdev/brev-cli/tree/v0.6.337).

Watchdog работает локально: во время оплаченной сессии ноутбук должен оставаться
включённым и иметь сеть. Перед выключением требуется подтверждённая очистка
ресурса через API; сам факт наличия таймера не заменяет эту проверку.
