# Prompt v2: применимость и завершение

Версия: `v2-applicability-and-closing`. Исходная версия — коммит `2b8f7c4`,
её текст сохранён в `../prompts/archive/evaluator_system.v1.txt`. Активный текст
находится в `../prompts/evaluator_system.txt` и в идентичном ресурсе пакета
`src/cqa_eval/resources/evaluator_system.txt`. Именно ресурс пакета загружает
`prepare_request`; он дополняется прежним общим суффиксом из evaluator.py.
Metadata сохраняет hash фактически отправленной инструкции. Версия чек-листа
остаётся `1`: бизнес-правила и формула расчёта не менялись.

## Основание изменения

По сообщению пользователя Qwen3-14B-AWQ на NVIDIA Brev вернула 100/100 для
`p1-ru-001`, выбрав yes по 02 и 07. Исходный JSON этого GPU-запуска в локальных
артефактах не найден; нельзя утверждать, какие reason/evidence вернула модель.
Это сообщение о вероятных семантических ошибках, а не размеченный эталон.

Checklist и BUSINESS_RULES требуют сначала определить применимость. Когда
информации изначально достаточно, по выявлению потребности нужен N/A.
Правильный ответ оператора не заменяет требуемое уточнение и не превращает N/A
в yes. Учтены сведения, ранее сообщённые клиентом боту. При нехватке существенной
детали применимый критерий проверяет, уточнил ли её оператор до ответа.
Позднее полученная при уточнении информация не доказывает начальную достаточность.

Для завершения явно раскрыта связка `(итог ИЛИ дальнейшая помощь) И прощание`.
Обычная благодарность/пожелание не заменяет первую часть; итог/помощь не заменяет
прощание. Подтверждение результата может занимать несколько заключительных
реплик; точные слова и единое предложение не требуются. Обрыв нужно подтвердить,
его нельзя выводить только из отсутствия хорошего завершения.

В prompt нет ID диалогов, DEV-цитат, готовых ответов или предварительных
интерпретаций starter_cases. Уточнения привязаны к смыслу текущего критерия,
а не номеру: отредактированный checklist имеет приоритет. Это помогает избежать
обратной ошибки: ставить N/A при любом отсутствии вопросов или no при любом
кратком, но содержательно полном завершении. Схема JSON остаётся прежней.

## Замороженная выборка

Manifest: `calibration_15.json`. Всего 15 чатов, по пять RU/KZ/EN.
Это направленная диагностическая выборка, а не случайная оценка всего DEV;
голосовые разговоры и все возможные виды грубых ошибок она не покрывает.
`selection_focus` — цели отбора, не ответы и не ground truth.

| Группа | RU | KZ | EN | Раздел |
|---|---|---|---|---|
| Навигация по тарифу | p1-ru-001 | p1-kz-001 | p1-en-003 | development |
| Проверка пополнения, уточнения и идентификация | p1-ru-006 | p1-kz-003 | p1-en-006 | development |
| Частная консультация по облигациям | p1-ru-023 | p1-kz-013 | p1-en-013 | development |
| Обрыв до запроса / несколько уточнений / идентификация | p1-ru-025 | p1-kz-009 | p1-en-014 | development; три разные группы |
| Перенос на другие темы | p1-ru-016 | p1-kz-014 | p1-en-011 | reserved |

Резервные KZ и EN относятся к одной семье темы налоговой формы; с RU это
**две семьи**, а не три независимых сценария. Переводные семьи целиком помещены
по одну сторону разделения. Для отбора просмотрены начальные темы разговоров;
полные резервные диалоги и модельные ответы не использовались для правки v2.
Окончательно проверить тематическое разделение должен независимый разметчик.
Пять прежних starter-примеров остались в development. Резерв мал; он не является
доказательством качества на неизвестном GT организаторов. После просмотра его
ошибок при дальнейшей настройке потребуется новая резервная выборка.

## Независимая разметка

Из корня проекта (окружение установлено по CQA_README):

```powershell
& .\.venv\Scripts\python.exe scripts/prepare_calibration.py --out-dir artifacts/cqa/calibration/blank
```

Создаются `review_all.json`, `review_development.json`, `review_reserved.json`.
Готовая пустая общая форма также сохранена в
`codex_context/eval/calibration_15_review_template.json`; копируйте её в локальный
`artifacts/cqa/` перед заполнением, чтобы не публиковать рабочую разметку в Git.
Ответы, причины, доказательства и reviewer пусты. Форма не содержит тем отбора,
предположений, предсказаний или копий исходных реплик. Скрипт сверяет hash DEV,
версию чек-листа, 15 уникальных ID, языки и отсутствие групп по обе стороны
разделения. Существующий каталог не перезаписывается.

Разметчику передать только DEV локально, исходный checklist/BUSINESS_RULES и
`review_all.json`; не передавать результаты Qwen, starter_cases, manifest с
темами или анализ ошибок. Первый человек заполняет все восемь критериев,
второй проверяет, разногласия обсуждаются до просмотра предсказаний. Сохранить
копию как `artifacts/cqa/calibration/human-reviewed.json`.
Только завершённая согласованная запись получает `annotation_status=human_reviewed`
и непустой `reviewer`. Неразрешённые случаи остаются unreviewed; причина может
быть записана отдельным полем записи `review_note`. Это не N/A и не готовый эталон.
Не изменять ID, версии, hash и checklist_snapshot. Для N/A заполнить na_reason;
для no по бинарному критерию — checked_range; для flag=yes — violations.

Последующую ручную проверку evidence предсказаний выполнять отдельно для каждой
версии модели/prompt: `evidence_review` из проверки v1 нельзя автоматически
считать проверкой evidence v2. До такой проверки оставить checked=false.

Разделить заполненную форму без изменения ответов:

```powershell
& .\.venv\Scripts\python.exe scripts/prepare_calibration.py --labels artifacts/cqa/calibration/human-reviewed.json --out-dir artifacts/cqa/calibration/locked
```

Linux/Brev: вместо `& .\.venv\Scripts\python.exe` использовать Python окружения,
в котором установлен компонент, например `python` или `.venv/bin/python`.

## Следующий GPU smoke на Brev

На машине с уже работающим Qwen3-14B-AWQ получить эту ветку обычным командным
процессом Git и установить её ML-пакет через `python -m pip install -e .`.
DEV должен оставаться локальным в `codex_context/data/dev.json`.
Сохранить прежние рабочие LLM_BASE_URL, LLM_MODEL, LLM_API_STYLE и
LLM_STRUCTURED_OUTPUT: точный endpoint и имя модели здесь не предполагаются.
CLI сам не читает `.env`: настройки должны быть экспортированы в процесс.
Не менять модель, AWQ-веса, chat template, режим thinking и параметры сервера
одновременно с prompt. Не добавлять неподтверждённые параметры генерации.

Один запуск с уникальным каталогом, без перезаписи первого результата:

```bash
run_dir="artifacts/cqa/calibration/qwen-v2-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$run_dir"
python -m cqa_eval dry-run --id p1-ru-001 --out "$run_dir/request.json"
# Сначала проверить missing_settings и новый system в request.json.
python -m cqa_eval evaluate --id p1-ru-001 --save "$run_dir/p1-ru-001.json"
python -m cqa_eval validate --id p1-ru-001 --response "$run_dir/p1-ru-001.json"
python -m cqa_eval score --id p1-ru-001 --response "$run_dir/p1-ru-001.json"
```

Это один выбранный диалог, максимум две сетевые попытки при настройке
LLM_MAX_ATTEMPTS=2. Отдельно сохранить версию serving-сервера, точную модель,
параметры chat template/decoding и commit через `git rev-parse HEAD`, без ключей.
Не судить об успехе только по общему баллу: проверить answers/reasons/evidence
по 02 и 07, затем остальные критерии. Предположение об исправлении этих двух
ответов не задаёт заранее итог: он зависит от всех валидированных ответов.

## Контролируемое сравнение v1 и v2

Один прежний live-ответ не является baseline для остальных 14 разговоров.
Для сравнения нужны сохранённые ответы обеих версий на одинаковых ID и одна
зафиксированная независимая разметка. При повторных запусках использовать новые
каталоги; не выбирать лучший из нескольких результатов. Для проверки разброса
при необходимости повторить одинаковое число запусков обеих версий.

Старую версию можно запускать в отдельном checkout с тем же окружением и
тем же сервером, без замены файлов активной версии (Bash/Brev):

```bash
git worktree add --detach ../DOOMSDAY-ml-v1 2b8f7c4
baseline_src="$(cd ../DOOMSDAY-ml-v1/src && pwd)"
PYTHONPATH="$baseline_src" python -m cqa_eval dry-run --id p1-ru-001 --out artifacts/cqa/calibration/v1-request.json
PYTHONPATH="$baseline_src" python -m cqa_eval evaluate --id p1-ru-001 --save artifacts/cqa/calibration/v1/p1-ru-001.json
```

Эта команда выполняется из корня текущего проекта, поэтому использует тот же
локальный DEV. Архив prompt — справочный текст; baseline запускает прежний
код целиком, включая прежний общий суффикс. Проверить различие instruction_hash
у v1/v2 и совпадение dialogue_hash/checklist_hash/model. Входной prompt_hash
зависит также от конкретного диалога; не сравнивать его между разными ID.

После smoke отдельно запустить каждый из 12 development ID обеими версиями.
До раскрытия трёх reserved ответов зафиксировать выбор prompt и правила оценки.
Выбранные ID перечислены в manifest; команда evaluate всегда принимает один ID.
Массовых вызовов этот этап подготовки не делает. Предсказания складывать в
отдельные каталоги `v1/` и `v2/`, по одному файлу `<conversation_id>.json`.

Существующая команда eval compare, PowerShell:

```powershell
$v1 = @(Get-ChildItem artifacts/cqa/calibration/v1/p1-*.json | ForEach-Object FullName)
$v2 = @(Get-ChildItem artifacts/cqa/calibration/v2/p1-*.json | ForEach-Object FullName)
& .\.venv\Scripts\python.exe -m cqa_eval eval compare --labels artifacts/cqa/calibration/locked/review_development.json --predictions $v1 --out artifacts/cqa/calibration/metrics-v1-development.json
& .\.venv\Scripts\python.exe -m cqa_eval eval compare --labels artifacts/cqa/calibration/locked/review_development.json --predictions $v2 --out artifacts/cqa/calibration/metrics-v2-development.json
# Только после фиксации prompt: аналогичные вызовы с review_reserved.json.
```

Bash/Brev:

```bash
python -m cqa_eval eval compare --labels artifacts/cqa/calibration/locked/review_development.json --predictions artifacts/cqa/calibration/v1/p1-*.json --out artifacts/cqa/calibration/metrics-v1-development.json
python -m cqa_eval eval compare --labels artifacts/cqa/calibration/locked/review_development.json --predictions artifacts/cqa/calibration/v2/p1-*.json --out artifacts/cqa/calibration/metrics-v2-development.json
```

Перед сравнением проверить соответствие run-каталога версии prompt: eval compare
валидирует формат, диалог и checklist, но не выбирает для пользователя единую
версию prompt и не проверяет настройки удалённого сервера. Не смешивать v1/v2.

Сравнивать в quality_metrics: agreement["criterion:02"], agreement["criterion:07"],
срезы по языкам, na (особенно applicable_instead_of_na), flags, score_mae вместе
с score_pairs. Также смотреть остальные критерии, coverage, processing_errors,
invalid_labels и reviewed_records. Ошибки и пропуски нельзя скрывать MAE только
успешных пар. При пустой разметке quality_metrics=null — это ожидаемое поведение.

Офлайн-тесты подтверждают целостность конфигурации, формы и расчёта. Они не
доказывают, что Qwen применит новый prompt правильно. Вывод об улучшении делать
только после реального A/B и ручной проверки; публиковать также ухудшения,
малый размер выборки и число независимых сценарных семей.

## Фактическая офлайн-проверка этой версии

В этой итерации `python -m unittest discover -s tests -v` завершился успешно:
39 тестов, включая прежние 34 и пять проверок выборки/формы. `pip check` не
обнаружил проблем. Выполнены генерация 15 пустых записей, dry-run для p1-ru-001
и eval compare с пустой формой: requested=15, reviewed_records=0,
quality_metrics=null. Архив v1 совпадает с исходным коммитом; активная инструкция
попадает в запрос и отличается от v1. Файлы scorer, валидатора, evaluator.py,
quality.py, checklist и BUSINESS_RULES относительно базового коммита не менялись.
Локальные результаты: `artifacts/cqa/calibration/`. Новых вызовов GPU/API в этой
итерации не было. Предыдущий успешный live-запуск Qwen сообщён пользователем;
точность v2 ещё не измерена.
