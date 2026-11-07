"""Lossless import validation, independent of SQLite and the evaluator."""

from copy import deepcopy
import unittest

from backend.importing import (ImportConflictError, ImportValidationError,
                               InvalidJSON, canonical, messages,
                               normalize_import, parse_json)


def dialogue():
    return {"conversation_id": "synthetic-import", "channel": "new-channel",
            "language": "new-language", "audio_url": "https://example.invalid/audio",
            "synthetic": True,
            "transcript": [{"timestamp": "2026-11-07T12:00:00Z", "speaker": role,
                            "text": " exact text  "}
                           for role in ("bot", "system", "operator", "client")]}


class ImportTests(unittest.TestCase):
    def test_wrappers_preserve_all_source_contents_and_independence(self):
        source = dialogue()
        for payload in (source, [source], {"isSuccess": True, "data": {"records": [source]}}):
            with self.subTest(payload_type=type(payload).__name__):
                normalized = normalize_import(payload)
                self.assertEqual(normalized, [source])
                normalized[0]["transcript"][0]["text"] = "changed"
                self.assertEqual(source["transcript"][0]["text"], " exact text  ")

    def test_invalid_or_unsuccessful_batch(self):
        invalid = dialogue()
        invalid["transcript"] = []
        for payload in ([dialogue(), invalid], {"isSuccess": False, "data": {"records": []}},
                        {"isSuccess": True, "data": []}, None):
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(ImportValidationError):
                    normalize_import(payload)

    def test_identical_ids_dedupe_but_changed_transcript_conflicts(self):
        source = dialogue()
        reordered = dict(reversed(list(source.items())))
        self.assertEqual(canonical(source), canonical(reordered))
        self.assertEqual(len(normalize_import([source, reordered])), 1)
        changed = deepcopy(source)
        changed["transcript"][0]["text"] += "change"
        with self.assertRaises(ImportConflictError):
            normalize_import([source, changed])

    def test_message_indices_include_all_roles_and_no_iso_inference(self):
        source = dialogue()
        output = messages(source)
        self.assertEqual([m["message_id"] for m in output], ["m001", "m002", "m003", "m004"])
        self.assertEqual([m["source_index"] for m in output], [0, 1, 2, 3])
        self.assertTrue(all(m["audio_offset_seconds"] is None for m in output))
        source["transcript"] *= 250
        source["transcript"][0]["audio_offset_seconds"] = 2.5
        self.assertEqual(messages(source)[-1]["message_id"], "m1000")
        self.assertEqual(messages(source)[0]["audio_offset_seconds"], 2.5)

    def test_invalid_offsets(self):
        for offset in (-1, float("inf"), float("nan"), True, "1"):
            source = dialogue()
            source["transcript"][0]["audio_offset_seconds"] = offset
            with self.assertRaises(ImportValidationError):
                normalize_import(source)

    def test_strict_json_and_no_input_values_in_errors(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}',
                    '{"a":1e999}', '{', b'\xff'):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidJSON):
                    parse_json(raw)
        self.assertEqual(parse_json(b'{"a":1.5}'), {"a": 1.5})


if __name__ == "__main__":
    unittest.main()
