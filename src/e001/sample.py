"""WP4 抽样：共享实体分层抽样与独立总体样本。

要点（任务书 §2.2/§2.4）：
- 共享实体样本只用于桥接分析；实体总体重合率必须另用完整清单或独立总体样本
  计算，不能在已按重合条件筛选的共享实体上估计总体重合。
- 记录随机种子、各层规模、抽样概率与映射覆盖率；稀缺类别报告实际数量。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Set

from .config import Config
from .graph import Alignment, Graph


def _stratum_of(classes: Set[str], cfg: Config) -> Optional[str]:
    """按配置顺序返回首个命中的层名（互斥：取优先级最高者）。"""
    for s in cfg.raw["sampling"]["strata"]:
        if s["class"] in classes:
            return s["name"]
    return None


def build_shared_pool(cfg: Config, graphs: Dict[str, Graph],
                      align: Alignment) -> Dict:
    """构建同时存在于两图（且有白名单出边）的共享实体池，并按层划分。"""
    langs = cfg.languages
    pool = []
    n_qid_both = 0
    n_both_present = 0
    for qid, d in align.by_qid.items():
        if len(d) != len(langs):
            continue
        n_qid_both += 1
        iris = {l: d[l] for l in langs}
        if any(iris[l] not in graphs[l].edges for l in langs):
            continue
        n_both_present += 1
        classes = set()
        for l in langs:
            classes |= graphs[l].types.get(iris[l], set())
        st = _stratum_of(classes, cfg)
        if st is None:
            continue
        deg = sum(graphs[l].degree(iris[l]) for l in langs)
        pool.append({"qid": qid, "iris": iris, "stratum": st, "degree": deg,
                     "classes": sorted(classes)})
    return {"pool": pool, "n_qid_both": n_qid_both,
            "n_both_present": n_both_present,
            "n_stratified": len(pool)}


def stratified_sample(cfg: Config, pool: List[Dict], seed: int,
                      target: int) -> List[Dict]:
    """按层权重抽样；层内按连接度高低分档后各取一半。

    层内样本不足时全取并如实记录实际数量，不外推。
    """
    rnd = random.Random(seed)
    by_stratum: Dict[str, List[Dict]] = {}
    for r in pool:
        by_stratum.setdefault(r["stratum"], []).append(r)

    # 先按连接度中位数分档
    for name, rows in by_stratum.items():
        degs = sorted(x["degree"] for x in rows)
        med = degs[len(degs) // 2] if degs else 0
        for x in rows:
            x["degree_bucket"] = "high" if x["degree"] >= med else "low"

    picked: List[Dict] = []
    weights = {s["name"]: s["weight"] for s in cfg.raw["sampling"]["strata"]}
    total_w = sum(weights.get(k, 0) for k in by_stratum) or 1.0
    for name, rows in sorted(by_stratum.items()):
        quota = int(round(target * weights.get(name, 0) / total_w))
        lows = [x for x in rows if x["degree_bucket"] == "low"]
        highs = [x for x in rows if x["degree_bucket"] == "high"]
        take_lo = min(len(lows), quota // 2)
        take_hi = min(len(highs), quota - take_lo)
        sel = rnd.sample(lows, take_lo) + rnd.sample(highs, take_hi)
        # 若某档不足，从同层剩余补齐，但不跨层
        if len(sel) < quota:
            rest = [x for x in rows if x not in sel]
            sel += rnd.sample(rest, min(len(rest), quota - len(sel)))
        for x in sel:
            x = dict(x)
            x["sampling_probability"] = (len(sel) / len(rows)) if rows else None
            x["stratum_population"] = len(rows)
            picked.append(x)
    picked.sort(key=lambda x: (x["stratum"], x["qid"]))
    return picked


def population_sample(cfg: Config, graphs: Dict[str, Graph]) -> Dict:
    """独立总体样本：从各图全部实体独立抽样，估计实体总体重合率。

    不经过「共享」筛选，因此可用于估计总体 Jaccard（验证计划 §2.3）。
    """
    s = cfg.raw["sampling"]
    n = s["population_sample_size"]
    seed = s["population_sample_seed"]
    out = {}
    for lang in cfg.languages:
        subs = list(graphs[lang].edges.keys())
        rnd = random.Random(seed + (0 if lang == "en" else 1))
        take = min(n, len(subs))
        out[lang] = {"total_entities": len(subs), "sample_size": take,
                     "sample": rnd.sample(subs, take)}
    return out
