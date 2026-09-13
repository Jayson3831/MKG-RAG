"""WP6：人工审核包与诊断包导出。

硬性约束（任务书 §7、AGENTS.md）：
- 初始 human_status 必须为 pending；未经真实人工审核不得填写通过/不通过。
- 自动检查不得冒充人工审核；auto_status 与 human_status 分开保存。
- 定向排错样本（诊断包）不能并入正式质量估计。
- HTML 是本次运行的审核产物，不另建平行总结文档。
"""
from __future__ import annotations

import html
import random
from typing import Dict, List

from . import util
from .config import Config


def select_audit(cfg: Config, qualified: List[Dict]) -> Dict:
    """分层随机抽取正式审核样本（不足则全选），并记录抽样概率。

    按层（root_stratum）分层，保留相应权重；选择方法与种子一并冻结。
    """
    a = cfg.raw["audit"]
    rnd = random.Random(a["seed"])
    by_stratum: Dict[str, List[Dict]] = {}
    for c in qualified:
        by_stratum.setdefault(c.get("root_stratum") or "unknown", []).append(c)
    target = a["sample_size"]
    picked: List[Dict] = []
    if len(qualified) <= target:
        picked = list(qualified)
    else:
        total = len(qualified)
        for name, rows in sorted(by_stratum.items()):
            quota = max(1, int(round(target * len(rows) / total)))
            take = min(len(rows), quota)
            picked += rnd.sample(rows, take)
        if len(picked) > target:
            picked = rnd.sample(picked, target)
        elif len(picked) < target:
            rest = [c for c in qualified if c not in picked]
            picked += rnd.sample(rest, min(len(rest), target - len(picked)))
    ids = []
    for c in picked:
        st = c.get("root_stratum") or "unknown"
        pop = len(by_stratum.get(st, []))
        ids.append({
            "question_id": c["question_id"],
            "track": c.get("track"),
            "template_id": c.get("template_id"),
            "stratum": st,
            "selection_method": "stratified_random",
            "seed": a["seed"],
            "stratum_population": pop,
            "selection_probability": (len([x for x in picked if (x.get("root_stratum") or "unknown") == st]) / pop) if pop else None,
        })
    return {"sample_size": len(ids),
            "target": target,
            "shortfall": max(0, target - len(ids)),
            "seed": a["seed"],
            "method": "stratified_random_by_root_stratum",
            "selected": ids,
            "note": "不足目标时全选；未达 100 条如实记录，不凑数。"}


def select_diagnostic(cfg: Config, non_qualified: List[Dict]) -> Dict:
    """定向诊断样本：失败/高风险候选，用于解释误差，不并入质量估计。"""
    a = cfg.raw["audit"]
    rnd = random.Random(a["seed"] + 1)
    n = min(a["diagnostic_size"], len(non_qualified))
    picked = rnd.sample(non_qualified, n) if n else []
    return {"sample_size": n, "target": a["diagnostic_size"],
            "seed": a["seed"] + 1,
            "ids": [c["question_id"] for c in picked],
            "note": "定向排错样本，不并入随机质量估计。"}


def init_audit_records(cfg: Config, selection: Dict,
                       results_by_id: Dict[str, Dict]) -> List[Dict]:
    """生成 audit.jsonl 初始记录：human_status 一律 pending。"""
    rows = []
    for s in selection["selected"]:
        r = results_by_id.get(s["question_id"], {})
        rows.append({
            "question_id": s["question_id"],
            "template_id": s.get("template_id"),
            "stratum": s.get("stratum"),
            "auto_status": r.get("state"),
            "human_status": cfg.raw["audit"]["human_status_initial"],  # pending
            "reviewer": None,
            "reviewed_at": None,
            "evidence_questions": [
                "事实确实存在：en/fr 每条关键三元组能否在固定输入文件找到",
                "桥接确实同一实体：参考链接、标签、类型及邻居是否支持",
                "答案确实由联合证据推出：逐跳方向、日期精度、单位与限定条件",
                "单图确实无法完成：单图起点是否已解析为本地 URI；替代路径实际输出",
                "任务没有被人为造难：是否截断、隐藏事实或缩窄单图检索权限",
                "分类与自然语言一致：全部单图为空才是严格连接；部分答案为集合补全",
            ],
            "notes": None,
            "revision_of": None,
            "revised_label": None,
        })
    return rows


CHECK_ITEMS = [
    ("fact_exists", "事实确实存在（源文件可查）"),
    ("bridge_same", "桥接确实同一实体"),
    ("answer_from_joint", "答案确实由联合证据推出"),
    ("single_graph_fails", "单图确实无法完成"),
    ("not_artificial", "任务没有被人为造难"),
    ("label_consistent", "分类与自然语言一致"),
]


def render_html(cfg: Config, run_id: str, selection: Dict,
                results_by_id: Dict[str, Dict], ledger_by_id: Dict[str, Dict],
                labels: Dict[str, Dict[str, str]]) -> str:
    """生成可离线打开的审核表。所有字段转义，无外部依赖。"""
    e = html.escape
    parts = [f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>E001 审核表 {e(run_id)}</title>
<style>
body{{font-family:-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
max-width:1100px;margin:24px auto;padding:0 16px;line-height:1.5;color:#111}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}}
.card{{border:1px solid #ddd;border-radius:6px;padding:12px 14px;margin:14px 0;background:#fafafa}}
.qid{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#555}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}}
th,td{{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}}
th{{background:#f0f0f0}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
background:#eee;padding:1px 4px;border-radius:3px;word-break:break-all}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;word-break:break-all}}
.state{{font-weight:600}} .pending{{color:#b26a00}}
ul.checks{{list-style:none;padding-left:0}} ul.checks li{{margin:3px 0}}
</style></head><body>
<h1>E001 审核表 · 运行 {e(run_id)}</h1>
<p>release <code>{e(cfg.release_id)}</code>；抽样方法 <code>{e(selection['method'])}</code>；
种子 <code>{e(str(selection['seed']))}</code>；样本 <b>{selection['sample_size']}</b> 条
（目标 {selection['target']}，不足 {selection['shortfall']} 条）。</p>
<p><b>人工审核状态一律为 <span class="pending">pending</span></b>：
本页由自动流水线生成，不构成人工审核通过。请逐条核对下列六项后自行记录结论。</p>
"""]
    for s in selection["selected"]:
        qid = s["question_id"]
        r = results_by_id.get(qid, {})
        c = ledger_by_id.get(qid, {})
        parts.append(f'<div class="card">')
        parts.append(f'<h2>#{e(qid)}</h2>')
        parts.append(f'<p class="qid">模板 {e(str(s.get("template_id")))} · '
                     f'轨道 {e(str(s.get("track")))} · 层 {e(str(s.get("stratum")))} · '
                     f'选择概率 {e(str(s.get("selection_probability")))}</p>')
        parts.append(f'<p>自动状态 <span class="state">{e(str(r.get("state")))}</span>；'
                     f'人工状态 <span class="state pending">pending</span></p>')
        parts.append(f'<p><b>问题：</b>{e(str(c.get("natural_question") or c.get("question_template") or ""))}</p>')
        parts.append(f'<p><b>查询：</b><code>{e(" / ".join(c.get("chain", [])))}</code> '
                     f'· 图归属 <code>{e(",".join(c.get("split", [])))}</code> '
                     f'· 起点 <code>{e(str(c.get("root_iris")))}</code></p>')
        for title, key in (("联合答案 A*", "answers_joint"),
                           ("英文单图 A_en", "answers_en"),
                           ("法文单图 A_fr", "answers_fr")):
            vals = r.get(key) or []
            txt = ", ".join(util.local_name(v) for v in vals) if vals else "（空）"
            parts.append(f'<p><b>{e(title)}：</b><span class="mono">{e(txt)}</span></p>')
        # 证据路径（含源文件与行号）
        paths = r.get("joint_records") or []
        if paths:
            parts.append("<p><b>联合证据路径：</b></p><table>"
                         "<tr><th>图</th><th>主语</th><th>谓词</th><th>宾语</th>"
                         "<th>源文件</th><th>行号</th></tr>")
            for rec in paths[:3]:
                for st in rec.get("path", []):
                    parts.append(
                        f'<tr><td>{e(str(st.get("graph")))}</td>'
                        f'<td class="mono">{e(util.local_name(str(st.get("s"))))}</td>'
                        f'<td class="mono">{e(str(st.get("p")))}</td>'
                        f'<td class="mono">{e(util.local_name(str(st.get("o"))))}</td>'
                        f'<td class="mono">{e(str(st.get("src")))}</td>'
                        f'<td>{e(str(st.get("lineno")))}</td></tr>')
                parts.append(f'<tr><td colspan="6"><b>桥接：</b>'
                             f'<span class="mono">{e(str(rec.get("bridges")))}</span></td></tr>')
            parts.append("</table>")
        # 替代路径检查
        hits = r.get("alt_path_hits") or []
        parts.append(f'<p><b>替代路径检查：</b>状态 <code>{e(str(r.get("alt_check_status")))}</code>；'
                     f'命中 {len(hits)} 条'
                     + (f'，例如 <code>{e(str(hits[0]))}</code>' if hits else '') + '</p>')
        if r.get("consistency_error"):
            parts.append(f'<p><b>一致性错误：</b>{e(str(r["consistency_error"]))}</p>')
        parts.append('<p><b>逐项核对（人工填写）：</b></p><ul class="checks">')
        for k, label in CHECK_ITEMS:
            parts.append(f'<li><label><input type="checkbox"> {e(label)}</label></li>')
        parts.append("</ul></div>")
    parts.append("</body></html>")
    return "\n".join(parts)
