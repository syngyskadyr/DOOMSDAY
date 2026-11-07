# DOOMSDAY: Qwen на NVIDIA Brev

## Фактически подготовленная конфигурация

Подтверждены маленький JSON-запрос, синтетическая CQA-оценка через существующий
адаптер, общая схема/scorer и полный HTTP-сценарий приложения с апелляцией,
новым критерием и сохранением истории после перезапуска. Браузерная проверка
недоступна в этой среде; HTTP-проверка не выдаётся за визуальную проверку UI.
`health=ready` сам по себе доказывает только наличие настроек.

DEV обработан целиком: первый проход — 56 валидных оценок и 9 ошибок. После
одного явного повторного прохода только по ошибкам — 62 оценки и 3 ошибки.
Файл сдачи сохраняет все 65 ID, включая ошибки. Независимой ручной разметки
нет; выборочный AI-разбор обнаружил смысловые ошибки, поэтому это не заявление
о точности или готовности модели к промышленному использованию.

Первый маленький синтетический запрос выявил отсутствие `Python.h` при JIT
компиляции xgrammar/Triton: engine завершился, лог первой ошибки сохранён.
После установки `python3.10-dev` повторный синтетический запрос прошёл.
Используется обычный backend `xgrammar`.

| Параметр | Значение |
|---|---|
| Тип инстанса | Crusoe `l40s-48gb.1x` |
| GPU / драйвер | 1 × L40S 48 GB / 565.57.01 |
| Удалённый Python | 3.10.12 |
| vLLM / torch | 0.8.5.post1 / 2.6.0+cu124, CUDA 12.4 |
| transformers / huggingface-hub | 4.51.3 / 0.30.2 |
| Модель | `Qwen/Qwen3-14B-AWQ` |
| Model/tokenizer commit | `31c69efc29464b6bb0aee1398b5a7b50a99340c3` |
| Served model name | `doomsday-qwen3-14b-awq` |
| Контекст / предел генерации | 32768 / 4096 токенов |
| Параллельность | 1, V0 engine, eager execution |
| API на удалённом сервере | `127.0.0.1:8000/v1` |

`torch.cuda.is_available()` вернул `true`; `pip check` прошёл. Выбрана версия
vLLM с CUDA 12.4, совместимая с обнаруженным драйвером; драйвер не обновлялся.
Код backend, HTTP-контракт и frontend ради смены провайдера не меняются.

## Бюджет и завершение

Пользователь согласовал **не более $5 и 90 минут** для созданного тестового
ресурса с удалением после сохранения логов. Каталожная GPU-ставка — $1.74/час.
Консервативная оценка с резервом на 128 GB диска — **$1.756896/час**, или
**$2.635344 за 90 минут**. Это расчётный верхний ориентир данной конфигурации,
не подтверждённый счёт: часть хранения может уже входить в фиксированную цену.
Отсчёт включает установку и скачивание весов, а не только генерацию.

К 80-й минуте начать завершение: сохранить приватные логи, результаты и
метаданные локально в игнорируемый `artifacts/`, завершить тест и удалить
**только созданный тестовый инстанс** с подтверждением его отсутствия
через API. При успешной ранней проверке завершить раньше. Не удалять другие
инстансы или пользовательские данные. Выход из SSH, остановка vLLM и закрытие
tunnel сами по себе не прекращают аренду. Перед удалением проверить, не остаётся
отдельно оплачиваемый диск; фактические списания сверять с Brev, если доступны.
Автопополнение и покупка кредитов не разрешены. Цены за токены неизвестны:
стоимость аренды нельзя записывать в токенные ставки.

## Удалённая установка и запуск

Установлено в `~/workspace/doomsday-brev/venv` только на GPU-сервере:

```bash
sudo apt-get install -y --no-install-recommends python3.10-venv python3.10-dev
python3 -m venv "$HOME/workspace/doomsday-brev/venv"
"$HOME/workspace/doomsday-brev/venv/bin/python" -m pip install --no-cache-dir \
  --only-binary=vllm,torch 'vllm==0.8.5.post1' \
  'transformers==4.51.3' 'huggingface-hub==0.30.2'
"$HOME/workspace/doomsday-brev/venv/bin/python" -m pip check
```

GCC уже присутствовал в образе. Одного venv недостаточно: xgrammar/Triton
может компилировать CUDA-обёртки при первом запросе, для чего требуются Python
headers выбранной версии и компилятор. Установка заголовков не меняет драйвер.

[serve_vllm_compat.sh](../scripts/brev/serve_vllm_compat.sh) не создаёт ресурсы,
не устанавливает пакеты и не меняет драйвер. Перед запуском он проверяет версии,
GPU и минимальный драйвер 550.54.15 для CUDA 12.4.1. Запуск **на Brev**:

```bash
bash serve_vllm_compat.sh --remote-brev \
  "$HOME/workspace/doomsday-brev/venv/bin/python"
```

Отдельный случайный inference-ключ читается из
`~/.config/doomsday/inference.key` (владелец — текущий пользователь, права
`0600` или строже). Другой путь можно передать третьим аргументом. Ключ
передаётся дочернему vLLM через `VLLM_API_KEY`, не через argv. `BREV_API_KEY`
используется только локальным управлением инфраструктурой; на GPU не копируется
и `LLM_API_KEY` не заменяет. При передаче inference-ключа использовать stdin SSH
из Python, не печатать значение, заголовки или `.env`. Launcher удаляет
`BREV_*` и `DEBUG_HTTP`, выключает телеметрию и журналирование запросов.

В этой версии нет серверного `--default-chat-template-kwargs`. Launcher
загружает **только tokenizer** закреплённой модели на удалённой машине,
добавляет в начало исходного шаблона `{%- set enable_thinking = false -%}`
и сохраняет отдельный каталог:

```text
~/.cache/doomsday/qwen3-nonthinking/31c69efc29464b6bb0aee1398b5a7b50a99340c3/
```

Сервер получает этот tokenizer и этот же `chat_template.jinja`. Пустой
закрытый think-блок входит в prompt; generated content должен содержать JSON.
Reasoning parser не включается. JSON schema обеспечивается
`--guided-decoding-backend xgrammar`; ответ всё равно проходит существующую
локальную схему и scorer. `--generation-config vllm` с override
`max_new_tokens=4096` задаёт серверный предел даже без `max_tokens` от backend.
При `finish_reason=length` результат отклоняется. Несуществующей переменной
`.env` для лимита генерации нет. Веса разрешает и скачивает vLLM **на Brev**.

## SSH tunnel и настройки backend

SSH alias на локальной машине создаётся через `brev refresh`. Прямой tunnel:

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:8001:127.0.0.1:8000 doomsday-cqa-1107-223417
```

После готовности сервера программно обновить нужные поля корневого `.env`,
сохранив остальные настройки. Значение inference-ключа здесь не приводится:

```dotenv
LLM_BASE_URL=http://127.0.0.1:8001/v1
LLM_MODEL=doomsday-qwen3-14b-awq
LLM_API_KEY=<отдельный inference-ключ, совпадающий с серверным>
LLM_API_STYLE=chat_completions
LLM_STRUCTURED_OUTPUT=json_schema
LLM_TIMEOUT_SECONDS=300
LLM_MAX_ATTEMPTS=1
LLM_INPUT_USD_PER_MILLION=
LLM_OUTPUT_USD_PER_MILLION=
```

Сначала маленький синтетический JSON-запрос без DEV → синтетическая CQA через
существующий адаптер, schema и scorer → перезапуск backend и сценарий
оценка → evidence → апелляция → новый критерий → переоценка → сохранение после
перезапуска. Успех каждого этапа фиксировать отдельно. Web-console Tunnel с
браузерной авторизацией вместо SSH не используется.

## Точный preflight перед DEV

Скопировать **только созданный каталог tokenizer**, включая
`chat_template.jinja` и `doomsday_serving.json`, в приватный локальный
`artifacts/tokenizers/`; веса остаются на Brev. Для локального подсчёта
использовать transformers **4.51.3**, как на сервере. В существующий
`cqa_eval batch` передать:

```text
--tokenizer artifacts/tokenizers/qwen3-nonthinking
--chat-template-kwargs '{"enable_thinking":false}'
--context-window 32768 --max-output-tokens 4096 --context-margin 512
```

Сначала выполнить `--preflight`, без вызова модели, на **всём** выбранном
наборе. CLI сам `.env` не читает: настройки передаются окружением процесса или
загружаются программно без вывода секретов. Требуется
input tokens + 4096 + 512 ≤ 32768 для каждого диалога. Флаг template kwargs
влияет на подсчёт и manifest; режим inference задаёт серверный шаблон.
Изменение kwargs блокирует `--resume` даже при одинаковом количестве токенов.
После первого разрешённого smoke сверить `usage.input_tokens` с preflight.
Не обрезать диалоги молча. Качество модели измеряется отдельно; техническая
валидация и офлайн-тесты не являются метрикой точности.

## Источники

- [Pinned Docker/CUDA 12.4.1](https://raw.githubusercontent.com/vllm-project/vllm/v0.8.5.post1/docker/Dockerfile),
  [torch](https://raw.githubusercontent.com/vllm-project/vllm/v0.8.5.post1/requirements/cuda.txt),
  [драйвер CUDA 12.4.1](https://docs.nvidia.com/cuda/archive/12.4.1/cuda-toolkit-release-notes/index.html).
- [Qwen3 registry](https://raw.githubusercontent.com/vllm-project/vllm/v0.8.5.post1/vllm/model_executor/models/registry.py),
  [JSON schema и минимум server/request cap](https://raw.githubusercontent.com/vllm-project/vllm/v0.8.5.post1/vllm/entrypoints/openai/protocol.py),
  [generation overrides](https://raw.githubusercontent.com/vllm-project/vllm/v0.8.5.post1/vllm/config.py).
- [Qwen3 non-thinking](https://huggingface.co/Qwen/Qwen3-14B-AWQ),
  [NVIDIA Brev SSH forwarding](https://docs.nvidia.com/brev/cli/connectivity).
