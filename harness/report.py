"""Check rerun integrity and report all experiments without changing nominees."""
import argparse
from collections import defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import random
from statistics import mean


DIRECTIONS = {
    "D01": "查询分解", "D02": "词汇归一与别名", "D03": "记忆索引表示",
    "D04": "结构化分段", "D05": "会话与层级检索", "D06": "时间与更新",
    "D07": "来源关联与多跳", "D08": "相关性重排序",
    "D09": "证据组织与摘录", "D10": "最小披露与拒答",
}
SPLITS = ("development", "validation", "diagnostic", "regression")
EXPERIMENT_IDS = {
    f"D{direction:02}.{variant:02}"
    for direction in range(1, 11) for variant in range(1, 11)
}
EXPECTED_IDS = {"BASE", *EXPERIMENT_IDS}


def _unique_ids(value, context):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{context}: expected a list of unique nonempty IDs")
    return set(value)


def _sha256(value, context):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{context}: invalid SHA-256")
    return value


def load_rows(path, expected_query_ids, split):
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            context = f"{path.name}:{line_number}"
            record = json.loads(line)
            if (not isinstance(record, dict)
                    or not isinstance(record.get("conversation"), str) or not record["conversation"]):
                raise ValueError(f"{context}: invalid conversation record")
            if record.get("split") != split:
                raise ValueError(f"{context}: invalid result split")
            # The runner appends retries and summarizes the last record per conversation.
            latest[record["conversation"]] = (context, record)
    rows = defaultdict(dict)
    for context, record in latest.values():
        if record.get("fatal"):
            raise ValueError(f"{context}: fatal result record")
        if not isinstance(record.get("rows"), dict) or set(record["rows"]) != EXPECTED_IDS:
            raise ValueError(f"{context}: rows must contain BASE and all 100 experiment IDs")
        conversation_ids = None
        for identifier, values in record["rows"].items():
            if not isinstance(values, list):
                raise ValueError(f"{context}: {identifier} has invalid rows")
            query_ids = set()
            for row in values:
                if not isinstance(row, dict) or row.get("status") != "ok":
                    raise ValueError(f"{context}: {identifier} has an error row")
                query_id = row.get("id")
                if not isinstance(query_id, str) or query_id not in expected_query_ids:
                    raise ValueError(f"{context}: {identifier} has an unexpected query ID")
                if query_id in rows[identifier]:
                    raise ValueError(f"{context}: {identifier} has a duplicate query ID")
                if row.get("split") != split:
                    raise ValueError(f"{context}: {identifier} has a wrong query split")
                rows[identifier][query_id] = row
                query_ids.add(query_id)
            if not query_ids or (conversation_ids is not None and query_ids != conversation_ids):
                raise ValueError(f"{context}: {identifier} query IDs/count differ within conversation")
            conversation_ids = query_ids
    if set(rows) != EXPECTED_IDS:
        raise ValueError(f"{path.name}: expected BASE and all 100 experiment IDs")
    for identifier, values in rows.items():
        if set(values) != expected_query_ids:
            raise ValueError(f"{path.name}: {identifier} query IDs/count do not match manifest")
    return rows


def validate_inputs(directory):
    summaries, manifests, summary_bytes = {}, {}, {}
    for split in SPLITS:
        path = directory / f"summary-{split}.json"
        summary_bytes[split] = path.read_bytes()
        summary = json.loads(summary_bytes[split])
        if not isinstance(summary, dict) or summary.get("fatals") != []:
            raise ValueError(f"{split}: summary fatals must be an empty list")
        manifest = summary.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError(f"{split}: missing manifest")
        expected_queries = manifest.get("expected_queries")
        query_ids = _unique_ids(manifest.get("expected_query_ids"), f"{split}: expected_query_ids")
        if (type(expected_queries) is not int or expected_queries <= 0
                or expected_queries != len(query_ids)):
            raise ValueError(f"{split}: expected_queries must match expected_query_ids")
        experiments = _unique_ids(manifest.get("experiments"), f"{split}: experiments")
        if experiments != EXPERIMENT_IDS:
            raise ValueError(f"{split}: manifest must name all 100 experiment IDs")
        if not isinstance(manifest.get("baseline"), str) or not manifest["baseline"]:
            raise ValueError(f"{split}: missing baseline")
        for field in ("code_sha256", "data_sha256"):
            _sha256(manifest.get(field), f"{split}: {field}")
        table = summary.get("table")
        if (not isinstance(table, list)
                or any(not isinstance(row, dict) for row in table)
                or _unique_ids([row.get("id") for row in table], f"{split}: table IDs") != EXPECTED_IDS):
            raise ValueError(f"{split}: table must contain BASE and all 100 experiment IDs")
        for row in table:
            for field, expected in (("questions", expected_queries), ("ok", expected_queries), ("errors", 0)):
                if type(row.get(field)) is not int or row[field] != expected:
                    raise ValueError(f"{split}: {row['id']} {field} must be {expected}")
        summaries[split] = {row["id"]: row for row in table}
        manifests[split] = manifest
    for split in SPLITS[1:]:
        for field in ("code_sha256", "baseline"):
            if manifests[split][field] != manifests["development"][field]:
                raise ValueError(f"{split}: {field} differs from development")
    public_data_sha = manifests["development"]["data_sha256"]
    for split in ("validation", "regression"):
        if manifests[split]["data_sha256"] != public_data_sha:
            raise ValueError(f"{split}: data_sha256 differs from development public data")
    if manifests["diagnostic"]["data_sha256"] == public_data_sha:
        raise ValueError("diagnostic: data_sha256 must differ from public data")

    receipt_bytes = (directory / "selection-before-validation.json").read_bytes()
    nominees = json.loads(receipt_bytes)
    if not isinstance(nominees, dict):
        raise ValueError("selection receipt must be an object")
    if nominees.get("development_sha256") != hashlib.sha256(summary_bytes["development"]).hexdigest():
        raise ValueError("selection development_sha256 does not match current development summary")
    selection_sha = _sha256(manifests["validation"].get("selection_sha256"), "validation: selection_sha256")
    if selection_sha != hashlib.sha256(receipt_bytes).hexdigest():
        raise ValueError("validation selection_sha256 does not match selection receipt")
    selected = nominees.get("selected")
    if (not isinstance(selected, list)
            or any(not isinstance(row, dict) or "id" not in row for row in selected)):
        raise ValueError("selection receipt must contain selected entries")
    directions = _unique_ids([row.get("direction") for row in selected], "selected directions")
    if directions != set(DIRECTIONS):
        raise ValueError("selection receipt must contain all ten directions")
    selected_ids = _unique_ids([row["id"] for row in selected if row["id"] is not None], "selected IDs")
    if not selected_ids <= EXPERIMENT_IDS:
        raise ValueError("selection receipt contains an unknown experiment ID")
    if any(row["id"] is not None and row["id"][:3] != row["direction"] for row in selected):
        raise ValueError("selection receipt experiment does not match its direction")

    validation_rows = None
    for split in SPLITS:
        rows = load_rows(directory / f"results-{split}.jsonl",
                         set(manifests[split]["expected_query_ids"]), split)
        if split == "validation":
            validation_rows = rows
    return summaries, manifests, nominees, validation_rows


def bootstrap_delta(rows, baseline, seed=20261029):
    strata = defaultdict(list)
    for identifier in sorted(rows):
        r = rows[identifier]
        b = baseline.get(identifier)
        if r.get("status") == "ok" and b and r["answerable"]:
            strata[(r["dataset"], r["category"])].append(r["recall20"] - b["recall20"])
    if not strata:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(2000):
        estimates.append(100 * mean(
            mean(rng.choices(strata[key], k=len(strata[key]))) for key in sorted(strata)))
    estimates.sort()
    return [estimates[49], estimates[1949]]


def create(directory):
    summaries, manifests, nominees, source_rows = validate_inputs(directory)
    development, validation, diagnostic, regression = (summaries[split] for split in SPLITS)
    selected = {r["id"] for r in nominees["selected"] if r["id"]}
    base_dev, base_val = development["BASE"], validation["BASE"]
    table = []
    for identifier in ["BASE", *sorted(i for i in development if i != "BASE")]:
        dev, val, diag, reg = (g[identifier] for g in
                               (development, validation, diagnostic, regression))
        row = {
            "id": identifier, "direction": DIRECTIONS.get(identifier[:3], "固定基线"),
            "name": dev["name"], "hypothesis": dev.get("hypothesis", ""),
            "mechanism": dev.get("mechanism", ""), "limitation": dev.get("limitation", ""),
            "selected_before_validation": identifier in selected,
            "dev_score": dev["evidence_score"], "dev_delta": dev["evidence_score"] - base_dev["evidence_score"],
            "validation_score": val["evidence_score"],
            "validation_delta": val["evidence_score"] - base_val["evidence_score"],
            "validation_r10": val["recall10"], "validation_r100": val["recall100"],
            "validation_all100": val["all100"], "validation_source_coverage100": val["source_coverage100"],
            "validation_improved": val["improved"], "validation_regressed": val["regressed"],
            "validation_triggered": val["triggered"], "dev_triggered": dev["triggered"],
            "diagnostic_triggered": diag["triggered"], "regression_triggered": reg["triggered"],
            "regression_score": reg["evidence_score"],
            "regression_delta": reg["evidence_score"] - regression["BASE"]["evidence_score"],
            "validation_p95_ms": val["p95_ms"], "validation_mean_source_chars": val["mean_characters"],
            "diagnostic_forbidden_hits": diag["forbidden_hits"],
            "diagnostic_privacy_hits": diag["privacy_forbidden_hits"],
            "diagnostic_correct_abstentions": diag["correct_abstentions"],
            "validation_correct_abstentions": val["correct_abstentions"],
            "errors": sum(v["errors"] for v in (dev, val, diag, reg)),
            "official_aml_score": None, "qa_accuracy": None,
        }
        row["bootstrap_95_delta"] = bootstrap_delta(source_rows[identifier], source_rows["BASE"]) if identifier in selected else None
        row["assessment"] = (
            "error" if row["errors"] else
            "inactive_in_evaluated_sets" if not any(row[k] for k in
                                                  ("dev_triggered", "validation_triggered", "diagnostic_triggered", "regression_triggered")) else
            "validation_regression" if row["validation_delta"] < -1e-10 else
            "validation_neutral" if abs(row["validation_delta"]) <= 1e-10 else
            "unsafe_or_coverage_tradeoff" if (
                diag["privacy_forbidden_hits"] > diagnostic["BASE"]["privacy_forbidden_hits"]
                or val["source_coverage100"] < base_val["source_coverage100"] - .01) else
            "positive_proxy_requires_answerer_validation"
        )
        table.append(row)
    (directory / "all-100-results.json").write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n")
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    (directory / "all-100-results.csv").write_text(stream.getvalue(), encoding="utf-8-sig")
    def num(value):
        return f"{value:.3f}" if value is not None else "未测"
    if nominees.get("validation_previously_seen"):
        validation_label = "题已查看验证"
        history = [
            "首轮因评测检查不完整而无效；本报告为补齐检查后的 v2 重跑。",
            "沿用同一组100个方案、同一份数据和首轮读取验证集前的预选项。",
            "验证集在首轮已被查看，本次不是新的独立留出验证。",
            "看到结果后未调整算法；本次只修复运行与报告检查。",
        ]
        selection_note = "每方向沿用首轮读取验证集前的预选项，不按本次开发或验证分数重新选择。"
    else:
        validation_label = "题验证"
        history = ["预选项由开发集结果固定；验证运行绑定选型凭据，不依据验证结果重新选择。"]
        selection_note = "每方向展示读取验证集前固定的开发预选项，不按验证结果重新选择。"
    lines = [
        "# 100个单机制实验结果", "",
        "**这里的分数是本地证据召回分，不是AML分数或答题准确率。**", "",
        "主分数 = 100 × 各题型平均R@20的均值；标准答案、证据标注不进入检索。",
        f"{manifests['development']['expected_queries']}题开发、"
        f"{manifests['validation']['expected_queries']}{validation_label}、"
        f"{manifests['regression']['expected_queries']}题已用LoCoMo回归、"
        f"{manifests['diagnostic']['expected_queries']}个独立合成诊断。",
        *history,
        "全部历史保留；无模型调用、无官方评测提交、无生产修改。",
        "每组只有一个机制相对v0.2.5变化，不是逐组叠加；全部负结果保留。", "",
        f"基线：开发 **{num(base_dev['evidence_score'])}**，验证 **{num(base_val['evidence_score'])}**。",
        "这两个数字不能与线上v0.2.5的AML Smoke 0.703559直接比较。", "",
        "## 每个方向的预选项", "",
        selection_note, "",
        "|方向|开发预选|开发分|验证分|验证差值|改善/回退|状态|",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for n in nominees["selected"]:
        r = next((r for r in table if r["id"] == n["id"]), None)
        if r:
            lines.append(f"|{r['direction']}|{r['id']} {r['name']}|{num(r['dev_score'])}|"
                         f"{num(r['validation_score'])}|{r['validation_delta']:+.3f}|"
                         f"{r['validation_improved']}/{r['validation_regressed']}|{r['assessment']}|")
    lines += ["", "## 全部100组", "",
              "|实验|名称|开发分|验证分|验证差值|验证触发|改善/回退|回归差值|诊断隐私命中|",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in table[1:]:
        lines.append(f"|{r['id']}|{r['name']}|{num(r['dev_score'])}|{num(r['validation_score'])}|"
                     f"{r['validation_delta']:+.3f}|{r['validation_triggered']}|"
                     f"{r['validation_improved']}/{r['validation_regressed']}|"
                     f"{r['regression_delta']:+.3f}|{r['diagnostic_privacy_hits']}|")
    lines += [
        "", "## 解读限制", "",
        "- 有触发但无收益是负结果；完全未触发说明当前测试覆盖不足，不算有效改进。",
        "- 命中标注消息不代表返回了答案所需语义。CSV另列原文覆盖率，短摘录不得只凭R@20宣称优胜。",
        "- 合成诊断的forbidden包含不相关记录；privacy字段仅统计隐私类别。它不是通用隐私保证。",
        "- 分组减少题目和证据session重叠，但干扰历史可能共享，置信区间只是探索性估计。",
        "- 100重比较存在选择偏差；只有事先固定的十个预选项提供探索性bootstrap区间，未作充分多重检验校正。",
        "- 每实验独立索引；search_ms不含单独记录的build_ms。相同实验内的查询可使用缓存，耗时不是生产延迟。",
        "- 没有获准的答案模型凭证，QA准确率与新AML成绩均未测，表中明确留空。",
        "- 保持线上v0.2.5，不因本地最高分直接部署。还需要Worker实现一致性、答案侧验证和用户确认。", "",
    ]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"rows": len(table) - 1, "selected": sorted(selected),
                      "positive_validation": sum(r["validation_delta"] > 0 for r in table[1:]),
                      "errors": sum(r["errors"] for r in table)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    create(parser.parse_args().output)
