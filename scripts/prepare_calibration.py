"""Build a blind annotation form from local DEV, or split a filled form.

No model calls, predictions, or starter hypotheses are loaded. Output stays local.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

from cqa_eval.data import InputError, dumps, fingerprint, load_dev, read_json
from cqa_eval.quality import prepare_review


def prepare(dev, checklist, manifest):
    content = Path(dev).read_bytes()
    if hashlib.sha256(content).hexdigest() != manifest["dev_sha256"]:
        raise InputError("DEV hash differs from the frozen selection")
    records = load_dev(dev)
    selection = manifest["cases"]
    ids = [case["conversation_id"] for case in selection]
    if len(ids) != 15 or len(set(ids)) != 15:
        raise InputError("Calibration requires exactly 15 unique conversations")
    if checklist["version"] != manifest["checklist_version"]:
        raise InputError("Checklist version differs from the frozen selection")
    if Counter(c["language"] for c in selection) != {"RU": 5, "KZ": 5, "EN": 5}:
        raise InputError("Selection must contain 5 RU, 5 KZ and 5 EN records")
    if Counter(c["split"] for c in selection) != {"development": 12, "reserved": 3}:
        raise InputError("Selection must contain 12 development and 3 reserved records")
    groups = {}
    for case in selection:
        split = groups.setdefault(case["group"], case["split"])
        if split != case["split"]:
            raise InputError("Related scenario group crosses the split boundary")
    form = prepare_review(records, checklist, ids)
    form["selection_hash"] = fingerprint(manifest)
    form["purpose"] = "Independent human annotation of 15 DEV records; no model answers or selection hypotheses"
    for row, selected in zip(form["cases"], selection):
        if row["language"] != selected["language"]:
            raise InputError("Selected language differs from DEV")
        row["split"] = selected["split"]
        # Topic/expected coverage remains in the maintainer manifest, not the blind form.
    return form


def split_form(form, manifest):
    expected = {c["conversation_id"]: c["split"] for c in manifest["cases"]}
    ids = [c["conversation_id"] for c in form["cases"]]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise InputError("Filled form must retain all 15 unique selected IDs")
    if form.get("selection_hash") != fingerprint(manifest):
        raise InputError("Filled form belongs to a different selection")
    if form.get("checklist_version") != manifest["checklist_version"]:
        raise InputError("Filled form checklist version differs")
    outputs = {}
    for name in ("development", "reserved"):
        subset = deepcopy(form)
        subset["cases"] = [r for r in subset["cases"] if expected[r["conversation_id"]] == name]
        for row in subset["cases"]:
            row["split"] = name
        outputs[name] = subset
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("codex_context/eval/calibration_15.json"))
    parser.add_argument("--dev", type=Path, default=Path("codex_context/data/dev.json"))
    parser.add_argument("--checklist", type=Path, default=Path("codex_context/checklist.json"))
    parser.add_argument("--labels", type=Path, help="Split a completed/partially completed form without changing labels")
    parser.add_argument("--out-dir", type=Path, required=True, help="New directory; existing files are never overwritten")
    args = parser.parse_args(argv)
    manifest = read_json(args.manifest)
    form = read_json(args.labels) if args.labels else prepare(args.dev, read_json(args.checklist), manifest)
    outputs = split_form(form, manifest)
    outputs["all"] = form
    if args.out_dir.exists():
        parser.error("Output directory already exists; choose a new directory to preserve annotations")
    args.out_dir.mkdir(parents=True)
    for name, value in outputs.items():
        (args.out_dir / ("review_" + name + ".json")).write_text(dumps(value, indent=2) + "\n", encoding="utf-8")
    print(dumps({"records": len(form["cases"]), "output": str(args.out_dir),
                 "note": "No predictions generated; compare validates reviewed labels separately"}))


if __name__ == "__main__":
    main()
