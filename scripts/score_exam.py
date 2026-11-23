#!/usr/bin/env python3
"""Grade a mock exam: MCQ automatically, subjective by rubric keyword hit (initial pass).

Usage:
    python scripts/score_exam.py --spec exam_spec.json --responses responses.json --out out/
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def band_for(total: float, bands: list[dict]) -> str:
    for b in bands:
        if b["min"] <= total <= b["max"]:
            return b["label"]
    return "未定档"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--responses", required=True)
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    spec = load_json(Path(args.spec))
    resp = load_json(Path(args.responses))
    exam = spec["exam"]
    sections = {s["id"]: s for s in spec["profile"]["sections"]}

    rows = []
    for q in spec["questions"]:
        qid = q["id"]
        given = str(resp.get(qid, "")).strip()
        sec = q["section"]
        if q["type"] == "mcq":
            ok = given.upper()[:1] == str(q["answer"]).upper()[:1]
            got = q["points"] if ok else 0
            hit_points, miss_points = [], []
        else:
            text = given.lower()
            got = 0
            hit_points, miss_points = [], []
            for r in q.get("rubric", []):
                hit = bool(given) and any(k.lower() in text for k in r.get("keywords", []))
                if hit:
                    got += r["score"]
                    hit_points.append(r["point"])
                else:
                    miss_points.append(r["point"])
            got = min(got, q["points"])
            ok = got >= q["points"] * 0.6
        rows.append({
            "id": qid, "section": sec, "type": q["type"], "dimension": q["dimension"],
            "topic": q["topic"], "difficulty": q["difficulty"], "points": q["points"],
            "got": got, "ok": ok, "given": given, "answer": q.get("answer", ""),
            "hit_points": hit_points, "miss_points": miss_points,
            "explanation": q.get("explanation", ""), "reference": q.get("reference", ""),
        })

    total = sum(r["got"] for r in rows)
    full = sum(r["points"] for r in rows)
    band = band_for(total, exam.get("grade_bands", []))

    # section stats
    sec_stat = defaultdict(lambda: {"n": 0, "got": 0, "full": 0, "right": 0})
    for r in rows:
        st = sec_stat[r["section"]]
        st["n"] += 1
        st["got"] += r["got"]
        st["full"] += r["points"]
        st["right"] += 1 if r["ok"] else 0

    # dimension stats
    dim_stat = defaultdict(lambda: {"n": 0, "got": 0, "full": 0})
    for r in rows:
        st = dim_stat[r["dimension"]]
        st["n"] += 1
        st["got"] += r["got"]
        st["full"] += r["points"]

    # topic stats (lost points drive priority, not count of errors)
    topic_stat = defaultdict(lambda: {"n": 0, "got": 0, "full": 0})
    for r in rows:
        st = topic_stat[r["topic"]]
        st["n"] += 1
        st["got"] += r["got"]
        st["full"] += r["points"]

    lost_topics = sorted(
        ((t, v["full"] - v["got"], v["full"], v["got"] / v["full"] if v["full"] else 0)
         for t, v in topic_stat.items()),
        key=lambda x: x[1], reverse=True,
    )
    weak_topics = [t for t in lost_topics if t[1] > 0 and t[3] < 0.6]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# {exam.get('name', '')}　成绩报告", "",
        f"- **总分**：{total} / {full}",
        f"- **档位**：{band}（及格线 {exam.get('pass_score')}，优秀线 "
        f"{next((b['min'] for b in exam.get('grade_bands', []) if b['label'] == '优秀'), '-')}）",
        f"- **得分率**：{total / full:.1%}" if full else "",
        f"- **客观题正确率**：{sum(1 for r in rows if r['type']=='mcq' and r['ok'])} / "
        f"{sum(1 for r in rows if r['type']=='mcq')}",
        "",
        "> 主观题为按采分点关键词的机器初评，仅用于定位薄弱点，最终成绩以人工复核为准。",
        "",
        "## 分模块", "", "| 模块 | 题量 | 得分/满分 | 得分率 | 全对（客观）/达标（主观） |", "|---|---|---|---|---|",
    ]
    for sid, st in sorted(sec_stat.items()):
        name = sections.get(sid, {}).get("title", sid)
        lines.append(f"| {name} | {st['n']} | {st['got']}/{st['full']} | {st['got']/st['full']:.0%} | {st['right']} |")

    lines += ["", "## 分维度", "", "| 维度 | 题量 | 得分/满分 | 得分率 |", "|---|---|---|---|"]
    for d, st in sorted(dim_stat.items(), key=lambda x: x[1]["got"] / x[1]["full"] if x[1]["full"] else 0):
        lines.append(f"| {d} | {st['n']} | {st['got']}/{st['full']} | {st['got']/st['full']:.0%} |")

    wrong = [r for r in rows if not r["ok"]]
    lines += ["", "## 错题与失分", ""]
    if wrong:
        lines += ["| 题号 | 模块 | 考点 | 我的答案 | 正确答案/采分点缺失 | 失分 |", "|---|---|---|---|---|---|"]
        for r in wrong:
            mine = r["given"] or "（未作答）"
            if r["type"] == "mcq":
                right = r["answer"]
            else:
                right = "；".join(r["miss_points"]) if r["miss_points"] else "覆盖面不足"
            lines.append(
                f"| {r['id']} | {r['section']} | {r['topic']} | {mine[:24]} | {right[:40]} | {r['points']-r['got']} |"
            )
    else:
        lines += ["全对。"]

    lines += ["", "## 高价值失分点（按失分 × 考点体量排序）", "",
              "| 考点 | 失分 | 该考点总分 | 正确率 |", "|---|---|---|---|"]
    for t, lost, fullp, rate in lost_topics[:8]:
        if lost <= 0:
            continue
        lines.append(f"| {t} | {lost} | {fullp} | {rate:.0%} |")

    # strategy
    bands = sorted(exam.get("grade_bands", []), key=lambda b: b["min"])
    labels = [b["label"] for b in bands]
    cur = labels.index(band) if band in labels else None
    next_band = bands[cur + 1] if cur is not None and cur + 1 < len(bands) else None
    top_band = bands[-1] if bands else None
    weak_modules = [
        (sections.get(sid, {}).get("title", sid), st["got"] / st["full"])
        for sid, st in sec_stat.items() if st["full"] and st["got"] / st["full"] < 0.7
    ]

    lines += ["", "## 结论与下一步", ""]
    if cur is None:
        lines += ["- 档位未匹配，请检查 exam.grade_bands 配置。"]
    elif next_band is None:
        lines += [f"- 已到最高档（{band}）。下一阶段只做两件事：主观题的语言与结构打磨，"
                  f"以及把错题考点做成 10 题以内的速查卡。"]
    elif band == "未通过" or total < exam.get("pass_score", full * 0.6):
        gap = exam.get("pass_score", full * 0.6) - total
        lines += [f"- 距及格线还差 {gap} 分。先补知识型考点（客观题），再练主观题框架。"]
        lines += ["- 客观题按模块刷：每个模块限时 15 分钟，错题当天记录'考点 + 错因'两行。"]
        lines += ["- 主观题先背三套句式：表态、归因、方案，再把关键词填进去。"]
    else:
        gap_next = next_band["min"] - total
        gap_top = top_band["min"] - total if top_band else 0
        lines += [f"- 当前档位：{band}。距{next_band['label']}线（{next_band['min']}）差 "
                  f"{gap_next} 分，距最高档（{top_band['min']}）差 {gap_top} 分。"]
        if gap_next <= 10:
            lines += ["- 只差一步：优先把 2-3 个单点知识补上，再做一套同等难度的半程卷验证。"]
        lines += ["- 主观题按'识别冲突 → 理论归因 → 方案'三段写，每段至少 2 个采分点关键词。"]
    if weak_modules:
        lines += ["- 模块短板：" + "；".join(f"{name} 得分率 {rate:.0%}" for name, rate in weak_modules)
                  + "。补短板比刷整套更划算。"]

    if weak_topics:
        lines += ["", "## 专项强化卷建议", "",
                  "只抽下列考点，题量控制在 1/3，限时 40 分钟：", ""]
        for t, lost, fullp, rate in weak_topics[:5]:
            lines.append(f"- {t}（正确率 {rate:.0%}，本卷失分 {lost}）")
        for name, rate in weak_modules:
            lines.append(f"- 模块级：{name} 整体得分率 {rate:.0%}，整模块重练一遍再进下一模块")

    # schedule to exam day
    exam_date = exam.get("date")
    if exam_date:
        try:
            from datetime import date
            target = date.fromisoformat(exam_date)
            days_left = (target - date.today()).days
            lines += ["", f"## 到考试日的安排（{exam_date}，剩余 {days_left} 天）", ""]
            if days_left <= 0:
                lines += ["- 考试日期已到或已过，直接进入错题速查与状态调整。"]
            elif days_left <= 3:
                lines += [f"- 今天：只补模块短板（{('、'.join(n for n, _ in weak_modules)) or '错题考点'}），限时 60 分钟。",
                          "- 明天：做一套半程卷（客观题 35 题 + 1 简答 + 1 案例），严格计时。",
                          "- 考前：只看错题卡与主观题三段框架，不再开新题；确认机考打字速度与作答界面。"]
            else:
                first = max(2, days_left // 3)
                lines += [f"- 第 1-{first} 天：补模块短板，每天 1 个模块 + 错题当天归档。",
                          f"- 第 {first + 1}-{days_left - 1} 天：主观题模板各写 2 篇，隔天做半程卷；客观题每天 20 题保持手感。",
                          "- 最后 1 天：全真二模（同一时段 9:30-11:30），之后只复盘错题，不开新题。"]
            lines += ["- 每天固定 20 分钟回看'考点 + 错因'两行记录，避免同类题二次失分。"]
        except ValueError:
            lines += ["", f"（考试日期 {exam_date} 格式无法解析，跳过日程生成）"]

    (out_dir / "grade_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "graded_detail.json").write_text(
        json.dumps({"total": total, "full": full, "band": band, "rows": rows},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OK: grade_report.md written. total {total}/{full} ({total/full:.1%}), band {band}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
