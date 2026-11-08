"""Application operations and atomic revision checks; no scoring formula here."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from .importing import canonical, messages, normalize_import
from .ml_adapter import AdapterUnavailable, AdapterValidationError
from .store import decode, encode


class APIError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def missing(label):
    raise APIError(404, "NOT_FOUND", f"{label} не найден")


def conflict(message="Версия изменилась. Перечитайте данные и повторите действие."):
    raise APIError(409, "CONFLICT", message)


def invalid(message):
    raise APIError(422, "VALIDATION_ERROR", message)


def result_status(state):
    return "needs_review" if any(r["status"] == "needs_review" for r in state["assessment"]["criteria"]) else "completed"


def evaluation_summary(document):
    final = document["final"]
    return {**{key: document[key] for key in ("evaluation_id", "checklist_version", "status", "created_at", "revision")},
            "total_score": final["calculation"]["total_score"] if final else None,
            "critical_error": final["critical_error"] if final else None}


class Service:
    def __init__(self, store, adapter):
        self.store, self.adapter = store, adapter

    def import_official_results(self, payload, dataset="dev"):
        from .result_importing import import_official_results
        return import_official_results(self, payload, dataset)

    def import_conversations(self, payload):
        records = normalize_import(payload)
        imported = skipped = 0
        with self.store.transaction() as db:
            for record in records:
                normalized = canonical(record)
                old = db.execute("SELECT canonical FROM conversations WHERE id=?", (record["conversation_id"],)).fetchone()
                if old:
                    if old["canonical"] != normalized:
                        conflict("Диалог с таким ID уже существует с другим содержимым. Пакет не импортирован.")
                    skipped += 1
                else:
                    db.execute("INSERT INTO conversations VALUES (?,?,?)", (record["conversation_id"], encode(record), normalized))
                    imported += 1
        return {"imported": imported, "skipped": skipped,
                "conversation_ids": [r["conversation_id"] for r in records]}

    def checklist(self, version=None, db=None):
        if db is None:
            with self.store.transaction(write=False) as conn:
                return self.checklist(version, conn)
        row = db.execute("SELECT content FROM checklists ORDER BY sequence DESC LIMIT 1").fetchone() if version is None else db.execute(
            "SELECT content FROM checklists WHERE version=?", (version,)).fetchone()
        if row is None:
            missing("Чек-лист")
        return decode(row["content"])

    def seed_checklist(self, checklist):
        self.adapter.validate_checklist(checklist)
        with self.store.transaction() as db:
            if not db.execute("SELECT 1 FROM checklists LIMIT 1").fetchone():
                db.execute("INSERT INTO checklists(version,content) VALUES (?,?)", (checklist["version"], encode(checklist)))

    def checklists(self):
        with self.store.transaction(write=False) as db:
            documents = [decode(r["content"]) for r in db.execute("SELECT content FROM checklists ORDER BY sequence DESC")]
        return {"items": [{k: d[k] for k in ("checklist_id", "version", "title")} for d in documents],
                "current_version": documents[0]["version"]}

    def save_checklist(self, base_version, checklist):
        self.adapter.validate_checklist(checklist)
        with self.store.transaction() as db:
            current = self.checklist(db=db)
            if base_version != current["version"] or checklist["version"] != base_version:
                conflict("Чек-лист уже изменён. Перечитайте текущую версию формы.")
            if checklist["checklist_id"] != current["checklist_id"]:
                invalid("checklist_id должен совпадать с текущим чек-листом")
            # Canonical initial versions are decimal strings; also tolerate custom seeds.
            versions = [r["version"] for r in db.execute("SELECT version FROM checklists")]
            next_version = str(max([int(v) for v in versions if v.isdecimal()] + [0]) + 1)
            document = deepcopy(checklist)
            document["version"] = next_version
            db.execute("INSERT INTO checklists(version,content) VALUES (?,?)", (next_version, encode(document)))
        return document

    def conversations(self, *, language=None, channel=None, status=None, critical_error=None, limit=100, offset=0):
        items = []
        with self.store.transaction(write=False) as db:
            for row in db.execute("SELECT content FROM conversations ORDER BY id"):
                record = decode(row["content"])
                evaluation = db.execute("SELECT content FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                        (record["conversation_id"],)).fetchone()
                latest = evaluation_summary(decode(evaluation["content"])) if evaluation else None
                if language is not None and record["language"] != language:
                    continue
                if channel is not None and record["channel"] != channel:
                    continue
                if status is not None and (latest["status"] if latest else "not_evaluated") != status:
                    continue
                flag = latest["critical_error"] if latest else None
                if critical_error is not None and critical_error != ("unknown" if flag is None else str(flag).lower()):
                    continue
                open_appeals = db.execute("SELECT 1 FROM appeals a JOIN evaluations e ON e.id=a.evaluation_id WHERE e.conversation_id=? AND a.status='open' LIMIT 1",
                                          (record["conversation_id"],)).fetchone() is not None
                items.append({"conversation_id": record["conversation_id"], "channel": record["channel"],
                              "language": record["language"], "message_count": len(record["transcript"]),
                              "has_audio": bool(record.get("audio_url")), "synthetic": record.get("synthetic", False),
                              "has_open_appeals": open_appeals, "latest_evaluation": latest})
        return {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}

    def conversation(self, conversation_id):
        with self.store.transaction(write=False) as db:
            row = db.execute("SELECT content FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if row is None:
                missing("Диалог")
            record = decode(row["content"])
            history = [evaluation_summary(decode(r["content"])) for r in db.execute(
                "SELECT content FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC", (conversation_id,))]
        return {**{k: record[k] for k in ("conversation_id", "channel", "language")}, "audio_url": record.get("audio_url"),
                "synthetic": record.get("synthetic", False), "messages": messages(record),
                "latest_evaluation_id": history[0]["evaluation_id"] if history else None, "evaluation_history": history}

    def evaluation(self, evaluation_id):
        with self.store.transaction(write=False) as db:
            document = self.store.evaluation(db, evaluation_id)
        if document is None:
            missing("Оценка")
        return document

    def queue_evaluation(self, conversation_id, version):
        with self.store.transaction() as db:
            row = db.execute("SELECT content FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if row is None:
                missing("Диалог")
            checklist = self.checklist(version, db)
            if db.execute("SELECT 1 FROM evaluations WHERE conversation_id=? AND checklist_version=? AND status IN ('queued','running')",
                          (conversation_id, version)).fetchone():
                conflict("Оценка этого диалога по выбранной версии уже выполняется.")
            ready, message = self.adapter.availability()
            if not ready:
                raise APIError(503, "EVALUATOR_UNAVAILABLE", message or "Оценщик пока не подключён")
            dialogue = decode(row["content"])
            document = {"evaluation_id": str(uuid4()), "conversation_id": conversation_id, "checklist_version": version,
                        "created_at": now(), "status": "queued", "revision": 0, "synthetic": dialogue.get("synthetic", False),
                        "checklist_snapshot": checklist, "original": None, "final": None, "metadata": None, "error": None, "appeals": []}
            db.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)", (document["evaluation_id"], conversation_id,
                       version, "queued", document["created_at"], encode(document), encode(dialogue)))
        return document

    def run_evaluation(self, evaluation_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT content,dialogue FROM evaluations WHERE id=? AND status='queued'", (evaluation_id,)).fetchone()
            if row is None:
                return
            document, dialogue = decode(row["content"]), decode(row["dialogue"])
            document["status"] = "running"
            self.store.update_evaluation(db, document)
        try:
            result = self.adapter.evaluate(dialogue, document["checklist_snapshot"])
            # Revalidate even an injected evaluator; trust only the shared scorer.
            state = self.adapter.result_state(dialogue, document["checklist_snapshot"], result["assessment"])
            document.update(status=result_status(state), original=deepcopy(state), final=state,
                            revision=1, metadata=result.get("metadata"), error=None)
        except Exception as exc:
            from .ml_adapter import AdapterEvaluationError
            safe = isinstance(exc, (AdapterEvaluationError, AdapterUnavailable, AdapterValidationError))
            document.update(status="failed", original=None, final=None,
                            error={"code": exc.code if safe else "EVALUATION_FAILED",
                                   "message": exc.message if safe else "Не удалось выполнить или проверить оценку.", "details": None},
                            metadata=getattr(exc, "metadata", None) if safe else None)
        with self.store.transaction() as db:
            self.store.update_evaluation(db, document)

    def appeals(self, status="open"):
        with self.store.transaction(write=False) as db:
            query = "SELECT content FROM appeals" + (" WHERE status=?" if status != "all" else "") + " ORDER BY created_at DESC, rowid DESC"
            items = [decode(r["content"]) for r in db.execute(query, (status,) if status != "all" else ())]
        return {"items": items, "total": len(items)}

    def create_appeal(self, evaluation_id, criterion_id, comment, expected_revision):
        with self.store.transaction() as db:
            document = self.store.evaluation(db, evaluation_id)
            if document is None:
                missing("Оценка")
            if document.get("import_source", {}).get("read_only"):
                conflict("Импортированный официальный результат доступен только для чтения: исходный полный ответ модели отсутствует.")
            if document["revision"] != expected_revision:
                conflict()
            if document["final"] is None:
                conflict("У оценки ещё нет результата для апелляции.")
            before = next((r for r in document["final"]["assessment"]["criteria"] if r["criterion_id"] == criterion_id), None)
            if before is None:
                invalid("Критерий отсутствует в снимке оценки")
            if db.execute("SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'", (evaluation_id, criterion_id)).fetchone():
                conflict("По этому критерию уже есть открытая апелляция.")
            appeal = {"appeal_id": str(uuid4()), "evaluation_id": evaluation_id, "criterion_id": criterion_id,
                      "comment": comment, "created_at": now(), "status": "open", "based_on_revision": expected_revision,
                      "before": before, "resolution": None}
            db.execute("INSERT INTO appeals VALUES (?,?,?,?,?,?)", (appeal["appeal_id"], evaluation_id, criterion_id,
                       "open", appeal["created_at"], encode(appeal)))
        return appeal

    def resolve_appeal(self, appeal_id, decision, reason, replacement, expected_revision):
        with self.store.transaction() as db:
            row = db.execute("SELECT content FROM appeals WHERE id=?", (appeal_id,)).fetchone()
            if row is None:
                missing("Апелляция")
            appeal = decode(row["content"])
            document = self.store.evaluation(db, appeal["evaluation_id"])
            if appeal["status"] != "open":
                conflict("Апелляция уже рассмотрена.")
            if document["revision"] != expected_revision:
                conflict()
            assessment = deepcopy(document["final"]["assessment"])
            if decision == "change":
                if not isinstance(replacement, dict) or replacement.get("criterion_id") != appeal["criterion_id"]:
                    invalid("replacement должен содержать полную строку с исходным criterion_id")
                assessment["criteria"] = [replacement if r["criterion_id"] == appeal["criterion_id"] else r for r in assessment["criteria"]]
            elif replacement is not None:
                invalid("Для confirm replacement должен быть null")
            dialogue = decode(db.execute("SELECT dialogue FROM evaluations WHERE id=?", (document["evaluation_id"],)).fetchone()["dialogue"])
            state = self.adapter.result_state(dialogue, document["checklist_snapshot"], assessment)
            document.update(final=state, status=result_status(state), revision=document["revision"] + 1)
            appeal.update(status="resolved", resolution={"decision": decision, "reason": reason, "replacement": replacement,
                          "resolved_at": now(), "resulting_revision": document["revision"]})
            db.execute("UPDATE appeals SET status='resolved',content=? WHERE id=?", (encode(appeal), appeal_id))
            self.store.update_evaluation(db, document)
            document = self.store.evaluation(db, document["evaluation_id"])
        return {"appeal": appeal, "evaluation": document}
