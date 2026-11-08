"""Offline, atomic import of official results into existing evaluation history.

The official format is lossy. Its presentation assessment is NOT a reconstructed
raw model response: missing checked ranges and violation types stay absent.
Imported evaluations are read-only, and retain the exact official source row.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
from uuid import NAMESPACE_URL, uuid5

from cqa_eval.data import InputError, fingerprint
from cqa_eval.submission import CRITERION_CODES, OFFICIAL_CHECKLIST_HASH, validate_submission
from .importing import messages
from .service import conflict, now
from .store import decode, encode


def presentation_state(result, dialogue, checklist):
    """Map already-validated official values/evidence to the existing UI contract."""
    source = {row["criterion_id"]: row for row in result["criteria"]}
    transcript = messages(dialogue)
    rows, points, sections = [], [], {}
    applicable = sum(Decimal(str(c["weight"])) for c in checklist["criteria"]
                     if c["kind"] != "flag" and source[CRITERION_CODES[c["id"]]]["value"] is not None)
    with localcontext() as context:
        context.prec = 50
        for criterion in checklist["criteria"]:
            official = source[CRITERION_CODES[criterion["id"]]]
            value = official["value"]
            answer = "na" if value is None else ("yes" if value else "no") if type(value) is bool else value
            rows.append({"criterion_id": criterion["id"], "status": "evaluated", "answer": answer,
                         "reason": official["rationale"],
                         "evidence_message_ids": [transcript[turn - 1]["message_id"] for turn in official["evidence_turns"]],
                         "checked_range": None, "na_reason": None, "uncertainty": None, "violations": []})
            base = None if criterion["kind"] == "flag" else Decimal(str(criterion["weight"]))
            effective = None if base is None else Decimal(0) if value is None else 100 * base / applicable
            fraction = None if base is None else Decimal(0) if value is None else (
                Decimal(int(value)) / Decimal(str(criterion["scale_max"])) if criterion["kind"] == "scale"
                else Decimal(int(value)))
            earned = None if effective is None else effective * fraction
            points.append({"criterion_id": criterion["id"], "base_weight": float(base) if base is not None else None,
                           "effective_weight": float(effective) if effective is not None else None,
                           "earned_points": float(earned) if earned is not None else None})
            if base is not None:
                section = sections.setdefault(criterion["section_id"], {"earned_points": Decimal(0), "possible_points": Decimal(0)})
                section["earned_points"] += earned
                section["possible_points"] += effective
    return {"assessment": {"conversation_id": result["conversation_id"], "checklist_version": checklist["version"], "criteria": rows},
            "calculation": {"total_score": result["score"]["final_score"], "applicable_weight": float(applicable),
                            "no_applicable_criteria": False, "rows": points,
                            "sections": [{"section_id": key, **{name: float(value) for name, value in totals.items()}}
                                         for key, totals in sections.items()]},
            "critical_error": result["score"]["critical_flag"]}


def import_official_results(service, payload, dataset="dev"):
    imported = skipped = 0
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise InputError("Expected an official results object")
    source_ids = {row["conversation_id"] for row in payload["results"]
                  if isinstance(row, dict) and isinstance(row.get("conversation_id"), str)}
    with service.store.transaction() as db:
        records = [decode(row["content"]) for row in db.execute("SELECT id,content FROM conversations ORDER BY id")
                   if row["id"] in source_ids]
        # Validate exact source coverage, types, evidence indices and arithmetic
        # before inserting any result. Real transcripts must already be imported.
        validate_submission(payload, records, dataset)
        if any(record.get("synthetic") for record in records):
            conflict("Для официальных результатов требуется исходный реальный набор диалогов.")
        checklist = service.checklist("1", db)
        if fingerprint(checklist) != OFFICIAL_CHECKLIST_HASH:
            conflict("Импорт требует неизменённый официальный чек-лист версии 1.")
        by_id = {record["conversation_id"]: record for record in records}
        for result in payload["results"]:
            conversation_id = result["conversation_id"]
            dialogue = by_id[conversation_id]
            identity = fingerprint({"result": result, "model": payload["model"], "team_name": payload["team_name"],
                                    "schema_version": payload["schema_version"], "dialogue": dialogue,
                                    "checklist_hash": OFFICIAL_CHECKLIST_HASH})
            evaluation_id = str(uuid5(NAMESPACE_URL, f"doomsday:official:{dataset}:{conversation_id}"))
            old = service.store.evaluation(db, evaluation_id)
            if old is not None:
                if old.get("import_source", {}).get("identity") != identity:
                    conflict("Для диалога уже импортирован другой результат. Существующая оценка не изменена.")
                skipped += 1
                continue
            if db.execute("SELECT 1 FROM evaluations WHERE conversation_id=? LIMIT 1", (conversation_id,)).fetchone():
                conflict("У диалога уже есть оценка. Импорт не заменяет и не скрывает существующую историю.")
            state = presentation_state(result, dialogue, checklist) if result["status"] == "ok" else None
            document = {"evaluation_id": evaluation_id, "conversation_id": conversation_id,
                        "checklist_version": checklist["version"], "created_at": now(),
                        "status": "completed" if state is not None else "failed", "revision": 1,
                        "synthetic": False, "checklist_snapshot": deepcopy(checklist),
                        "original": deepcopy(state), "final": state, "appeals": [],
                        "error": None if state is not None else {"code": "IMPORTED_PROCESSING_ERROR", "message": result["error"], "details": None},
                        "metadata": {"model": payload["model"]["name"], "cache_hit": False,
                                     "api_calls": None, "retry_count": None, "latency_ms": None, "usage": None,
                                     "estimated_cost_usd": None, "cost_note": "Импорт сохранённого результата; новых вызовов модели нет. Исходные usage и стоимость неизвестны."},
                        "import_source": {"kind": "official_submission", "schema_version": payload["schema_version"],
                                          "dataset": dataset, "identity": identity, "read_only": True,
                                          "raw_assessment_available": False, "model": deepcopy(payload["model"]),
                                          "team_name": payload["team_name"], "result": deepcopy(result)}}
            db.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)", (evaluation_id, conversation_id,
                       checklist["version"], document["status"], document["created_at"], encode(document), encode(dialogue)))
            imported += 1
    return {"imported": imported, "skipped": skipped}
