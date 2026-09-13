"""WP3/WP6：指标计算与从账本复算。

复算只读取已保存的候选 / 查询 / 审计记录，不调用判定函数重跑查询，
以便与原始运行结果对账（任务书 §7最小接口 recompute）。

口径要点：
- 轨道 A 主分母 = 预先采样且语法/类型有效的全部候选，含联合证据不足者。
- 空分母输出 null，不输出 0，避免误导。
- 未核验样本不得默认为负例；未决项报告覆盖率与上下界。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import util
from .config import Config

STATES = ("strict_join", "completion", "single_graph", "insufficient",
          "conflict_invalid", "unresolved")


def _ratio(num: int, den: int) -> Optional[float]:
    """空分母返回 None（序列化为 null），不返回 0.0。"""
    if den == 0:
        return None
    return num / den


def track_a_metrics(cands: List[Dict]) -> Dict:
    """轨道 A：必要问题发生率。

    R_strict      = strict_join / 全部有效候选
    R_completion  = completion  / 全部有效候选
    R_multi       = R_strict + R_completion
    另报告「联合可回答候选中的必要率」，条件分母明确。
    """
    valid = [c for c in cands if c.get("syntax_type_valid", True)]
    counts = {s: 0 for s in STATES}
    unresolved_kinds: Dict[str, int] = {}
    for c in valid:
        st = c.get("state")
        if st in counts:
            counts[st] += 1
        if st == "unresolved":
            k = c.get("unresolved_kind") or "unspecified"
            unresolved_kinds[k] = unresolved_kinds.get(k, 0) + 1
    den = len(valid)
    joint_answerable = counts["strict_join"] + counts["completion"] + counts["single_graph"]
    unfinished = counts["unresolved"]
    return {
        "denominator_valid_candidates": den,
        "n_raw_candidates": len(cands),
        "n_excluded_syntax_type_invalid": len(cands) - den,
        "state_counts": counts,
        "R_strict": _ratio(counts["strict_join"], den),
        "R_completion": _ratio(counts["completion"], den),
        "R_multi": _ratio(counts["strict_join"] + counts["completion"], den),
        "R_multi_among_joint_answerable": _ratio(
            counts["strict_join"] + counts["completion"], joint_answerable),
        "R_multi_among_joint_answerable_denominator": joint_answerable,
        "unfinished_unresolved": unfinished,
        "unresolved_by_kind": unresolved_kinds,
        "verification_coverage": _ratio(den - unfinished, den),
        # 未决项既非正例也非负例：给上下界
        "R_multi_lower_bound_excluding_unresolved": _ratio(
            counts["strict_join"] + counts["completion"], den),
        "R_multi_upper_bound_treating_unresolved_as_positive": _ratio(
            counts["strict_join"] + counts["completion"] + unfinished, den),
        "note": ("空分母为 null。未决项未计入正例，上下界在未决全部核实后收敛。"
                 "该比率是给定采样分布下的发生率，不能外推为自然用户问题分布。"),
    }


def track_b_metrics(tb: Dict) -> Dict:
    return {
        "attempts": tb["attempts"],
        "budget": tb["budget"],
        "reached_budget": tb["reached_budget"],
        "n_qualified": tb["n_qualified"],
        "n_failed": tb["n_failed"],
        "pass_rate": tb["pass_rate"],                      # 空分母 -> null
        "pass_rate_denominator": tb["pass_rate_denominator"],
        "note": tb["note"],
    }


def population_overlap(graphs: Dict, align, pop: Dict, langs: List[str]) -> Dict:
    """实体总体重合估计。

    做法：对每种语言，以其**全部实体**（未经任何共享条件筛选）构造身份集合，
    再用另一语言的独立总体样本检查命中率。分母是对侧全量集合而非对侧样本，
    因此该比率估计的是「某语言实体出现在另一语言的概率」，不受两侧样本量
    不等或抽样比不同的影响。

    映射缺失的实体只能落在自身语言一侧，会拉低命中率，故同时报告
    有映射样本上的命中率，避免把「映射没覆盖」误读成「实体不重合」。
    """
    full = {}
    for l in langs:
        full[l] = {align.canon(l, x) for x in graphs[l].subjects()}
    out = {
        "estimator": "对侧全量身份集合 + 本语言独立总体样本",
        "full_identity_set_sizes": {l: len(full[l]) for l in langs},
        "note": ("不使用已按共享条件筛选的实体池。映射覆盖率低时，"
                 "含未映射实体的比率会被低估，须与本字段一并解读。"),
    }
    for l in langs:
        others = [o for o in langs if o != l]
        if not others:
            continue
        other = others[0]
        s = list(pop[l]["sample"])
        mapped = [x for x in s if align.qid(l, x)]
        hit = sum(1 for x in s if align.canon(l, x) in full[other])
        hit_mapped = sum(1 for x in mapped
                         if align.canon(l, x) in full[other])
        out[f"{l}_in_{other}"] = {
            "sample_size": len(s),
            "sample_with_reference_mapping": len(mapped),
            "sample_present_in_other": hit,
            "containment_rate": _ratio(hit, len(s)),
            "containment_rate_among_mapped": _ratio(hit_mapped, len(mapped)),
            "containment_rate_denominator": len(s),
            "containment_rate_among_mapped_denominator": len(mapped),
        }
    return out


def overlap_metrics(cfg: Config, pop: Dict, align_stats: Dict,
                    graphs_meta: Dict, pop_overlap: Optional[Dict] = None) -> Dict:
    """实体/Schema/事实重合。分母均明确给出，未映射项单列。"""
    en = set(pop["en"]["sample"])
    fr = set(pop["fr"]["sample"])
    return {
        "entity_population_sample": {
            "en_total_entities": pop["en"]["total_entities"],
            "fr_total_entities": pop["fr"]["total_entities"],
            "en_sample": len(en), "fr_sample": len(fr),
            "sample_seed": cfg.raw["sampling"]["population_sample_seed"],
            "note": ("总体重合率须用完整实体清单或独立总体样本计算，"
                     "不得在已筛选的共享实体上估计（验证计划 §2.3）。"),
        },
        "entity_population_overlap": pop_overlap,
        "alignment_coverage": align_stats,
        "graph_scale": graphs_meta,
    }


def diversity_metrics(cands: List[Dict]) -> Dict:
    """实体/模板/桥接分布、长尾与重复率。"""
    from collections import Counter
    roots = Counter(c["root_qid"] for c in cands)
    tmpl = Counter(c["template_id"] for c in cands)
    strat = Counter(c.get("root_stratum") for c in cands)
    n = len(cands)
    return {
        "n_candidates": n,
        "distinct_roots": len(roots),
        "distinct_templates": len(tmpl),
        "template_distribution": dict(tmpl.most_common()),
        "stratum_distribution": {k: v for k, v in strat.items() if k},
        "max_candidates_per_root": max(roots.values()) if roots else 0,
        "duplicate_rate": _ratio(n - len(roots), n),
        "note": ("重复实体/模板会造成样本相关性，置信区间须采用分层或按实体聚类估计，"
                 "不把高度重复的问题当成独立证据。单纯更换问题措辞不计入模板多样性。"),
    }


def recompute(cfg: Config, ledger: List[Dict]) -> Dict:
    """从 candidates.jsonl 账本重算全部统计。"""
    a = [c for c in ledger if c.get("track") == "A"]
    b = [c for c in ledger if c.get("track") == "B"]
    out = {
        "recomputed_at": util.now_iso(),
        "source": "candidates.jsonl",
        "track_a": track_a_metrics(a),
        "track_a_diversity": diversity_metrics(a),
        "track_b": {
            "attempts": len(b),
            "n_qualified": sum(1 for c in b if c.get("state") in ("strict_join", "completion")),
            "n_failed": sum(1 for c in b if c.get("state") not in ("strict_join", "completion")),
        },
    }
    out["track_b"]["pass_rate"] = _ratio(out["track_b"]["n_qualified"],
                                         out["track_b"]["attempts"])
    out["track_b"]["pass_rate_denominator"] = out["track_b"]["attempts"]
    return out
