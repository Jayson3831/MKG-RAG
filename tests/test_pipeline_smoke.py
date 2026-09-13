"""端到端冒烟测试：用微型合成原始文件跑通 prepare → run → recompute。

合成数据刻意构造一个真实的跨图连接（en 提供 director，fr 提供 birthPlace），
因此可以断言流水线确实把它判成 strict_join，而不是只断言「没有崩溃」。
"""
from __future__ import annotations

import bz2
import json
import os

import pytest

from e001 import pipeline, util
from e001.config import load as load_config
from e001.prepare import prepare as run_prepare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "configs", "e001.yaml")
RELEASE = "2022.03.01"

EN = "http://dbpedia.org/resource/"
FR = "http://fr.dbpedia.org/resource/"
DBO = "http://dbpedia.org/ontology/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
SAMEAS = "http://www.w3.org/2002/07/owl#sameAs"
WKD = "http://wikidata.dbpedia.org/resource/"

FILM, PERSON_EN, PLACE_EN = f"{EN}SomeFilm", f"{EN}SomePerson", f"{EN}SomePlace"
FILM_FR, PERSON_FR, PLACE_FR = f"{FR}SomeFilm", f"{FR}SomePerson", f"{FR}SomePlace"


def _write(root: str, file_id: str, lines) -> None:
    d = os.path.join(root, "data", "raw", "e001", RELEASE)
    os.makedirs(d, exist_ok=True)
    with bz2.open(os.path.join(d, f"{file_id}.ttl.bz2"), "wt",
                  encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


@pytest.fixture(scope="module")
def synthetic_root(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("proj"))
    t = lambda s, p, o: f"<{s}> <{p}> <{o}> ."          # noqa: E731
    lit = lambda s, p, v, l: f'<{s}> <{p}> "{v}"@{l} .'  # noqa: E731

    # 跨图连接：en 有 director，fr 有该导演的 birthPlace
    _write(root, "mappingbased_objects_en", [
        t(FILM, f"{DBO}director", PERSON_EN),
        t(FILM, f"{DBO}country", f"{EN}CountryA"),
    ])
    _write(root, "mappingbased_objects_fr", [
        t(PERSON_FR, f"{DBO}birthPlace", PLACE_FR),
        t(FILM_FR, f"{DBO}country", f"{FR}CountryB"),
    ])
    _write(root, "mappingbased_literals_en", [lit(FILM, RDFS_LABEL, "Some Film", "en")])
    _write(root, "mappingbased_literals_fr", [lit(FILM_FR, RDFS_LABEL, "Un Film", "fr")])

    _write(root, "instance_types_specific_en", [
        t(FILM, RDF_TYPE, f"{DBO}Film")])
    _write(root, "instance_types_specific_fr", [
        t(FILM_FR, RDF_TYPE, f"{DBO}Film")])
    _write(root, "instance_types_transitive_en", [
        t(FILM, RDF_TYPE, f"{DBO}Film"),
        t(FILM, RDF_TYPE, f"{DBO}Work"),
        t(PERSON_EN, RDF_TYPE, f"{DBO}Person")])
    _write(root, "instance_types_transitive_fr", [
        t(FILM_FR, RDF_TYPE, f"{DBO}Film"),
        t(FILM_FR, RDF_TYPE, f"{DBO}Work"),
        t(PERSON_FR, RDF_TYPE, f"{DBO}Person"),
        t(PLACE_FR, RDF_TYPE, f"{DBO}Place")])

    _write(root, "labels_en", [lit(FILM, RDFS_LABEL, "Some Film", "en")])
    _write(root, "labels_fr", [lit(FILM_FR, RDFS_LABEL, "Un Film", "fr")])

    _write(root, "sameas_all_wikis", [
        t(f"{WKD}Q1", SAMEAS, FILM), t(f"{WKD}Q1", SAMEAS, FILM_FR),
        t(f"{WKD}Q2", SAMEAS, PERSON_EN), t(f"{WKD}Q2", SAMEAS, PERSON_FR),
        t(f"{WKD}Q3", SAMEAS, PLACE_EN), t(f"{WKD}Q3", SAMEAS, PLACE_FR),
    ])
    return root


@pytest.fixture(scope="module")
def smoke_cfg(synthetic_root, tmp_path_factory):
    """与合成文件**实际字节数**一致的配置副本。

    正式配置里 inputs[].bytes 记录的是真实 DBpedia 文件大小，而运行开始时会
    当场重算磁盘字节并与之比对（pipeline.input_checksums），不符即中止。冒烟
    测试因此必须用一份字节数诚实的配置副本，而不是让校验器对合成文件放行——
    放行会让「输入与配置不一致就中止」这条路径永远得不到执行。
    """
    import copy

    import yaml

    raw = copy.deepcopy(load_config(CONFIG).raw)
    for inp in raw["inputs"]:
        keys = ([f"{inp['id']}_{l}" for l in inp["langs"]] if inp["langs"]
                else [inp["id"]])
        for fid in keys:
            path = os.path.join(synthetic_root, "data", "raw", "e001",
                                RELEASE, f"{fid}.ttl.bz2")
            slot = fid.rsplit("_", 1)[-1] if inp["langs"] else "_single"
            inp["bytes"][slot] = os.path.getsize(path)
    p = tmp_path_factory.mktemp("cfg") / "e001.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return load_config(str(p))


@pytest.fixture(scope="module")
def smoke(smoke_cfg, synthetic_root, tmp_path_factory):
    cfg = smoke_cfg
    prep = run_prepare(cfg, synthetic_root, skip_download=True)
    assert prep["status"] == "ok", prep
    run_dir = str(tmp_path_factory.mktemp("runs") / f"{RELEASE}_E001")
    status = pipeline.run(cfg, synthetic_root, run_dir, "pytest smoke")
    return cfg, synthetic_root, run_dir, status, prep


def test_prepare_builds_derived_views(smoke):
    _cfg, root, _rd, _st, prep = smoke
    base = os.path.join(root, "data", "processed", "e001")
    for name in ("edges_en.tsv", "edges_fr.tsv", "types_en.tsv",
                 "labels_fr.tsv", "qid_map.tsv"):
        assert os.path.exists(os.path.join(base, name)), name
    assert prep["qid_map"]["qids_with_all_langs"] == 3


def test_input_checksums_match_and_mismatch_is_detected(smoke_cfg, synthetic_root):
    """运行时输入校验：一致时通过；字节数被改动时必须报不一致。

    后半段是关键——若校验恒真，正式运行里「输入与冻结配置不符」就永远不会
    暴露，而这正是它存在的唯一理由。
    """
    im = pipeline.input_checksums(smoke_cfg, synthetic_root)
    assert im["all_ok"] and im["n_files"] == 11 and not im["mismatched"]

    saved = smoke_cfg.raw["inputs"][0]["bytes"]["en"]
    smoke_cfg.raw["inputs"][0]["bytes"]["en"] = saved + 1
    try:
        bad = pipeline.input_checksums(smoke_cfg, synthetic_root)
    finally:
        smoke_cfg.raw["inputs"][0]["bytes"]["en"] = saved
    assert not bad["all_ok"]
    assert bad["mismatched"] == ["mappingbased_objects_en"]
    row = [r for r in bad["files"] if r["id"] == "mappingbased_objects_en"][0]
    assert row["bytes_match_config"] is False and row["sha256"]


def test_run_writes_all_required_artifacts(smoke):
    _cfg, _root, run_dir, _st, _prep = smoke
    required = ["manifest.json", "config.json", "command.txt", "run.log",
                "run-status.json", "metrics.json", "queries.jsonl",
                "certificates.jsonl", "candidates.jsonl", "sampling.json",
                "normalization.jsonl", "checks.json", "audit-selection.json",
                "audit-samples.html", "audit.jsonl"]
    for name in required:
        assert os.path.exists(os.path.join(run_dir, name)), f"缺少交付物 {name}"


def test_run_detects_the_planted_strict_join(smoke):
    """合成数据里 T1 必须被判为 strict_join，否则判定逻辑有实质缺陷。"""
    _cfg, _root, run_dir, _st, _prep = smoke
    rows = list(util.read_jsonl(os.path.join(run_dir, "candidates.jsonl")))
    t1 = [r for r in rows if r.get("template_id") == "T1_film_director_birthplace"]
    assert t1, "未生成 T1 候选"
    states = {r["state"] for r in t1}
    assert "strict_join" in states, f"T1 状态: {states}"
    hit = [r for r in t1 if r["state"] == "strict_join"][0]
    assert hit["answers_joint"] == [PLACE_FR]
    # 证据必须带源文件与未压缩行号
    step = hit["joint_records"][0]["path"][0]
    assert step["src"] and step["lineno"] >= 1
    # 跨图处必须有桥接记录
    assert hit["joint_records"][0]["bridges"]


def test_union_template_yields_completion_or_single(smoke):
    _cfg, _root, run_dir, _st, _prep = smoke
    rows = list(util.read_jsonl(os.path.join(run_dir, "candidates.jsonl")))
    c1 = [r for r in rows if r.get("template_id") == "C1_film_country_union"]
    assert c1
    r = c1[0]
    assert r["state"] in ("completion", "single_graph", "strict_join")
    assert len(r["answers_joint"]) == 2, r["answers_joint"]


def test_status_separates_engineering_and_research(smoke):
    _cfg, _root, run_dir, status, _prep = smoke
    assert status["engineering_acceptance"]["status"] in (
        "completed", "completed_with_failures")
    assert status["research_review"]["status"] == "pending_human_review"
    assert status["research_review"]["human_status"] == "pending"


def test_audit_records_start_pending(smoke):
    _cfg, _root, run_dir, _st, _prep = smoke
    rows = list(util.read_jsonl(os.path.join(run_dir, "audit.jsonl")))
    assert rows, "审核记录为空"
    assert all(r["human_status"] == "pending" for r in rows)
    assert all(r["reviewer"] is None and r["reviewed_at"] is None for r in rows)


def test_checks_json_all_passed(smoke):
    _cfg, _root, run_dir, _st, _prep = smoke
    chk = json.load(open(os.path.join(run_dir, "checks.json"), encoding="utf-8"))
    failed = [c["name"] for c in chk["checks"] if not c["passed"]]
    assert chk["all_passed"], f"运行内检查未通过: {failed}"


def test_recompute_matches_metrics(smoke):
    cfg, root, run_dir, _st, _prep = smoke
    out = pipeline.recompute_run(cfg, root, run_dir)
    for k, v in out["diff_vs_metrics_json"].items():
        assert v["match"], f"{k} 复算不一致: {v}"


def test_verify_replays_from_raw_files(smoke):
    """verify 必须直读原始 .bz2 重解，且结果与记录一致。"""
    cfg, root, run_dir, _st, _prep = smoke
    from e001.graph import Alignment
    from e001.verify import verify_ids
    ledger = list(util.read_jsonl(os.path.join(run_dir, "candidates.jsonl")))
    ids = [r["question_id"] for r in ledger if r.get("track") == "A"][:5]
    align = Alignment(cfg.languages)
    align.load(root)
    res = verify_ids(cfg, root, align, ledger, ids)
    assert res["found"] == len(ids)
    assert res["missing_ids"] == []
    assert res["agreement_rate"] == 1.0, res["results"]
    # 证据必须来自原始文件扫描，且带未压缩行号
    t1 = [r for r in res["results"]
          if r["template_id"] == "T1_film_director_birthplace"
          and r["replayed_paths_joint"]]
    assert t1, "T1 的重放路径为空"
    step = t1[0]["replayed_paths_joint"][0][0]
    assert step["src"].startswith("mappingbased_objects_")
    assert step["lineno"] >= 1


def test_verify_reports_missing_ids(smoke):
    cfg, root, run_dir, _st, _prep = smoke
    from e001.graph import Alignment
    from e001.verify import verify_ids
    ledger = list(util.read_jsonl(os.path.join(run_dir, "candidates.jsonl")))
    align = Alignment(cfg.languages)
    align.load(root)
    res = verify_ids(cfg, root, align, ledger, ["does-not-exist"])
    assert res["found"] == 0 and res["missing_ids"] == ["does-not-exist"]


def test_audit_export_within_limits(smoke, tmp_path):
    cfg, root, run_dir, _st, _prep = smoke
    m = pipeline.audit_export(cfg, root, run_dir, out_dir=str(tmp_path / "review"))
    assert m["n_files"] > 0
    assert m["within_limits"]
    assert all("sha256" in f and f["bytes"] > 0 for f in m["files"])
    # manifest 不包含自身哈希（避免自指）
    assert all(f["path"] != "download-manifest.json" for f in m["files"])
