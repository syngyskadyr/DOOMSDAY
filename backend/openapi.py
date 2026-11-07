"""JSON request documentation for the manually validated HTTP boundary.

These schemas only describe Swagger's request editor. Runtime validation stays
in the importer, services, and shared ML validator.
"""


def object_schema(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def request_body(schema, description):
    return {"requestBody": {"required": True, "description": description,
                            "content": {"application/json": {"schema": schema}}}}


STRING = {"type": "string", "minLength": 1}
REVISION = {"type": "integer", "minimum": 0}
MESSAGE = {"type": "object", "required": ["timestamp", "speaker", "text"],
           "properties": {key: {"type": "string"} for key in ("timestamp", "speaker", "text")}}
RECORD = {"type": "object", "required": ["conversation_id", "channel", "language", "transcript"],
          "properties": {"conversation_id": STRING, "channel": STRING, "language": STRING,
                         "transcript": {"type": "array", "minItems": 1, "items": MESSAGE},
                         "audio_url": {"type": ["string", "null"]}, "synthetic": {"type": "boolean"}},
          "example": {"conversation_id": "synthetic-http-import", "channel": "chat", "language": "RU",
                      "synthetic": True, "transcript": [
                          {"timestamp": "00:00", "speaker": "client", "text": "Синтетический вопрос."},
                          {"timestamp": "00:01", "speaker": "operator", "text": "Синтетический ответ."}]}}
RECORDS = {"type": "array", "items": RECORD}
IMPORT = request_body({"oneOf": [RECORD, RECORDS, {
    "type": "object", "required": ["isSuccess", "data"],
    "properties": {"isSuccess": {"const": True}, "data": {
        "type": "object", "required": ["records"], "properties": {"records": RECORDS}}}}]},
    "Исходная выгрузка организаторов, массив диалогов или один диалог. Импорт не вызывает модель.")
CHECKLIST = request_body(object_schema({"base_version": STRING, "checklist": {
    "type": "object", "required": ["schema_version", "checklist_id", "version", "title", "sections", "criteria"]}}),
    "Передайте полный изменённый объект GET /api/checklists/current с прежней version и base_version.")
EVALUATION = request_body(object_schema({"checklist_version": {**STRING, "example": "1"}}),
    "Снимок выбранной версии закрепляется за фоновой задачей. Ненастроенная модель даёт 503.")
APPEAL = request_body(object_schema({"criterion_id": {**STRING, "example": "07"},
                                   "comment": STRING, "expected_revision": REVISION}),
    "Критерий берётся из checklist_snapshot оценки; expected_revision — из последнего GET оценки.")
RESOLUTION = request_body(object_schema({"decision": {"type": "string", "enum": ["confirm", "change"]},
                                       "reason": STRING,
                                       "replacement": {"type": ["object", "null"]},
                                       "expected_revision": REVISION}),
    "confirm требует replacement=null; change требует полную CriterionResult с тем же criterion_id, основаниями и доказательствами.")
