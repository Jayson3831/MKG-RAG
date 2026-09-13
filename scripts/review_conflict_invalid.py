#!/usr/bin/env python
"""复核工具：conflict_invalid 样本的构成与一致性检查的适用空间。

**本脚本不改变任何判定，也不重跑正式运行。** 它只读取某次运行已保存的
候选账本，外加一次并集语义的联合查询，回答两个问题：

1. `conflict_invalid` 的样本来自哪类模板？配置里 `split` 非空的模板，其联合
   查询是**逐跳限定图归属**的连接；`split` 为空（并集语义）的模板则不同。
   任务书 §7.8 的一致性不变量是「单图答案 ⊆ 联合答案」，其依据是：两图可用
   时能推出的事实只会比单图更多。这个单调性只在联合查询取并集语义时成立。
2. 若把该不变量放到并集语义答案集 A_union 上检验，有多少样本真的违反它；
   以及这些样本按 §7.7/§218 的三分法（全部单图为空→严格连接；部分答案→
   集合补全；≥1 单图完整回答→单图可回答）会落到哪里。

输出仅作人工复核材料，不构成结论。

用法：
    python scripts/review_conflict_invalid.py --run-dir outputs/runs/<id>
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from e001 import util                                            # noqa: E402
from e001.config import load as load_config                      # noqa: E402
from e001.graph import Alignment, Graph, query_joint             # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser(description="conflict_invalid 构成复核")
    ap.add_argument("--config", default=os.path.join(PROJECT_ROOT, "configs",
                                                     "e001.yaml"))
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    ledger = list(util.read_jsonl(os.path.join(args.run_dir,
                                               "candidates.jsonl")))
    track_a = [r for r in ledger if r.get("track") == "A"]
    flagged = [r for r in track_a if r["state"] == "conflict_invalid"]
    print(f"账本 {len(ledger)} 条；轨道 A {len(track_a)} 条；"
          f"conflict_invalid {len(flagged)} 条", flush=True)

    by_split = collections.Counter(bool(r["split"]) for r in flagged)
    print(f"  其中 split 非空（逐跳限定图归属）: {by_split[True]} 条；"
          f"split 为空（并集语义）: {by_split[False]} 条", flush=True)

    # 三分法反事实：只用账本已保存的答案集合，不重新判定，不算替代路径。
    tri = collections.Counter()
    for r in flagged:
        a = set(r.get("answers_joint_canon") or [])
        en = set(r.get("answers_en_canon") or [])
        fr = set(r.get("answers_fr_canon") or [])
        if not a:
            tri["A* 为空（应另行归类）"] += 1
        elif en >= a or fr >= a:
            tri["至少一个单图完整回答 -> single_graph"] += 1
        elif (en | fr) & a:
            tri["部分答案 -> completion"] += 1
        else:
            tri["单图答案与 A* 无交集 -> 现行代码落到 strict_join"] += 1
    print("\n按 §7.7/§218 三分法的反事实分布（不含替代路径复核）：")
    for k, v in tri.most_common():
        print(f"  {k:46s} {v}")

    # 并集语义下的单调性检验
    graphs = {}
    for lang in cfg.languages:
        g = Graph(lang)
        g.load(PROJECT_ROOT)
        graphs[lang] = g
    align = Alignment(cfg.languages)
    align.load(PROJECT_ROOT)

    rows, viol_union = [], 0
    for i, r in enumerate(flagged, 1):
        root_lang, chain = r["root_lang"], r["chain"]
        root = r["root_iris"].get(root_lang)
        a_union, urecs, _uu = query_joint(graphs, align, root_lang, root,
                                          chain, [])
        ulang = {a: (p[-1][0] if p else root_lang) for a, p, _ in urecs}
        a_union_c = {align.canon(ulang.get(x, root_lang), x) for x in a_union}
        singles = {l: {align.canon(l, x)
                       for x in (r.get(f"answers_{l}") or [])}
                   for l in cfg.languages}
        bad = {l: sorted(singles[l] - a_union_c) for l in cfg.languages
               if singles[l] and not singles[l] <= a_union_c}
        viol_union += bool(bad)
        rows.append({"question_id": r["question_id"],
                     "template_id": r["template_id"], "split": r["split"],
                     "a_star_canon": sorted(r.get("answers_joint_canon") or []),
                     "a_union_canon_size": len(a_union_c),
                     "n_answers_en": len(singles["en"]),
                     "n_answers_fr": len(singles["fr"]),
                     "violates_union_invariant": bool(bad),
                     "single_answers_absent_from_a_union": bad})
        if i % 20 == 0:
            print(f"  ... {i}/{len(flagged)}", flush=True)

    print(f"\n在并集语义答案集 A_union 上检验「单图答案 ⊆ 联合答案」："
          f"{len(flagged)} 条中违反 {viol_union} 条", flush=True)
    print("（现行代码在 split 语义的 A* 上检验，故全部触发；"
          "两种参考集的差异即复核要点。）", flush=True)

    out = {
        "run_dir": os.path.relpath(args.run_dir, PROJECT_ROOT),
        "n_ledger": len(ledger), "n_track_a": len(track_a),
        "n_conflict_invalid": len(flagged),
        "conflict_invalid_by_split_nonempty": {str(k): v
                                               for k, v in by_split.items()},
        "counterfactual_trichotomy": dict(tri),
        "violates_union_invariant": viol_union,
        "note": ("本文件只报告事实与反事实分布，不改变任何判定。"
                 "一致性检查应当以哪个答案集为参考，属方法学判断，"
                 "须由人工复核决定。"),
        "rows": rows,
    }
    path = args.out or os.path.join(args.run_dir,
                                    "review-conflict-invalid.json")
    util.write_json(path, out)
    print(f"已写出 {os.path.relpath(path, PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
