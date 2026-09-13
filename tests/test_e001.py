"""E001 正确性测试。

这些测试与正式运行中写入 checks.json 的是同一套检查（e001.checks），
因此测试通过意味着「运行目录里的 checks.json 应该全绿」，
反之亦然——两者不可能给出不同结论。
"""
from __future__ import annotations

import bz2
import os

import pytest

from e001 import checks as C
from e001 import util
from e001.config import ConfigError, load as load_config
from e001.graph import Alignment, Graph, query_joint, query_single
from e001.metrics import _ratio, diversity_metrics, track_a_metrics
from e001.sample import _stratum_of, build_shared_pool, stratified_sample
from e001.tracks import dedup_key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "configs", "e001.yaml")


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


@pytest.mark.parametrize("name,fn", C.ALL_CHECKS, ids=[n for n, _ in C.ALL_CHECKS])
def test_required_check(cfg, name, fn):
    ok, detail = fn(cfg)
    assert ok, f"{name} 失败: {detail}"


def test_checks_run_all_all_pass(cfg):
    res = C.run_all(cfg)
    failed = [c["name"] for c in res["checks"] if not c["passed"]]
    assert res["all_passed"], f"未通过: {failed}"


# ------------------------------------------------------------ 配置与口径

def test_config_frozen_expectations(cfg):
    assert cfg.release_id == "2022.03.01"
    assert cfg.languages == ["en", "fr"]
    assert cfg.raw["audit"]["human_status_initial"] == "pending"
    assert cfg.raw["metrics"]["empty_denominator_repr"] is None


def test_config_rejects_non_dbo_relation(tmp_path):
    raw = open(CONFIG, encoding="utf-8").read()
    bad = raw.replace("  dbo:director:        {object_class: dbo:Person}",
                      "  dbp:director:        {object_class: dbo:Person}")
    p = tmp_path / "bad.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_config_rejects_split_length_mismatch(tmp_path):
    raw = open(CONFIG, encoding="utf-8").read()
    bad = raw.replace("    split: [en, fr]          # hop1 取 en，hop2 取 fr",
                      "    split: [en]              # 长度不符")
    p = tmp_path / "bad.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(p))


# ------------------------------------------------------------ 查询语义

def test_joint_union_semantics(cfg):
    """split 为空 = 并集语义，答案来自多个图。"""
    en = Graph.from_triples("en", [(C.FILM, "dbo:country", f"{C.EN}CA", "iri")])
    fr = Graph.from_triples("fr", [(f"{C.FR}SomeFilm", "dbo:country",
                                    f"{C.FR}CB", "iri")])
    align = C._base_align()
    ans, recs, unres = query_joint({"en": en, "fr": fr}, align, "en", C.FILM,
                                   ["dbo:country"], [])
    langs = {p[-1][0] for _, p, _ in recs}
    assert len(ans) == 2 and langs == {"en", "fr"} and unres == 0


def test_joint_rejects_split_length_mismatch(cfg):
    en = Graph.from_triples("en", [])
    with pytest.raises(ValueError):
        query_joint({"en": en}, C._base_align(), "en", C.FILM,
                    ["dbo:country", "dbo:capital"], ["en"])


def test_query_single_ignores_literals(cfg):
    en = Graph.from_triples("en", [(C.FILM, "dbo:country", "France", "literal")])
    ans, paths = query_single(en, C.FILM, ["dbo:country"])
    assert ans == set() and paths == []


def test_truncated_alt_search_is_marked(cfg):
    """穷举被预算截断时必须标记，不得声称已排除全部替代路径。"""
    en = Graph.from_triples("en", [])
    rc = cfg.raw["alternative_paths"]["relational_chain_search"]
    saved = rc["max_expansions"]
    rc["max_expansions"] = 1
    try:
        r = C._judge(cfg, {"en": en, "fr": Graph.from_triples("fr", [])},
                     C._base_align(),
                     C._template(["dbo:country"], ["en"]),
                     {"en": C.FILM, "fr": f"{C.FR}SomeFilm"})
        assert r["alt_check_status"] in ("executed_truncated", "not_run",
                                         "executed")
    finally:
        rc["max_expansions"] = saved


# ------------------------------------------------------------ 指标口径

def test_empty_denominator_is_null_not_zero():
    assert _ratio(0, 0) is None
    assert _ratio(3, 0) is None
    assert _ratio(0, 4) == 0.0


def test_track_a_metrics_counts_unresolved_separately():
    cands = [
        {"syntax_type_valid": True, "state": "strict_join"},
        {"syntax_type_valid": True, "state": "completion"},
        {"syntax_type_valid": True, "state": "single_graph"},
        {"syntax_type_valid": True, "state": "unresolved",
         "unresolved_kind": "mapping_missing"},
        {"syntax_type_valid": True, "state": "conflict_invalid"},
        {"syntax_type_valid": False, "state": "strict_join"},
    ]
    m = track_a_metrics(cands)
    assert m["denominator_valid_candidates"] == 5        # 无效候选不进主分母
    assert m["state_counts"]["strict_join"] == 1
    assert m["unfinished_unresolved"] == 1
    assert m["R_multi"] == pytest.approx(0.4)
    # 任务书 §7.7：unresolved 与 conflict_invalid 的多图必要性都视为未知，
    # 上界必须把两者都按正例计。只算 unresolved 会得到 0.6，低报上沿。
    assert m["n_unknown_necessity"] == 2
    assert m["R_multi_lower_bound_unknown_as_negative"] == pytest.approx(0.4)
    assert m["R_multi_upper_bound_unknown_as_positive"] == pytest.approx(0.8)
    assert m["unresolved_by_kind"] == {"mapping_missing": 1}


def test_track_a_metrics_null_when_no_valid_candidates():
    m = track_a_metrics([{"syntax_type_valid": False, "state": "single_graph"}])
    assert m["R_strict"] is None and m["R_multi"] is None


def test_diversity_flags_duplicate_roots():
    cands = [{"root_qid": "Q1", "template_id": "T1", "root_stratum": "work"},
             {"root_qid": "Q1", "template_id": "T2", "root_stratum": "work"}]
    d = diversity_metrics(cands)
    assert d["distinct_roots"] == 1 and d["duplicate_rate"] == 0.5


# ------------------------------------------------------------ 抽样与分层

def test_stratum_assignment_is_mutually_exclusive(cfg):
    assert _stratum_of({"dbo:Person", "dbo:Place"}, cfg) == "person"
    assert _stratum_of({"dbo:Place"}, cfg) == "place"
    assert _stratum_of({"dbo:Nothing"}, cfg) is None


def test_shared_pool_excludes_single_language_entities(cfg):
    en = Graph.from_triples("en", [(C.FILM, "dbo:country", f"{C.EN}CA", "iri")])
    fr = Graph.from_triples("fr", [(f"{C.FR}SomeFilm", "dbo:country",
                                    f"{C.FR}CB", "iri")])
    en.add_type(C.FILM, "dbo:Work")
    fr.add_type(f"{C.FR}SomeFilm", "dbo:Work")
    align = Alignment.from_map(["en", "fr"], {
        C.Q_FILM: {"en": C.FILM, "fr": f"{C.FR}SomeFilm"},
        "Q9": {"en": f"{C.EN}OnlyEn"},                       # 仅英文，须被排除
    })
    res = build_shared_pool(cfg, {"en": en, "fr": fr}, align)
    qids = {x["qid"] for x in res["pool"]}
    assert qids == {C.Q_FILM}


def test_dedup_key_ignores_question_wording():
    a = {"template_id": "T1", "root_qid": "Q1", "question_template": "说法甲"}
    b = {"template_id": "T1", "root_qid": "Q1", "question_template": "说法乙"}
    assert dedup_key(a) == dedup_key(b)


# ------------------------------------------------------------ 输入完整性

def test_scan_counts_corruption(tmp_path):
    from e001.prepare import scan_bz2
    p = tmp_path / "d.ttl.bz2"
    with bz2.open(str(p), "wt", encoding="utf-8") as f:
        f.write(f"<{C.FILM}> <{util.expand('dbo:country')}> <{C.EN}CA> .\n")
        f.write("garbage\n")
    stats = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
    scan_bz2(str(p), None, lambda *a: None, stats)
    assert stats["bad"] == 1 and stats["triples"] == 1


def test_parse_line_kinds():
    s, p, o, kind, lang = util.parse_line(
        '<http://a> <http://b> "Libellé"@fr .')
    assert (o, kind, lang) == ("Libellé", "literal", "fr")
    assert util.parse_line("<http://a> <http://b> <http://c> .")[3] == "iri"
    assert util.parse_line("not a triple") is None


def test_canon_keeps_unmapped_entities_language_separated():
    a = C._base_align()
    assert a.canon("en", f"{C.EN}Unmapped") != a.canon("fr", f"{C.FR}Unmapped")
    assert a.canon("en", C.PLACE_EN) == a.canon("fr", C.PLACE_FR)
