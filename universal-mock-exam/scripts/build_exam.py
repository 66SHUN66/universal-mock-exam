#!/usr/bin/env python3
"""Render a mock exam pack (paper + answer key + QC report) from exam_spec.json.

Usage:
    python scripts/build_exam.py --spec exam_spec.json --out out/
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def letter_of(opt: str) -> str:
    return opt.strip()[:1].upper()


def validate(spec: dict) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    exam = spec.get("exam", {})
    profile = spec.get("profile", {})
    sections = profile.get("sections", [])
    questions = spec.get("questions", [])

    for key in ("name", "level", "duration_min", "total_score", "pass_score"):
        if key not in exam:
            issues.append(("error", f"exam 缺少字段 {key}"))
    if not sections:
        issues.append(("error", "profile.sections 为空"))
    if not questions:
        issues.append(("error", "questions 为空"))
        return issues

    sec_ids = {s.get("id") for s in sections}
    seen = set()
    by_sec: dict[str, list[dict]] = defaultdict(list)

    for q in questions:
        qid = q.get("id", "<no-id>")
        if qid in seen:
            issues.append(("error", f"题号重复：{qid}"))
        seen.add(qid)
        for field in ("id", "section", "type", "dimension", "topic", "difficulty", "points", "stem"):
            if not q.get(field):
                issues.append(("error", f"{qid} 缺少字段 {field}"))
        if q.get("section") not in sec_ids:
            issues.append(("error", f"{qid} 的 section {q.get('section')} 不在 sections 中"))
        if q.get("difficulty") not in ("easy", "medium", "hard"):
            issues.append(("error", f"{qid} difficulty 非法：{q.get('difficulty')}"))
        by_sec[q.get("section")].append(q)

        if q.get("type") == "mcq":
            options = q.get("options") or []
            if len(options) < 3:
                issues.append(("error", f"{qid} 选项少于 3 个"))
            letters = [letter_of(o) for o in options]
            if q.get("answer", "").upper() not in letters:
                issues.append(("error", f"{qid} answer={q.get('answer')} 不在选项 {letters}"))
            if not q.get("explanation"):
                issues.append(("warning", f"{qid} 缺 explanation"))
            # 单项不做长度判定：整卷统计见下方 aggregate 检查
        else:
            rubric = q.get("rubric") or []
            if not rubric:
                issues.append(("error", f"{qid} 主观题缺 rubric"))
            total = sum(r.get("score", 0) for r in rubric)
            if total != q.get("points"):
                issues.append(("error", f"{qid} rubric 合计 {total} != points {q.get('points')}"))

    # section counts and points
    declared_points = 0
    for s in sections:
        sid = s.get("id")
        actual = len(by_sec.get(sid, []))
        if s.get("type") == "mcq":
            if actual != s.get("count"):
                issues.append(("error", f"Section {sid} 题量 {actual} != 声明 {s.get('count')}"))
            declared_points += s.get("points_each", 0) * s.get("count", 0)
        else:
            if s.get("count") and actual != s.get("count"):
                issues.append(("error", f"Section {sid} 题量 {actual} != 声明 {s.get('count')}"))
            declared_points += sum(q.get("points", 0) for q in by_sec.get(sid, []))

    actual_total = sum(q.get("points", 0) for q in questions)
    if actual_total != exam.get("total_score"):
        issues.append(("error", f"全卷分值合计 {actual_total} != 满分 {exam.get('total_score')}"))
    if declared_points != actual_total:
        issues.append(("warning", f"题型矩阵分值合计 {declared_points} != 实际 {actual_total}"))

    # difficulty distribution
    target = profile.get("difficulty_target", {})
    if target:
        n = len([q for q in questions if q.get("type") == "mcq"])
        dist = Counter(q.get("difficulty") for q in questions if q.get("type") == "mcq")
        for level, ratio in target.items():
            if not n:
                continue
            actual_ratio = dist.get(level, 0) / n
            if abs(actual_ratio - ratio) > 0.10:
                issues.append((
                    "warning",
                    f"难度 {level} 实际 {actual_ratio:.0%} 偏离目标 {ratio:.0%} 超过 10 个百分点",
                ))

    # dimension coverage
    dims = {q.get("dimension") for q in questions}
    if len(dims) < 3:
        issues.append(("warning", f"维度覆盖只有 {len(dims)} 个：{sorted(dims)}"))

    # answer position balance
    mcq = [q for q in questions if q.get("type") == "mcq"]
    if mcq:
        longest_is_answer = sum(
            1 for q in mcq
            if len(q.get("options", [])) == 4
            and letter_of(max(q["options"], key=len)) == q.get("answer", "").upper()
        )
        ratio = longest_is_answer / len(mcq)
        if ratio > 0.6:
            issues.append((
                "warning",
                f"最长选项恰为答案的题占 {ratio:.0%}（{longest_is_answer}/{len(mcq)}），超出 60% 阈值，"
                "考生可凭长度猜答案，需改写干扰项",
            ))
        pos = Counter(q.get("answer", "").upper() for q in mcq)
        for letter in "ABCD":
            share = pos.get(letter, 0) / len(mcq)
            if share < 0.15 or share > 0.35:
                issues.append(("warning", f"答案 {letter} 占比 {share:.0%}，分布不均衡"))

    if not any(sev == "error" for sev, _ in issues):
        issues.append(("info", "结构与分值校验通过"))
    return issues


def header_block(exam: dict) -> str:
    bands = "、".join(f"{b['label']} {b['min']}-{b['max']}" for b in exam.get("grade_bands", []))
    lines = [
        f"# {exam.get('name', '')}　模拟试卷",
        "",
        f"- **级别**：{exam.get('level', '')}",
        f"- **时长**：{exam.get('duration_min', '')} 分钟",
        f"- **满分**：{exam.get('total_score', '')} 分　**及格线**：{exam.get('pass_score', '')} 分",
        f"- **档位**：{bands}",
        f"- **生成依据**：{exam.get('basis', '未声明')}",
        "",
    ]
    if exam.get("instructions"):
        lines += ["## 作答须知", ""]
        lines += [f"{i}. {t}" for i, t in enumerate(exam["instructions"], 1)]
        lines += [""]
    return "\n".join(lines)


def render_paper(spec: dict) -> tuple[str, dict]:
    exam = spec["exam"]
    sections = spec["profile"]["sections"]
    numbered: dict[str, int] = {}
    out = [header_block(exam)]
    idx = 0

    for s in sections:
        qs = [q for q in spec["questions"] if q.get("section") == s["id"]]
        if not qs:
            continue
        if s.get("type") == "mcq":
            mintotal = s.get("points_each", 0) * len(qs)
            sub = f"共 {len(qs)} 题，每题 {s.get('points_each')} 分，共 {mintotal} 分　建议 {s.get('minutes', '')} 分钟"
        else:
            mintotal = sum(q.get("points", 0) for q in qs)
            sub = f"共 {len(qs)} 题，共 {mintotal} 分　建议 {s.get('minutes', '')} 分钟"
        out += [f"## {s.get('title', s['id'])}", "", f"*{sub}*", ""]
        if s.get("instructions"):
            out += [f"> {s['instructions']}", ""]

        for q in qs:
            idx += 1
            numbered[q["id"]] = idx
            out += [f"**{idx}.** {q['stem']}", ""]
            if q.get("type") == "mcq":
                out += [f"- {o}" for o in q.get("options", [])]
                out += [""]
            else:
                out += ["> **Answer:**", ""]
                out += ["______________________________________________________", ""] * 4
    out += ["---", "", "*试卷结束。答完后提交作答答案，进入判分与解析。*", ""]
    return "\n".join(out), numbered


def render_key(spec: dict, numbered: dict[str, int]) -> str:
    exam = spec["exam"]
    qs = spec["questions"]
    out = [f"# {exam.get('name', '')}　答案与解析", ""]
    out += ["## 答案速查", "", "| 题号 | 答案 | 维度 | 考点 | 难度 | 来源 |", "|---|---|---|---|---|---|"]
    for q in qs:
        ans = q.get("answer", "（主观）") if q.get("type") == "mcq" else "主观题"
        out.append(
            f"| {numbered.get(q['id'], '?')} | {ans} | {q.get('dimension')} | {q.get('topic')} | {q.get('difficulty')} | {q.get('source_tag', '-')} |"
        )
    out += ["", "## 逐题解析", ""]
    for q in qs:
        n = numbered.get(q["id"], "?")
        out += [f"### 第 {n} 题（{q.get('dimension')}｜{q.get('topic')}｜{q.get('difficulty')}｜{q.get('source_tag', '-')}）", ""]
        out += [f"**题干**：{q['stem']}", ""]
        if q.get("type") == "mcq":
            out += [f"**答案**：{q.get('answer')}", ""]
            out += [f"**依据**：{q.get('explanation', '')}", ""]
            if q.get("distractors"):
                out += [f"**干扰项分析**：{q['distractors']}", ""]
        else:
            out += [f"**参考答案**：{q.get('reference', '')}", ""]
            rubric = q.get("rubric", [])
            out += ["**采分点**：", "", "| 采分点 | 分值 | 判定关键词示例 |", "|---|---|---|"]
            for r in rubric:
                kws = " / ".join(r.get("keywords", [])[:4]) or "语义等价即可"
                out.append(f"| {r.get('point')} | {r.get('score')} | {kws} |")
            out += [""]
            if q.get("common_errors"):
                out += [f"**常见失分**：{q['common_errors']}", ""]
    return "\n".join(out)


def render_qc(spec: dict, issues: list[tuple[str, str]]) -> str:
    lines = [f"# 质检报告：{spec['exam'].get('name', '')}", ""]
    levels = {"error": "错误", "warning": "警告", "info": "通过"}
    for sev in ("error", "warning", "info"):
        items = [msg for s, msg in issues if s == sev]
        if not items:
            continue
        lines += [f"## {levels[sev]}（{len(items)}）", ""]
        lines += [f"- {m}" for m in items]
        lines += [""]
    mcq = [q for q in spec["questions"] if q.get("type") == "mcq"]
    if mcq:
        lines += ["## 结构统计", ""]
        lines += [f"- 客观题 {len(mcq)} 题，主观题 {len(spec['questions']) - len(mcq)} 题"]
        dist = Counter(q.get("difficulty") for q in mcq)
        lines += [f"- 难度：易 {dist.get('easy', 0)} / 中 {dist.get('medium', 0)} / 难 {dist.get('hard', 0)}"]
        ans = Counter(q.get("answer", "").upper() for q in mcq)
        lines += [f"- 答案分布：A {ans.get('A', 0)} / B {ans.get('B', 0)} / C {ans.get('C', 0)} / D {ans.get('D', 0)}"]
        dims = Counter(q.get("dimension") for q in mcq)
        lines += [f"- 维度：{dict(dims)}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    spec = load_json(Path(args.spec))
    issues = validate(spec)
    if any(sev == "error" for sev, _ in issues):
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "qc_report.md").write_text(render_qc(spec, issues), encoding="utf-8")
        print("STRUCTURE ERRORS FOUND -> see qc_report.md")
        for sev, msg in issues:
            if sev == "error":
                print(f"  [error] {msg}")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    paper, numbered = render_paper(spec)
    (out_dir / "paper.md").write_text(paper, encoding="utf-8")
    (out_dir / "answer_key.md").write_text(render_key(spec, numbered), encoding="utf-8")
    (out_dir / "qc_report.md").write_text(render_qc(spec, issues), encoding="utf-8")
    (out_dir / "index.json").write_text(
        json.dumps({"number": numbered, "reverse": {str(v): k for k, v in numbered.items()}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    warn = sum(1 for s, _ in issues if s == "warning")
    print(f"OK: paper.md / answer_key.md / qc_report.md written to {out_dir}")
    print(f"    客观题 {sum(1 for q in spec['questions'] if q['type']=='mcq')} 题，"
          f"主观题 {sum(1 for q in spec['questions'] if q['type']!='mcq')} 题，"
          f"合计 {sum(q['points'] for q in spec['questions'])} 分，警告 {warn} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
