"""WP5：必要性复核与候选分类。

互斥状态：strict_join / completion / single_graph / insufficient /
conflict_invalid / unresolved（任务书 §7.7）。

硬性约束：
- A* 非空且各单图结果均为空才可计入 strict_join。
- 单图答案不属于 A* 时触发一致性错误，不继续发布必要率（任务书 §7.8）。
- 映射缺失、查询超时不计为「不可回答证明」，单列 unresolved。
- 替代路径结论只覆盖已实现且完整执行的规则集；穷举被预算截断时
  必须标记 executed_truncated，不得声称已排除全部替代路径。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .config import Config
from .graph import Alignment, Graph, query_joint, query_single

STATES = ("strict_join", "completion", "single_graph", "insufficient",
          "conflict_invalid", "unresolved")

# 替代链中逆向跳的前缀记号，与配置里正向 curie（如 dbo:birthPlace）区分
REV = "-"


def _step_fwd(graph: Graph, nodes: Set[str], rel_iri: str) -> Set[str]:
    out = set()
    for n in nodes:
        for rec in graph.out(n, rel_iri):
            if rec[1] == "iri":
                out.add(rec[0])
    return out


def _step_rev(graph: Graph, nodes: Set[str], rel_iri: str) -> Set[str]:
    out = set()
    for n in nodes:
        for rec in graph.incoming(n, rel_iri):
            out.add(rec[0])
    return out


def _live_chains(graph: Graph, root: str, rels: List[str], min_len: int,
                 max_len: int, max_expansions: int):
    """在单图内枚举所有「活跃」替代链。

    采用按前缀剪枝的 BFS：某前缀若求解为空，其任何扩展也必为空，
    因此可安全剪掉。逆关系（反向遍历）一并枚举，覆盖逆关系重写。

    返回 (results, truncated)：
      results    [(chain, answers)]，chain 元素为 'dbo:x'（正向）或 '-dbo:x'（逆向）
      truncated  是否因 max_expansions 预算耗尽而截断
    """
    results: List[Tuple[List[str], Set[str]]] = []
    frontier: List[Tuple[Set[str], List[str]]] = [({root}, [])]
    expansions = 0
    truncated = False
    for _ in range(max_len):
        nxt: List[Tuple[Set[str], List[str]]] = []
        for nodes, hops in frontier:
            for rel in rels:
                for sign, moved in (("", _step_fwd(graph, nodes, rel)),
                                    (REV, _step_rev(graph, nodes, rel))):
                    expansions += 1
                    if expansions > max_expansions:
                        truncated = True
                        break
                    if not moved:
                        continue
                    # rel 与 graph 的谓词键同为 curie（如 dbo:birthPlace）
                    nh = hops + [sign + rel]
                    nxt.append((moved, nh))
                    if min_len <= len(nh) <= max_len:
                        results.append((nh, moved))
                if truncated:
                    break
            if truncated:
                break
        if truncated or not nxt:
            break
        frontier = nxt
    return results, truncated


def _target_class(cfg: Config, chain: List[str]) -> Optional[str]:
    """模板末跳的期望客体类型，用于替代链的类型兼容检查。"""
    rel_meta = cfg.raw["relations"].get(chain[-1]) if chain else None
    return (rel_meta or {}).get("object_class")


def judge_candidate(cfg: Config, graphs: Dict[str, Graph], align: Alignment,
                    cand: Dict, root_iris: Dict[str, Optional[str]]) -> Dict:
    """判定单个候选，返回判定结果字典。

    root_iris: {lang: 该语言图内的根实体 IRI 或 None}（由参考映射解析）。
    """
    chain = cand["chain"]
    split = cand["split"]
    rels = list(cfg.raw["relations"])
    ap = cfg.raw["alternative_paths"]
    rc = ap["relational_chain_search"]
    search_on = rc["enabled"]
    extra = rc["extra_hops"]
    max_len = rc["max_chain_len"]
    max_exp = rc["max_expansions"]
    want_class = _target_class(cfg, chain)

    root_lang = cand["root_lang"]
    root = root_iris.get(root_lang)
    out: Dict = {"question_id": cand["question_id"], "state": None,
                 "answers_joint": [], "answers_en": [], "answers_fr": [],
                 "unresolved_branches": 0, "alt_path_hits": [],
                 "alt_check_status": "not_run", "alt_check_truncated": False,
                 "consistency_error": None}

    if root is None:
        out["state"] = "unresolved"
        out["reason"] = "根实体未通过参考映射解析到起始语言图"
        return out

    # --- 联合查询 ---
    a_star, jrecs, unresolved = query_joint(graphs, align, root_lang, root, chain, split)
    # 每个答案的来源图取自其证据路径末跳；并集语义下答案可能来自不同图。
    ans_lang_of = {a: (p[-1][0] if p else root_lang) for a, p, _ in jrecs}
    ans_lang_joint = split[-1] if split else None
    # 跨图比较必须在同一身份空间进行：同一实体在两图有不同本地 URI，
    # 直接比较原 IRI 会让「单图足以回答」永远判不成立（任务书 §7.4）。
    a_star_c = {align.canon(ans_lang_of.get(x, root_lang), x) for x in a_star}
    out["answers_joint"] = sorted(a_star)
    out["answers_joint_canon"] = sorted(a_star_c)
    out["answers_joint_lang"] = ans_lang_joint
    out["unresolved_branches"] = unresolved
    out["joint_records"] = [
        {"answer": a, "path": [{"graph": g, "s": s, "p": p,
                                "o": e[0], "src": e[3], "lineno": e[4]}
                               for g, s, p, e in path],
         "bridges": br}
        for a, path, br in jrecs
    ]

    # --- 单图查询（起点必须解析为该图本地实体；未解析即为 unresolved 分支）---
    singles: Dict[str, Set[str]] = {}
    singles_c: Dict[str, Set[str]] = {}
    for lang in cfg.languages:
        ri = root_iris.get(lang)
        if ri is None:
            singles[lang] = set()
            singles_c[lang] = set()
            out[f"root_missing_{lang}"] = True
            continue
        ans, paths = query_single(graphs[lang], ri, chain)
        singles[lang] = ans
        singles_c[lang] = {align.canon(lang, x) for x in ans}
        out[f"answers_{lang}"] = sorted(ans)
        out[f"answers_{lang}_canon"] = sorted(singles_c[lang])
        out[f"single_paths_{lang}"] = [
            [{"graph": g, "s": s, "p": p, "o": e[0],
              "src": e[3], "lineno": e[4]} for g, s, p, e in path]
            for path in paths
        ]

    # --- 一致性检查：单图答案必须属于 A*（同身份空间比较）---
    for lang, ans_c in singles_c.items():
        if ans_c and a_star_c and not ans_c.issubset(a_star_c):
            out["consistency_error"] = (
                f"{lang} 单图答案不属于联合答案集合，触发一致性错误，"
                f"按任务书 §7.8 不发布必要率，需复核")
            out["state"] = "conflict_invalid"
            return out

    if not a_star:
        # 联合仍无证据：区分「确实不足」与「映射缺失导致的未决」
        out["state"] = "unresolved" if unresolved else "insufficient"
        out["reason"] = ("联合查询无答案且有映射缺失分支" if unresolved
                         else "联合查询无答案")
        return out

    # --- 替代路径复核（在判定 strict_join 之前必须执行）---
    alt_hits: List[Dict] = []
    any_truncated = False
    if search_on:
        min_len = len(chain)
        up = min(max_len, len(chain) + extra)
        for lang in cfg.languages:
            ri = root_iris.get(lang)
            if ri is None:
                continue
            chains, truncated = _live_chains(graphs[lang], ri, rels,
                                             min_len, up, max_exp)
            any_truncated = any_truncated or truncated
            for alt_chain, ans in chains:
                if alt_chain == chain:
                    continue          # 模板链本身已由单图查询覆盖
                # 类型兼容：以实例类型实证检查替代链的实际答案是否具备模板
                # 声明的目标类型，而不是只比较关系元数据（逆向跳无客体类型可查）。
                if rc.get("require_type_compatible") and want_class:
                    typed = {x for x in ans if graphs[lang].has_class(x, want_class)}
                    if not typed:
                        continue
                    ans = typed
                if a_star_c.issubset({align.canon(lang, x) for x in ans}):
                    alt_hits.append({"lang": lang, "chain": alt_chain,
                                     "n_answers": len(ans),
                                     "answers": sorted(ans)[:20]})
        out["alt_check_status"] = ("executed_truncated" if any_truncated
                                   else "executed")
        out["alt_check_truncated"] = any_truncated
        out["alt_path_hits"] = alt_hits[:20]
        out["alt_paths_searched"] = True
    else:
        out["alt_check_status"] = "not_implemented"

    # --- 分类（互斥）---
    a_en_c, a_fr_c = singles_c.get("en", set()), singles_c.get("fr", set())
    out["partial_single_answers"] = sorted((a_en_c | a_fr_c) & a_star_c)
    if a_en_c >= a_star_c or a_fr_c >= a_star_c:
        out["state"] = "single_graph"
        out["reason"] = "至少一个单图足以完整支持 A*"
    elif alt_hits:
        # 单图原链为空或不全，但经允许的替代路径可完整回答 -> 单图可回答
        out["state"] = "single_graph"
        out["reason"] = ("单图经替代路径可完整支持 A*: "
                         + ", ".join(f"{h['lang']}:{'/'.join(h['chain'])}"
                                     for h in alt_hits[:3]))
    elif out["partial_single_answers"]:
        out["state"] = "completion"
        out["reason"] = "各单图均不能给出完整 A*，但至少一个单图给出部分答案"
    else:
        out["state"] = "strict_join"
        out["reason"] = ("A* 非空且各单图结果均为空，替代路径已复核无命中"
                         + ("（穷举被预算截断，结论不完整）" if any_truncated else ""))
    return out
