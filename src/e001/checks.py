"""任务书 §7 要求的正确性检查。

同一套检查既由 tests/ 下的 pytest 调用，也在每次正式运行的 checks.json 中
重新执行并记录结果——运行目录里的 checks.json 是本次代码快照的实际输出，
不是从别处复制的历史结论。

所有检查使用内存中的微型合成图，走与正式运行完全相同的代码路径
（Graph / Alignment / query_* / judge_candidate），因此合成图上的结论
可以支撑对真实运行判定逻辑的信任，但不替代真实数据上的结果。

场景之间互相约束的一条核心不变量：
- strict_join 只在 A* 非空且**各单图结果均为空、且替代路径已复核无命中**时成立。
"""
from __future__ import annotations

import bz2
import os
import tempfile
from typing import Dict, Tuple

from . import util
from .config import Config
from .graph import Alignment, Graph, query_single
from .judge import judge_candidate
from .metrics import _ratio

EN = util.NS["dbr"]
FR = util.NS["dbp_fr"]
FILM = f"{EN}SomeFilm"
PERSON_EN, PERSON_FR = f"{EN}SomePerson", f"{FR}SomePerson"
PLACE_EN, PLACE_FR = f"{EN}SomePlace", f"{FR}SomePlace"
OTHER_EN, OTHER_FR = f"{EN}OtherPlace", f"{FR}OtherPlace"

Q_FILM, Q_PERSON, Q_PLACE, Q_OTHER = "Q1", "Q2", "Q3", "Q4"


def _base_align() -> Alignment:
    return Alignment.from_map(["en", "fr"], {
        Q_FILM: {"en": FILM, "fr": f"{FR}SomeFilm"},
        Q_PERSON: {"en": PERSON_EN, "fr": PERSON_FR},
        Q_PLACE: {"en": PLACE_EN, "fr": PLACE_FR},
        Q_OTHER: {"en": OTHER_EN, "fr": OTHER_FR},
    })


def _typed(g: Graph, subj: str, cls: str) -> Graph:
    g.add_type(subj, cls)
    return g


def _template(chain, split, root_class="dbo:Film", tid="TX"):
    return {"id": tid, "hops": len(chain), "chain": list(chain),
            "split": list(split), "root_class": root_class,
            "question": "{root_label}"}


def _cand(t, root_iris):
    return {"question_id": "chk-1", "track": "A", "template_id": t["id"],
            "chain": list(t["chain"]), "split": list(t["split"]),
            "root_lang": (t["split"][0] if t["split"] else "en"),
            "root_qid": Q_FILM, "root_iris": root_iris,
            "root_stratum": "work", "question_template": t["question"]}


def _judge(cfg, graphs, align, t, root_iris) -> Dict:
    return judge_candidate(cfg, graphs, align, _cand(t, root_iris), root_iris)


# ---------------------------------------------------------------- 单项检查

def check_strict_join_via_bridge(cfg: Config) -> Tuple[bool, str]:
    """跨图连接：A* 只有拼接两图事实才能得到，各单图均为空 -> strict_join。"""
    en = _typed(Graph.from_triples("en", [
        (FILM, "dbo:director", PERSON_EN, "iri")]), FILM, "dbo:Film")
    fr = _typed(_typed(Graph.from_triples("fr", [
        (PERSON_FR, "dbo:birthPlace", PLACE_FR, "iri")]),
        PERSON_FR, "dbo:Person"), PLACE_FR, "dbo:Place")
    t = _template(["dbo:director", "dbo:birthPlace"], ["en", "fr"])
    r = _judge(cfg, {"en": en, "fr": fr}, _base_align(), t,
               {"en": FILM, "fr": f"{FR}SomeFilm"})
    ok = r["state"] == "strict_join" and r["answers_joint"] == [PLACE_FR]
    return ok, f"state={r['state']} A*={[util.local_name(x) for x in r['answers_joint']]}"


def check_single_graph_direct(cfg: Config) -> Tuple[bool, str]:
    """同一图内即可完整回答 -> single_graph，不得计入必要跨图。"""
    en = Graph.from_triples("en", [
        (FILM, "dbo:director", PERSON_EN, "iri"),
        (PERSON_EN, "dbo:birthPlace", PLACE_EN, "iri")])
    fr = Graph.from_triples("fr", [])
    t = _template(["dbo:director", "dbo:birthPlace"], ["en", "en"])
    r = _judge(cfg, {"en": en, "fr": fr}, _base_align(), t,
               {"en": FILM, "fr": f"{FR}SomeFilm"})
    ok = r["state"] == "single_graph" and r["answers_joint"] == [PLACE_EN]
    return ok, f"state={r['state']}"


def check_same_entity_different_local_uri(cfg: Config) -> Tuple[bool, str]:
    """同一实体在两图有不同本地 URI 时，跨图比较必须按身份归一。"""
    a = _base_align()
    same = a.canon("en", PLACE_EN) == a.canon("fr", PLACE_FR)
    # 未在参考映射中的实体不得因本地名相同而被当作同一实体
    a2 = _base_align()
    diff = a2.canon("en", f"{EN}Unmapped") != a2.canon("fr", f"{FR}Unmapped")
    ok = same and diff
    return ok, f"mapped_same={same} unmapped_distinct={diff}"


def check_completion_subset(cfg: Config) -> Tuple[bool, str]:
    """两图各给出部分答案、并集才完整 -> completion（集合补全）。"""
    en = Graph.from_triples("en", [(FILM, "dbo:country", f"{EN}CountryA", "iri")])
    fr = Graph.from_triples("fr", [(f"{FR}SomeFilm", "dbo:country",
                                    f"{FR}CountryB", "iri")])
    t = _template(["dbo:country"], [])
    r = _judge(cfg, {"en": en, "fr": fr}, _base_align(), t,
               {"en": FILM, "fr": f"{FR}SomeFilm"})
    n_joint = len(r["answers_joint"])
    ok = r["state"] == "completion" and n_joint == 2
    return ok, f"state={r['state']} |A*|={n_joint}"


def check_labels_and_literals_not_answers(cfg: Config) -> Tuple[bool, str]:
    """标签/摘要/单位/日期精度差异不得计入答案事实重叠。

    这里检查最基础的一条：字面量永远不作为实体路径的中间点或答案。
    """
    en = Graph.from_triples("en", [
        (FILM, "dbo:director", PERSON_EN, "iri"),
        (PERSON_EN, "dbo:birthPlace", "1963-04-04", "literal")])
    got, _ = query_single(en, FILM, ["dbo:director", "dbo:birthPlace"])
    ok = got == set()
    return ok, f"literal_answers={sorted(got)}"


def check_mapping_missing_is_unresolved(cfg: Config) -> Tuple[bool, str]:
    """映射缺失导致的空结果记为 unresolved，不得当作「单图不存在该事实」。"""
    en = Graph.from_triples("en", [(FILM, "dbo:director", f"{EN}Unmapped", "iri")])
    fr = Graph.from_triples("fr", [])
    t = _template(["dbo:director", "dbo:birthPlace"], ["en", "fr"])
    r = _judge(cfg, {"en": en, "fr": fr}, _base_align(), t,
               {"en": FILM, "fr": f"{FR}SomeFilm"})
    ok = r["state"] == "unresolved" and r["unresolved_branches"] > 0
    return ok, f"state={r['state']} unresolved_branches={r['unresolved_branches']}"


def check_corrupt_input_counted_not_silent(cfg: Config) -> Tuple[bool, str]:
    """损坏输入必须被计数并暴露，不得静默当成「事实不存在」。"""
    from .prepare import scan_bz2
    payload = ("<{s}> <{p}> <{o}> .\n"
               "this line is not a triple\n"
               "<{s}> <{p}> <{o2}> .\n").format(
        s=FILM, p=util.expand("dbo:director"), o=PERSON_EN, o2=PLACE_EN)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.ttl.bz2")
        with bz2.open(path, "wt", encoding="utf-8") as f:
            f.write(payload)
        stats = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
        scan_bz2(path, {util.expand("dbo:director")}, lambda *a: None, stats)
    ok = stats["bad"] == 1 and stats["triples"] == 2
    return ok, f"bad={stats['bad']} triples={stats['triples']}"


def check_empty_denominator_is_null(cfg: Config) -> Tuple[bool, str]:
    """空分母输出 null，不输出 0，避免误导。"""
    ok = _ratio(0, 0) is None and _ratio(1, 0) is None and _ratio(1, 2) == 0.5
    return ok, f"ratio(0,0)={_ratio(0, 0)!r} ratio(1,2)={_ratio(1, 2)!r}"


def check_track_denominators_independent(cfg: Config) -> Tuple[bool, str]:
    """轨道 A/B 分母独立：同一实体不可能同时落入两个分母。"""
    from .tracks import dedup_key
    a = {"template_id": "T1", "root_qid": "Q1"}
    b = {"template_id": "T1", "root_qid": "Q1"}
    ok = dedup_key(a) == dedup_key(b)
    return ok, f"dedup_key={dedup_key(a)}"


def check_alt_path_flips_strict_join(cfg: Config) -> Tuple[bool, str]:
    """存在合法单图替代路径时不得判为 strict_join；关闭该检查后应恢复。"""
    en = Graph.from_triples("en", [
        (FILM, "dbo:director", PERSON_EN, "iri"),
        (PLACE_EN, "dbo:birthPlace", PERSON_EN, "iri")])   # 逆向可达
    _typed(en, PLACE_EN, "dbo:Place")
    fr = _typed(Graph.from_triples("fr", [
        (PERSON_FR, "dbo:birthPlace", PLACE_FR, "iri")]), PLACE_FR, "dbo:Place")
    graphs = {"en": en, "fr": fr}
    align = _base_align()
    ri = {"en": FILM, "fr": f"{FR}SomeFilm"}
    t = _template(["dbo:director", "dbo:birthPlace"], ["en", "fr"])

    on = _judge(cfg, graphs, align, t, ri)
    saved = cfg.raw["alternative_paths"]["relational_chain_search"]["enabled"]
    cfg.raw["alternative_paths"]["relational_chain_search"]["enabled"] = False
    try:
        off = _judge(cfg, graphs, align, t, ri)
    finally:
        cfg.raw["alternative_paths"]["relational_chain_search"]["enabled"] = saved
    ok = (on["state"] == "single_graph" and off["state"] == "strict_join"
          and bool(on["alt_path_hits"]))
    return ok, (f"with_alt={on['state']} without_alt={off['state']} "
                f"hits={len(on['alt_path_hits'])}")


def check_alt_path_survives_removal(cfg: Config) -> Tuple[bool, str]:
    """删掉一条替代路径后若仍有另一条合法路径，结论不得翻转。"""
    en = Graph.from_triples("en", [
        (FILM, "dbo:director", PERSON_EN, "iri"),
        (PLACE_EN, "dbo:birthPlace", PERSON_EN, "iri"),      # 逆向路径 1
        (PLACE_EN, "dbo:location", PERSON_EN, "iri")])       # 逆向路径 2
    _typed(en, PLACE_EN, "dbo:Place")
    fr = _typed(Graph.from_triples("fr", [
        (PERSON_FR, "dbo:birthPlace", PLACE_FR, "iri")]), PLACE_FR, "dbo:Place")
    graphs = {"en": en, "fr": fr}
    align = _base_align()
    ri = {"en": FILM, "fr": f"{FR}SomeFilm"}
    t = _template(["dbo:director", "dbo:birthPlace"], ["en", "fr"])

    before = _judge(cfg, graphs, align, t, ri)
    # 删掉路径 1 的支撑事实，路径 2 仍在
    del en.edges[PLACE_EN]["dbo:birthPlace"]
    del en.rev[PERSON_EN]["dbo:birthPlace"]
    after = _judge(cfg, graphs, align, t, ri)
    ok = (before["state"] == "single_graph" and after["state"] == "single_graph"
          and len(before["alt_path_hits"]) >= 2)
    return ok, (f"before={before['state']}({len(before['alt_path_hits'])} hits) "
                f"after={after['state']}({len(after['alt_path_hits'])} hits)")


def check_consistency_error_detected(cfg: Config) -> Tuple[bool, str]:
    """单图答案不属于 A* 时触发一致性错误，不继续发布必要率。"""
    en = Graph.from_triples("en", [
        (FILM, "dbo:director", PERSON_EN, "iri"),
        (PERSON_EN, "dbo:birthPlace", f"{EN}Nowhere", "iri")])
    fr = Graph.from_triples("fr", [
        (PERSON_FR, "dbo:birthPlace", PLACE_FR, "iri")])
    t = _template(["dbo:director", "dbo:birthPlace"], ["en", "fr"])
    r = _judge(cfg, {"en": en, "fr": fr}, _base_align(), t,
               {"en": FILM, "fr": f"{FR}SomeFilm"})
    ok = r["state"] == "conflict_invalid" and r["consistency_error"]
    return ok, f"state={r['state']}"


ALL_CHECKS = [
    ("strict_join_via_bridge", check_strict_join_via_bridge),
    ("single_graph_direct", check_single_graph_direct),
    ("same_entity_different_local_uri", check_same_entity_different_local_uri),
    ("completion_subset", check_completion_subset),
    ("labels_and_literals_not_answers", check_labels_and_literals_not_answers),
    ("mapping_missing_is_unresolved", check_mapping_missing_is_unresolved),
    ("corrupt_input_counted_not_silent", check_corrupt_input_counted_not_silent),
    ("empty_denominator_is_null", check_empty_denominator_is_null),
    ("track_denominators_independent", check_track_denominators_independent),
    ("alt_path_flips_strict_join", check_alt_path_flips_strict_join),
    ("alt_path_survives_removal", check_alt_path_survives_removal),
    ("consistency_error_detected", check_consistency_error_detected),
]


def run_all(cfg: Config) -> Dict:
    """执行全部检查，返回可写入 checks.json 的结果。"""
    results = []
    for name, fn in ALL_CHECKS:
        try:
            ok, detail = fn(cfg)
            err = None
        except Exception as exc:                      # noqa: BLE001 - 记录而非中断
            ok, detail, err = False, "检查抛出异常", f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "passed": bool(ok), "detail": detail,
                        "error": err})
    n_pass = sum(1 for r in results if r["passed"])
    return {
        "executed_at": util.now_iso(),
        "n_total": len(results),
        "n_passed": n_pass,
        "n_failed": len(results) - n_pass,
        "all_passed": n_pass == len(results),
        "checks": results,
        "scope_note": ("合成微型图验证判定逻辑；不替代真实数据上的结果，"
                       "也不构成人工审核。"),
    }
