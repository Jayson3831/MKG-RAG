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


def _step_fwd(graph: Graph, nodes: Set[str], rel: str) -> Set[str]:
    out = set()
    for n in nodes:
        for rec in graph.out(n, rel):
            if rec[1] == "iri":
                out.add(rec[0])
    return out


def _step_rev(graph: Graph, nodes: Set[str], rel: str) -> Set[str]:
    out = set()
    for n in nodes:
        for rec in graph.incoming(n, rel):
            out.add(rec[0])
    return out


def _live_chains(graph: Graph, root: str, rels: List[str], min_len: int,
                 max_len: int, max_expansions: int, max_node_visits: int):
    """在单图内枚举所有「活跃」替代链。

    采用按前缀剪枝的 BFS：某前缀若求解为空，其任何扩展也必为空，
    因此可安全剪掉。逆关系（反向遍历）一并枚举，覆盖逆关系重写。

    返回 (results, truncated, stats)：
      results    [(chain, answers)]，chain 元素为 'dbo:x'（正向）或 '-dbo:x'（逆向）
      truncated  是否因预算耗尽而截断
      stats      实际消耗，供审核判断结论是否完整
    """
    results: List[Tuple[List[str], Set[str]]] = []
    frontier: List[Tuple[Set[str], List[str]]] = [({root}, [])]
    expansions = 0
    node_visits = 0
    truncated = False
    for _ in range(max_len):
        nxt: List[Tuple[Set[str], List[str]]] = []
        for nodes, hops in frontier:
            for rel in rels:
                for sign, step in (("", _step_fwd), (REV, _step_rev)):
                    # 预算检查必须在真正展开之前：单次展开若落在超大节点集合上，
                    # 事后计数无法阻止它跑完。
                    expansions += 1
                    node_visits += len(nodes)
                    if (expansions > max_expansions
                            or node_visits > max_node_visits):
                        truncated = True
                        break
                    moved = step(graph, nodes, rel)
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
    return results, truncated, {"expansions": expansions,
                                "node_visits": node_visits}


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
                 "unresolved_kind": None, "consistency_error": None,
                 # 提前返回的分支（未决/冲突）也必须带该字段，否则账本字段缺失，
                 # 下游按字段存在性区分路径就会误判。
                 "partial_single_answers": []}

    if root is None:
        out["state"] = "unresolved"
        out["unresolved_kind"] = "root_not_resolved"
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
        out["unresolved_kind"] = "mapping_missing" if unresolved else None
        out["reason"] = ("联合查询无答案且有映射缺失分支" if unresolved
                         else "联合查询无答案")
        return out

    # --- 替代路径复核（在判定 strict_join 之前必须执行）---
    alt_hits: List[Dict] = []
    alt_stats: Dict[str, Dict] = {}
    any_truncated = False
    a_en_c, a_fr_c = singles_c.get("en", set()), singles_c.get("fr", set())
    direct_single = a_en_c >= a_star_c or a_fr_c >= a_star_c

    # O(1) 预筛探针：任何命中的替代链都必须至少包含 A* 中的一个答案节点，
    # 故先取一个 A* 答案在该图内的本地 IRI 作探针，逐链先做集合成员判断。
    # 否则在答案集很大时（实测最重候选单次穷举保留约 1000 万节点），
    # 每条替代链都要整体扫一遍做类型过滤与身份归一，等于白扫上千万个节点。
    probes: Dict[str, Optional[str]] = {}
    for lang in cfg.languages:
        probe = None
        for x in sorted(a_star_c):
            loc = align.by_qid.get(x, {}).get(lang)
            # 仅当该本地 IRI 归一后确实回到 x 时才可作探针：若同一本地 IRI 被
            # 多个 QID 指涉，canon 未必回到 x，用它剪枝可能剪掉真命中。
            if loc is not None and align.canon(lang, loc) == x:
                probe = loc
                break
        probes[lang] = probe

    if search_on and not direct_single:
        # 某个单图已按模板链完整回答时，替代路径不可能改变分类，
        # 跳过穷举以把预算留给真正需要它的候选（否则纯属浪费）。
        min_len = len(chain)
        up = min(max_len, len(chain) + extra)
        for lang in cfg.languages:
            ri = root_iris.get(lang)
            if ri is None:
                continue
            chains, truncated, st = _live_chains(graphs[lang], ri, rels,
                                                 min_len, up, max_exp,
                                                 rc["max_node_visits"])
            any_truncated = any_truncated or truncated
            alt_stats[lang] = st
            probe = probes.get(lang)
            for alt_chain, ans in chains:
                if alt_chain == chain:
                    continue          # 模板链本身已由单图查询覆盖
                # 预筛是必要条件，不满足即不可能命中，直接跳过整条链的后续开销
                if probe is not None and probe not in ans:
                    continue
                # 类型兼容：以实例类型实证检查替代链的实际答案是否具备模板
                # 声明的目标类型，而不是只比较关系元数据（逆向跳无客体类型可查）。
                if rc.get("require_type_compatible") and want_class:
                    if probe is not None and not graphs[lang].has_class(probe, want_class):
                        continue      # 探针答案本身不带目标类型，整条链不可能命中
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
        out["alt_search_cost"] = alt_stats
    elif not search_on:
        out["alt_check_status"] = "not_implemented"
    else:
        # 单图已直接完整回答，无需穷举；不是「已排除全部替代路径」的断言
        out["alt_check_status"] = "not_needed_direct_single_graph"
        out["alt_paths_searched"] = False

    # --- 分类（互斥）---
    out["partial_single_answers"] = sorted((a_en_c | a_fr_c) & a_star_c)
    if direct_single:
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
    elif any_truncated:
        # 判 strict_join 依据的是「替代路径穷举未命中」这一**否定**结论。
        # 穷举被预算截断即表示该检查未完整执行，任务书 §7.5 要求结论只覆盖
        # 实现且完整执行的规则集，故不得据此断订单图必要——记为未决而非正例。
        # 注意：命中类结论（single_graph / completion）是存在性证据，不受截断影响。
        out["state"] = "unresolved"
        out["unresolved_kind"] = "alt_search_budget_exhausted"
        out["reason"] = ("A* 非空且各单图结果均为空，但替代路径穷举被预算截断"
                         "（未完整执行），无法据此确认单图必要，记为未决")
    else:
        # 到达此分支必然满足 singles_all_empty：若某单图答案非空而它与 A* 无交集，
        # 则该答案不属于 A*，上面的一致性检查已先行判为冲突并返回。这里不重复
        # 判一次（否则是永不执行的死代码），但 §7.7/§218 把「各单图为空」写成
        # 严格连接的必要条件，checks.check_strict_join_requires_empty_singles
        # 以可观测方式守住该不变量：一旦一致性检查的参考集被改动，它会失败。
        out["state"] = "strict_join"
        out["reason"] = "A* 非空且各单图结果均为空，替代路径已复核完整执行且无命中"
    return out
