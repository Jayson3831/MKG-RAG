"""WP4：轨道 A（发生率估计）与轨道 B（可构造样本挖掘）候选生成。

两轨分母独立，不得混用（任务书 §3、§5）：
- 轨道 A 的主分母 = 预先采样且语法/类型有效的全部候选，含联合证据不足者。
  语法错误与类型不合法的排除条件在查询前定义；查询返回空不得转记为「无效」。
- 轨道 B 衡量构造效率与样本产量，记录每次尝试与失败原因，不称天然必要率。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .config import Config
from .graph import Graph


def template_chain(t: Dict) -> Tuple[List[str], List[str]]:
    return list(t["chain"]), list(t.get("split", []))


def root_lang_of(t: Dict, langs: List[str]) -> str:
    """根实体所属图：模板 split 的首跳来源图。"""
    sp = t.get("split")
    return sp[0] if sp else langs[0]


def type_valid(graphs: Dict[str, Graph], t: Dict, root_iris: Dict[str, Optional[str]]) -> bool:
    """语法/类型有效性：根实体在起始图内具有模板声明的根类型。

    仅检查类型；查询返回空不算无效（任务书 §7.6）。
    """
    rc = t.get("root_class")
    if not rc:
        return True
    rl = root_lang_of(t, list(graphs))
    ri = root_iris.get(rl)
    if ri is None:
        return False
    return graphs[rl].has_class(ri, rc)


def make_candidate(track: str, t: Dict, ent: Dict, idx: int,
                   langs: List[str]) -> Dict:
    chain, split = template_chain(t)
    rl = root_lang_of(t, langs)
    return {
        "question_id": f"{track}-{t['id']}-{ent['qid']}-{idx}",
        "track": track,
        "template_id": t["id"],
        "template_hops": t["hops"],
        "chain": chain,
        "split": split,
        "root_lang": rl,
        "root_qid": ent["qid"],
        "root_iris": ent["iris"],
        "root_stratum": ent.get("stratum"),
        "root_degree": ent.get("degree"),
        "sampling_probability": ent.get("sampling_probability"),
        "question_template": t["question"],
    }


def dedup_key(c: Dict) -> Tuple:
    """去重键：模板 + 根实体 QID。重复实体/模板造成的相关性在统计时按实体聚类处理。"""
    return (c["template_id"], c["root_qid"])


def track_a(cfg: Config, graphs: Dict[str, Graph], sampled: List[Dict]) -> Dict:
    """轨道 A：在预先采样的共享实体上，按固定模板生成候选。

    不要求跨图；这是给定采样分布下的发生率，不能外推为自然用户问题分布。
    """
    langs = cfg.languages
    budget = cfg.raw["budget"]["track_a_candidates"]
    templates = cfg.raw["templates"] + cfg.raw["completion_templates"]
    rnd = random.Random(cfg.raw["sampling"]["seed"] + 1000)
    order = list(sampled)
    rnd.shuffle(order)

    cands: List[Dict] = []
    seen = set()
    n_invalid = 0
    n_dup = 0
    per_template: Dict[str, int] = {}
    per_stratum: Dict[str, int] = {}
    i = 0
    for ent in order:
        for t in templates:
            if not type_valid(graphs, t, ent["iris"]):
                n_invalid += 1
                continue
            c = make_candidate("A", t, ent, i, langs)
            k = dedup_key(c)
            if k in seen:
                n_dup += 1
                continue
            seen.add(k)
            per_template[t["id"]] = per_template.get(t["id"], 0) + 1
            per_stratum[ent["stratum"]] = per_stratum.get(ent["stratum"], 0) + 1
            cands.append(c)
            i += 1
            if len(cands) >= budget:
                break
        if len(cands) >= budget:
            break

    return {
        "candidates": cands,
        "sampled_roots": len(order),
        "n_candidates": len(cands),
        "n_invalid_type": n_invalid,
        "n_duplicate": n_dup,
        "target": budget,
        "reached_target": len(cands) >= budget,
        "per_template": per_template,
        "per_stratum": per_stratum,
        "seed": cfg.raw["sampling"]["seed"] + 1000,
    }


def track_b(cfg: Config, graphs: Dict[str, Graph], pool: List[Dict],
            judge_fn) -> Dict:
    """轨道 B：定向挖掘跨图路径。

    每次尝试登记；达到预算上限即结束，报告实际产量。judge_fn(candidate, idx)
    返回判定结果。合格 = strict_join 或 completion（跨图必要性成立）。
    """
    langs = cfg.languages
    budget = cfg.raw["budget"]["track_b_attempts"]
    templates = cfg.raw["templates"] + cfg.raw["completion_templates"]
    rnd = random.Random(cfg.raw["sampling"]["seed"] + 2000)
    order = list(pool)
    rnd.shuffle(order)

    attempts = 0
    kept: List[Dict] = []
    failures: List[Dict] = []
    n_invalid = 0
    seen = set()
    for ent in order:
        for t in templates:
            if attempts >= budget:
                break
            if not type_valid(graphs, t, ent["iris"]):
                n_invalid += 1
                continue
            c = make_candidate("B", t, ent, attempts, langs)
            k = dedup_key(c)
            if k in seen:
                continue
            seen.add(k)
            attempts += 1
            res = judge_fn(c, attempts)
            if res["state"] in ("strict_join", "completion"):
                kept.append({**c, **res})
            else:
                failures.append({"question_id": c["question_id"],
                                 "template_id": t["id"], "root_qid": ent["qid"],
                                 "state": res["state"],
                                 "reason": res.get("reason", "")})
        if attempts >= budget:
            break

    return {
        "attempts": attempts,
        "budget": budget,
        "reached_budget": attempts >= budget,
        "qualified": kept,
        "n_qualified": len(kept),
        "n_failed": len(failures),
        "n_invalid_type": n_invalid,
        "failures_sample": failures[:200],
        "pass_rate": (len(kept) / attempts) if attempts else None,
        "pass_rate_denominator": attempts,
        "note": "合格数/尝试数，衡量构造效率；不是天然必要率",
    }
