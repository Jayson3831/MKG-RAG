"""WP6：指定 question_id 的独立复核。

独立路径要求（任务书 §7 最小接口）：
- 不得只比较缓存标签，也不得调用同一判定函数返回成功。
- 本模块直接扫描**原始 .bz2 源文件**，用独立的简单三元组匹配重新求解，
  输出实际答案与原始证据行（源文件 + 未压缩行号）。
- 可共享 RDF 解析器与已核验语义规则，但执行路径与 graph.py 的索引无关。

实现方式：按跳数分批的「按需取数」扫描。每轮把当前 frontier 需要的
subject 汇总后扫描一次原始文件，取回这些 subject 的全部白名单三元组，
再推进一跳。轮数上限 = 最长链长，因此成本可预测，且中途不依赖任何
内存索引来决定「下一步该看哪里」——包括「哪些 subject 需要取」这件事
本身也完全由原始文件回读结果驱动。

批量模式：多个 question_id 在同一次扫描中一起处理，避免重复解压。
"""
from __future__ import annotations

import bz2
import os
from typing import Dict, List, Set, Tuple

from . import util
from .config import Config
from .graph import Alignment
from .prepare import raw_path

# 原始三联组的取数单位：subject -> [(pred_curie, object, kind, src_id, lineno)]
Facts = Dict[str, List[Tuple[str, str, str, str, int]]]


def _scan_into(cfg: Config, project_root: str, lang: str,
               want: Set[str], found: Facts) -> Dict:
    """扫描该语言全部 fact 源文件，把 want 中 subject 的白名单三元组并入 found。

    这是独立于 graph.py 的取数路径：不做索引、不做缓存标签，逐行读原始 .bz2。
    """
    rels = {util.expand(r) for r in cfg.raw["relations"]}
    remaining = set(want)
    stats = {"scanned_files": 0, "scanned_lines": 0, "matched_triples": 0}
    if not remaining:
        return stats
    # 预筛用具名集合 + 逐行切出主语，逐行成本 O(1)。
    # 早前用 line.startswith(tuple(prefixes)) 在 subject 上万时会退化成
    # O(行数 × subject 数)（每行都要试完所有前缀），几十万行乘上万前缀
    # 会让复核慢到不可用。
    wrapped = {f"<{t}>" for t in remaining}
    for f in cfg.raw["inputs"]:
        if f["role"] != "fact" or lang not in f["langs"]:
            continue
        src_id = f"{f['id']}_{lang}"
        path = raw_path(project_root, cfg.release_id, src_id)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        stats["scanned_files"] += 1
        with bz2.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                stats["scanned_lines"] += 1
                if not line or line[0] != "<":
                    continue
                # 快速预筛：subject 必须出现在行首
                end = line.find(">", 1)
                if end < 0 or line[:end + 1] not in wrapped:
                    continue
                t = util.parse_line(line)
                if t is None:
                    continue
                s, p, o, kind, _olang = t
                if s not in remaining or p not in rels:
                    continue
                found.setdefault(s, []).append((util.curie(p), o, kind, src_id, lineno))
                stats["matched_triples"] += 1
    return stats


def _targets(name: str, split: List[str], hop_index: int,
             langs: List[str]) -> List[str]:
    """该遍历在第 hop_index 跳应当从哪些图取事实。

    与 query_joint 保持一致：split 留空 = 并集语义（在每个图中分别求解），
    否则第 i 跳只从 split[i] 指定的图取。
    """
    if name != "joint":
        return [name[len("single_"):]]
    return list(langs) if not split else [split[hop_index]]


def _lang_of(name: str, path: List[Dict], root_lang: str) -> str:
    """节点当前所属图：路径非空取末跳来源图，否则为根实体所在图。"""
    if path:
        return path[-1]["graph"]
    return root_lang if name == "joint" else name[len("single_"):]


def _resolve(node: str, cur_lang: str, gl: str, align: Alignment,
             bridges: List[Dict]):
    """把节点换身份到目标图。返回 (节点, bridges, 是否映射缺失)。"""
    if cur_lang == gl:
        return node, bridges, False
    tgt = align.translate(node, cur_lang, gl)
    if tgt is None:
        return None, bridges, True
    return tgt, bridges + [{"lang": gl, "iri": tgt, "from_lang": cur_lang}], False


def _advance(frontier: List[Tuple], hop: str, lang: str,
             found: Facts) -> List[Tuple]:
    """在已取回的三元组上推进一跳。frontier 元素为 (node, path, bridges)。"""
    nxt = []
    for node, path, bridges in frontier:
        for pred, obj, kind, src, ln in found.get(node, ()):
            if pred != hop or kind != "iri":
                continue          # 字面量不作实体路径中间点
            nxt.append((obj,
                        path + [{"graph": lang, "s": node, "p": pred, "o": obj,
                                 "src": src, "lineno": ln}],
                        bridges))
    return nxt


def verify_ids(cfg: Config, project_root: str, align: Alignment,
               ledger: List[Dict], ids: List[str]) -> Dict:
    """按 question_id 独立复核；返回逐条结果、扫描成本与汇总一致率。"""
    langs = cfg.languages
    want = set(ids)
    cands = [c for c in ledger if c["question_id"] in want]
    missing = want - {c["question_id"] for c in cands}

    # 每条候选维护若干条独立遍历：joint（按 split 跨图）与每条单图。
    # 元素 (node, path, bridges)；bridges 记录跨图换身份的位置。
    trav: Dict[str, Dict[str, List[Tuple]]] = {}
    max_len = 0
    for c in cands:
        chain, rl = c["chain"], c["root_lang"]
        root = c["root_iris"].get(rl)
        if root is None:
            trav[c["question_id"]] = {}
            continue
        d = {"joint": [(root, [], [])]}
        for l in langs:
            ri = c["root_iris"].get(l)
            if ri is not None:
                d[f"single_{l}"] = [(ri, [], [])]
        trav[c["question_id"]] = d
        max_len = max(max_len, len(chain))

    found: Facts = {l: {} for l in langs}
    # 已取过数的 subject 单独记账：无匹配三元组的 subject 不会出现在 found 里，
    # 若用它判断「是否已取过」会导致每轮重复解压整个文件。
    fetched: Dict[str, Set[str]] = {l: set() for l in langs}
    scan_stats: List[Dict] = []
    bridge_missing: Dict[str, int] = {c["question_id"]: 0 for c in cands}

    for hop_index in range(max_len):
        # --- 1) 汇总本轮需要取数的 subject（含跨图换身份后的目标实体）---
        pending: Dict[str, Set[str]] = {l: set() for l in langs}
        for c in cands:
            qid = c["question_id"]
            chain, split = c["chain"], c["split"]
            if hop_index >= len(chain):
                continue
            for name, frontier in trav[qid].items():
                if not frontier:
                    continue
                for gl in _targets(name, split, hop_index, langs):
                    for node, path, bridges in frontier:
                        cur = _lang_of(name, path, c["root_lang"])
                        nd, _br, missed = _resolve(node, cur, gl, align, bridges)
                        if missed or nd in fetched[gl]:
                            continue
                        pending[gl].add(nd)
        # --- 2) 扫描原始文件取数 ---
        for l in langs:
            if pending[l]:
                st = _scan_into(cfg, project_root, l, pending[l], found[l])
                fetched[l] |= pending[l]
                if st["scanned_files"]:
                    scan_stats.append({"round": hop_index + 1, "lang": l, **st})

        # --- 3) 推进一跳 ---
        for c in cands:
            qid = c["question_id"]
            chain, split = c["chain"], c["split"]
            if hop_index >= len(chain):
                continue
            hop = chain[hop_index]
            for name in list(trav[qid].keys()):
                frontier = trav[qid][name]
                if not frontier:
                    continue
                moved: List[Tuple] = []
                for gl in _targets(name, split, hop_index, langs):
                    for node, path, bridges in frontier:
                        cur = _lang_of(name, path, c["root_lang"])
                        nd, br, missed = _resolve(node, cur, gl, align, bridges)
                        if missed:
                            # 映射缺失 -> 未决，不得当作「单图不存在该事实」
                            bridge_missing[qid] += 1
                            continue
                        moved += _advance([(nd, path, br)], hop, gl, found[gl])
                trav[qid][name] = moved

    # --- 4) 汇总 ---
    results = []
    n_match = 0
    for c in cands:
        qid = c["question_id"]
        d = trav.get(qid, {})
        joint = d.get("joint", [])
        ans_joint = sorted({n for n, _, _ in joint})
        rec = {"question_id": qid, "template_id": c["template_id"],
               "recorded_state": c.get("state"),
               "recorded_answers_joint": sorted(c.get("answers_joint", [])),
               "chain": c["chain"], "split": c["split"],
               "root_lang": c["root_lang"], "root_iris": c["root_iris"],
               "replayed_answers_joint": ans_joint,
               "replayed_paths_joint": [p for _, p, _ in joint][:20],
               "bridge_missing": bridge_missing.get(qid, 0)}
        for l in langs:
            rec[f"replayed_answers_{l}"] = sorted(
                {n for n, _, _ in d.get(f"single_{l}", [])})
        rec["matches_recorded_joint"] = (
            ans_joint == sorted(c.get("answers_joint", [])))
        if rec["matches_recorded_joint"]:
            n_match += 1
        results.append(rec)

    return {
        "requested": sorted(ids), "found": len(results),
        "missing_ids": sorted(missing),
        "n_matches_recorded_joint": n_match,
        "agreement_rate": (n_match / len(results)) if results else None,
        "scan_stats": scan_stats,
        "method": ("按需取数：逐跳汇总 subject 后直读原始 .bz2，"
                   "不使用 graph.py 的内存索引，也不比较缓存标签"),
        "results": results,
    }
