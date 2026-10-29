import csv
import hashlib
import json

import pytest

from harness import report


SPLITS = ("development", "validation", "diagnostic", "regression")
EXPERIMENTS = [
    f"D{direction:02}.{variant:02}"
    for direction in range(1, 11) for variant in range(1, 11)
]
IDS = ["BASE", *EXPERIMENTS]
OUTPUTS = ("all-100-results.json", "all-100-results.csv", "report.md")
COUNTS = {"development": 2, "validation": 2, "diagnostic": 3, "regression": 4}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind_selection(directory):
    receipt_path = directory / "selection-before-validation.json"
    receipt = read_json(receipt_path)
    receipt["development_sha256"] = digest(directory / "summary-development.json")
    write_json(receipt_path, receipt)
    validation_path = directory / "summary-validation.json"
    validation = read_json(validation_path)
    validation["manifest"]["selection_sha256"] = digest(receipt_path)
    write_json(validation_path, validation)


@pytest.fixture
def study(tmp_path):
    for split in SPLITS:
        query_ids = [f"{split}-q{index}" for index in range(COUNTS[split])]
        manifest = {
            "expected_queries": len(query_ids),
            "expected_query_ids": query_ids,
            "code_sha256": "a" * 64,
            "data_sha256": ("d" if split == "diagnostic" else "b") * 64,
            "baseline": "synthetic-baseline",
            "experiments": EXPERIMENTS,
        }
        table = []
        rows = {}
        for identifier in IDS:
            table.append({
                "id": identifier, "name": f"Synthetic {identifier}",
                "questions": len(query_ids), "ok": len(query_ids), "errors": 0,
                "evidence_score": 50.0, "recall10": 0.5, "recall100": 0.5,
                "all100": 0.0, "source_coverage100": 0.5,
                "improved": 0, "regressed": 0, "triggered": 0,
                "p95_ms": 1.0, "mean_characters": 50,
                "forbidden_hits": 0, "privacy_forbidden_hits": 0,
                "correct_abstentions": 0,
            })
            rows[identifier] = [
                {"id": query_id, "split": split, "status": "ok",
                 "answerable": True, "dataset": "synthetic", "category": "lookup",
                 "recall20": 0.5}
                for query_id in query_ids
            ]
        write_json(tmp_path / f"summary-{split}.json",
                   {"manifest": manifest, "fatals": [], "table": table})
        (tmp_path / f"results-{split}.jsonl").write_text(
            json.dumps({"conversation": f"{split}-conversation", "split": split, "rows": rows}) + "\n",
            encoding="utf-8")
    write_json(tmp_path / "selection-before-validation.json", {
        "selected": [{"direction": f"D{direction:02}", "id": f"D{direction:02}.10"}
                     for direction in range(1, 11)],
        "original_nominees": [f"D{direction:02}.10" for direction in range(1, 11)],
        "original_selection_sha256": "c" * 64,
        "original_selected_at": "2026-10-29T00:00:00+00:00",
        "validation_previously_seen": True,
        "preservation": {"validation_seen_firstpass": True, "retuned": False},
    })
    bind_selection(tmp_path)
    return tmp_path


def assert_rejected(directory, message):
    with pytest.raises(ValueError, match=message):
        report.create(directory)
    assert not any((directory / name).exists() for name in OUTPUTS)


def test_valid_study_keeps_all_experiments_original_nominees_and_caveats(study, capsys):
    receipt_path = study / "selection-before-validation.json"
    receipt_before = receipt_path.read_bytes()
    report.create(study)
    output = read_json(study / "all-100-results.json")
    with (study / "all-100-results.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert [row["id"] for row in output] == IDS
    assert [row["id"] for row in csv_rows] == IDS
    expected_selected = {f"D{direction:02}.10" for direction in range(1, 11)}
    assert {row["id"] for row in output if row["selected_before_validation"]} == expected_selected
    assert all(row["bootstrap_95_delta"] == ([0.0, 0.0] if row["id"] in expected_selected else None)
               for row in output)
    assert all(row["official_aml_score"] is None and row["qa_accuracy"] is None for row in output)
    assert receipt_path.read_bytes() == receipt_before
    markdown = (study / "report.md").read_text()
    full_table = markdown.split("## 全部100组\n", 1)[1].split("## 解读限制", 1)[0]
    assert [line.split("|")[1] for line in full_table.splitlines() if line.startswith("|D")] == EXPERIMENTS
    for caveat in ("首轮因评测检查不完整而无效", "v2 重跑", "验证集在首轮已被查看",
                   "不是新的独立留出验证", "看到结果后未调整算法", "不按本次开发或验证分数重新选择"):
        assert caveat in markdown
    assert json.loads(capsys.readouterr().out)["rows"] == 100


def test_fresh_study_does_not_claim_a_failed_first_pass(study):
    receipt_path = study / "selection-before-validation.json"
    receipt = read_json(receipt_path)
    receipt.pop("validation_previously_seen")
    write_json(receipt_path, receipt)
    bind_selection(study)
    report.create(study)
    markdown = (study / "report.md").read_text()
    assert "首轮因评测检查不完整而无效" not in markdown
    assert "预选项由开发集结果固定" in markdown


def test_bootstrap_is_independent_of_parallel_completion_order():
    rows = {
        "a": {"status": "ok", "answerable": True, "dataset": "a", "category": "b", "recall20": 1},
        "b": {"status": "ok", "answerable": True, "dataset": "a", "category": "b", "recall20": .2},
        "c": {"status": "ok", "answerable": True, "dataset": "b", "category": "a", "recall20": .7},
    }
    baseline = {key: {**row, "recall20": .5} for key, row in rows.items()}
    assert report.bootstrap_delta(rows, baseline) == report.bootstrap_delta(
        dict(reversed(list(rows.items()))), baseline)


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("fatals", [[{"fatal": "synthetic failure"}], None, 0, False, {}])
def test_summary_fatals_fail_closed(study, split, fatals):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    summary["fatals"] = fatals
    write_json(path, summary)
    assert_rejected(study, "fatals")


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("identifier", ["BASE", "D01.01"])
@pytest.mark.parametrize("field,value", [
    ("questions", 1), ("ok", 1), ("errors", 1), ("questions", None),
    ("ok", True), ("errors", False), ("errors", "0"), ("questions", 2.0),
])
def test_summary_counts_must_be_exact_integers(study, split, identifier, field, value):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    next(row for row in summary["table"] if row["id"] == identifier)[field] = value
    write_json(path, summary)
    assert_rejected(study, f"{identifier} {field}")


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("change", ["missing_base", "missing_experiment", "duplicate", "unknown", "extra"])
def test_summary_requires_exactly_the_101_ids(study, split, change):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    if change.startswith("missing"):
        summary["table"].pop(0 if change == "missing_base" else -1)
    elif change == "extra":
        summary["table"].append({**summary["table"][-1], "id": "D11.01"})
    else:
        summary["table"][-1]["id"] = "BASE" if change == "duplicate" else "D11.01"
    write_json(path, summary)
    assert_rejected(study, "table")


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("field", [
    "expected_queries", "expected_query_ids", "code_sha256", "data_sha256",
    "baseline", "experiments",
])
def test_required_manifest_fields_cannot_be_missing(study, split, field):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    del summary["manifest"][field]
    write_json(path, summary)
    assert_rejected(study, field)


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("change", [
    "count", "zero", "boolean", "duplicate_query", "missing_query",
    "unexpected_query", "not_list", "duplicate_experiment", "missing_experiment", "unknown_experiment",
])
def test_manifest_ids_and_expected_count_are_checked(study, split, change):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    manifest = summary["manifest"]
    if change in ("count", "zero", "boolean"):
        manifest["expected_queries"] = {"count": 999, "zero": 0, "boolean": True}[change]
    elif change == "duplicate_query":
        manifest["expected_query_ids"][-1] = manifest["expected_query_ids"][0]
    elif change == "missing_query":
        manifest["expected_query_ids"].pop()
    elif change == "unexpected_query":
        manifest["expected_query_ids"][-1] = "unseen-synthetic-query"
    elif change == "not_list":
        manifest["expected_query_ids"] = "not-a-list"
    elif change == "duplicate_experiment":
        manifest["experiments"][-1] = manifest["experiments"][0]
    elif change == "missing_experiment":
        manifest["experiments"].pop()
    else:
        manifest["experiments"][-1] = "D11.01"
    write_json(path, summary)
    bind_selection(study)
    assert_rejected(study, "expected|experiment|query")


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("field", ["code_sha256", "baseline"])
def test_cross_split_core_hash_and_baseline_must_match(study, split, field):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    summary["manifest"][field] = "e" * 64 if field == "code_sha256" else "another-baseline"
    write_json(path, summary)
    assert_rejected(study, field)


@pytest.mark.parametrize("split", SPLITS)
def test_public_data_hash_matches_and_diagnostic_is_separate(study, split):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    summary["manifest"]["data_sha256"] = ("b" if split == "diagnostic" else "e") * 64
    write_json(path, summary)
    assert_rejected(study, "data_sha256")


@pytest.mark.parametrize("field", ["code_sha256", "data_sha256"])
@pytest.mark.parametrize("value", [None, "", "z" * 64, "a" * 63])
def test_invalid_hashes_fail_even_when_all_splits_agree(study, field, value):
    for split in SPLITS:
        path = study / f"summary-{split}.json"
        summary = read_json(path)
        summary["manifest"][field] = value
        write_json(path, summary)
    assert_rejected(study, field)


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("identifier", ["BASE", "D01.01"])
@pytest.mark.parametrize("change", [
    "error", "missing_query", "duplicate_query", "unknown_query",
    "missing_experiment", "wrong_query_split",
])
def test_raw_rows_cannot_hide_errors_or_wrong_coverage(study, split, identifier, change):
    path = study / f"results-{split}.jsonl"
    record = read_json(path)
    values = record["rows"][identifier]
    if change == "error":
        values[0]["status"] = "error"
    elif change == "missing_query":
        values.pop()
    elif change == "duplicate_query":
        values[-1]["id"] = values[0]["id"]
    elif change == "unknown_query":
        values[-1]["id"] = "unexpected-synthetic-query"
    elif change == "missing_experiment":
        del record["rows"][identifier]
    else:
        values[0]["split"] = "another-split"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert_rejected(study, "row|query|experiment")


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("change", [
    "fatal", "wrong_split", "duplicate_queries_across_conversations", "missing_rows",
    "unknown_experiment", "missing_conversation", "empty_rows", "empty_file",
])
def test_raw_record_integrity_is_checked_before_writing(study, split, change):
    path = study / f"results-{split}.jsonl"
    record = read_json(path)
    if change == "fatal":
        record["fatal"] = "synthetic fatal"
    elif change == "wrong_split":
        record["split"] = "wrong-split"
    elif change == "missing_rows":
        del record["rows"]
    elif change == "unknown_experiment":
        record["rows"]["D11.01"] = record["rows"]["D01.01"]
    elif change == "missing_conversation":
        del record["conversation"]
    elif change == "empty_rows":
        record["rows"] = {identifier: [] for identifier in IDS}
    text = json.dumps(record) + "\n"
    if change == "duplicate_queries_across_conversations":
        record["conversation"] = "another-conversation"
        text += json.dumps(record) + "\n"
    elif change == "empty_file":
        text = ""
    path.write_text(text, encoding="utf-8")
    assert_rejected(study, "fatal|split|duplicate|rows|experiment|conversation")


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("earlier_status", ["ok", "fatal", "error"])
def test_resumed_runs_use_latest_complete_record_per_conversation(study, split, earlier_status):
    path = study / f"results-{split}.jsonl"
    complete = path.read_text()
    earlier = json.loads(complete)
    if earlier_status == "fatal":
        earlier["fatal"] = "earlier synthetic failure"
        del earlier["rows"]
    elif earlier_status == "error":
        earlier["rows"]["D01.01"][0]["status"] = "error"
    path.write_text(json.dumps(earlier) + "\n" + complete, encoding="utf-8")
    summaries, manifests, nominees, rows = report.validate_inputs(study)
    assert set(summaries) == set(SPLITS)
    assert set(rows) == set(IDS)
    assert len(rows["BASE"]) == COUNTS["validation"]


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("latest_status", ["fatal", "error", "incomplete"])
def test_resume_never_falls_back_to_earlier_success(study, split, latest_status):
    path = study / f"results-{split}.jsonl"
    complete = path.read_text()
    latest = json.loads(complete)
    if latest_status == "fatal":
        latest["fatal"] = "latest synthetic failure"
    elif latest_status == "error":
        latest["rows"]["D01.01"][0]["status"] = "error"
    else:
        latest["rows"]["D01.01"].pop()
    path.write_text(complete + json.dumps(latest) + "\n", encoding="utf-8")
    assert_rejected(study, "fatal|error|query")


def test_multiple_conversations_and_unordered_inputs_are_valid(study):
    for split in SPLITS:
        path = study / f"results-{split}.jsonl"
        record = read_json(path)
        records = [
            {**record, "conversation": f"{split}-c{index}",
             "rows": {identifier: [record["rows"][identifier][index]] for identifier in reversed(IDS)}}
            for index in reversed(range(COUNTS[split]))
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        path = study / f"summary-{split}.json"
        summary = read_json(path)
        summary["table"].reverse()
        summary["manifest"]["experiments"].reverse()
        summary["manifest"]["expected_query_ids"].reverse()
        write_json(path, summary)
    bind_selection(study)
    summaries, manifests, nominees, rows = report.validate_inputs(study)
    assert all(set(table) == set(IDS) for table in summaries.values())
    assert set(rows) == set(IDS)


def test_matching_global_counts_do_not_hide_conversation_mismatch(study):
    path = study / "results-validation.jsonl"
    record = read_json(path)
    records = [
        {**record, "conversation": f"validation-c{index}",
         "rows": {identifier: [record["rows"][identifier][index]] for identifier in IDS}}
        for index in range(2)
    ]
    records[0]["rows"]["D01.01"], records[1]["rows"]["D01.01"] = (
        records[1]["rows"]["D01.01"], records[0]["rows"]["D01.01"])
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    assert_rejected(study, "within conversation")


@pytest.mark.parametrize("change", ["missing", "wrong", "summary_bytes_changed"])
def test_selection_is_bound_to_current_development_bytes(study, change):
    path = study / "selection-before-validation.json"
    receipt = read_json(path)
    if change == "missing":
        del receipt["development_sha256"]
    elif change == "wrong":
        receipt["development_sha256"] = "e" * 64
    else:
        summary_path = study / "summary-development.json"
        summary_path.write_bytes(summary_path.read_bytes() + b"\n")
    write_json(path, receipt)
    assert_rejected(study, "development_sha256")


@pytest.mark.parametrize("change", ["missing", "wrong", "receipt_bytes_changed", "nominee_changed", "extra_field_changed"])
def test_validation_is_bound_to_exact_selection_receipt(study, change):
    if change in ("missing", "wrong"):
        path = study / "summary-validation.json"
        summary = read_json(path)
        if change == "missing":
            del summary["manifest"]["selection_sha256"]
        else:
            summary["manifest"]["selection_sha256"] = "e" * 64
        write_json(path, summary)
    else:
        path = study / "selection-before-validation.json"
        receipt = read_json(path)
        if change == "receipt_bytes_changed":
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            if change == "nominee_changed":
                receipt["selected"][0]["id"] = "D01.01"
            else:
                receipt["preservation"]["retuned"] = True
            write_json(path, receipt)
    assert_rejected(study, "selection_sha256")


@pytest.mark.parametrize("change", [
    "unknown", "duplicate", "missing", "not_list", "missing_id", "empty",
    "missing_direction", "duplicate_direction", "wrong_direction",
])
def test_malformed_nominees_are_not_silently_omitted(study, change):
    path = study / "selection-before-validation.json"
    receipt = read_json(path)
    if change == "unknown":
        receipt["selected"][0]["id"] = "D11.01"
    elif change == "duplicate":
        receipt["selected"][0]["id"] = receipt["selected"][1]["id"]
    elif change == "missing":
        del receipt["selected"]
    elif change == "not_list":
        receipt["selected"] = {}
    elif change == "missing_id":
        del receipt["selected"][0]["id"]
    elif change == "empty":
        receipt["selected"] = []
    elif change == "missing_direction":
        del receipt["selected"][0]["direction"]
    elif change == "duplicate_direction":
        receipt["selected"][0]["direction"] = receipt["selected"][1]["direction"]
    else:
        receipt["selected"][0]["id"] = "D02.01"
    write_json(path, receipt)
    bind_selection(study)
    assert_rejected(study, "select")


@pytest.mark.parametrize("name", [
    *(f"summary-{split}.json" for split in SPLITS),
    *(f"results-{split}.jsonl" for split in SPLITS),
    "selection-before-validation.json",
])
def test_missing_input_files_fail_before_output(study, name):
    (study / name).unlink()
    with pytest.raises(FileNotFoundError):
        report.create(study)
    assert not any((study / output).exists() for output in OUTPUTS)


@pytest.mark.parametrize("split", SPLITS)
@pytest.mark.parametrize("field", ["fatals", "manifest", "table"])
def test_missing_summary_sections_fail_closed(study, split, field):
    path = study / f"summary-{split}.json"
    summary = read_json(path)
    del summary[field]
    write_json(path, summary)
    assert_rejected(study, field)


def test_invalid_inputs_do_not_overwrite_existing_report(study):
    for name in OUTPUTS:
        (study / name).write_text("existing report\n", encoding="utf-8")
    path = study / "summary-regression.json"
    summary = read_json(path)
    summary["table"][-1]["errors"] = 1
    write_json(path, summary)
    with pytest.raises(ValueError, match="errors"):
        report.create(study)
    assert all((study / name).read_text() == "existing report\n" for name in OUTPUTS)
