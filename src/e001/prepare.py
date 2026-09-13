"""WP1/WP2：输入下载与核验、可追溯派生视图构建。

原则：
- 原始数据放 data/raw/ 且下载后保持不变。
- 每个文件记录实际读入三元组数、解析错误数；损坏输入不得支撑「事实不存在」结论。
- 派生视图保留来源（源文件 id + 未压缩行号），支持 WP6 的逐样本独立复核。
"""
from __future__ import annotations

import bz2
import os
import subprocess
import time
from typing import Dict, List, Tuple

from . import util
from .config import Config

RAW_SUBDIR = "data/raw/e001"
PROC_SUBDIR = "data/processed/e001"


def _p(msg: str) -> None:
    """带时间戳的进度输出；长任务需要能从日志判断卡在哪一步。"""
    print(f"[{util.now_iso()}] {msg}", flush=True)


# ------------------------------------------------------------------ 下载

def _download_one(url: str, dest: str, expect_bytes: int | None,
                  retries: int = 5) -> Dict:
    """下载单文件；仅在大小不符时重下。返回 {bytes, sha256, ok, note}。"""
    util.ensure_dir(os.path.dirname(dest))
    tmp = dest + ".part"
    for attempt in range(1, retries + 1):
        if os.path.exists(dest) and expect_bytes and os.path.getsize(dest) == expect_bytes:
            break
        if os.path.exists(tmp):
            os.remove(tmp)
        # curl 比 urllib 更耐受长连接中断，且支持续传
        cmd = ["curl", "-sSL", "--fail", "--retry", "3", "--retry-delay", "3",
               "--max-time", "3600", "-o", tmp, url]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(tmp):
            if attempt == retries:
                return {"ok": False, "note": f"下载失败: {r.stderr.strip()[:200]}"}
            time.sleep(2 * attempt)
            continue
        os.replace(tmp, dest)
        break
    size = os.path.getsize(dest)
    rec = {"bytes": size, "sha256": util.sha256_file(dest), "ok": True, "note": ""}
    if expect_bytes and size != expect_bytes:
        rec["ok"] = False
        rec["note"] = f"字节数不符: 期望 {expect_bytes}, 实际 {size}"
    return rec


def download_inputs(cfg: Config, project_root: str) -> List[Dict]:
    """下载并核验全部输入，返回 manifest 条目列表。"""
    raw_dir = os.path.join(project_root, RAW_SUBDIR, cfg.release_id)
    util.ensure_dir(raw_dir)
    base = cfg.raw["release"]["base_url"]
    entries: List[Dict] = []
    for inp in cfg.raw["inputs"]:
        if inp["langs"]:
            for lang in inp["langs"]:
                rel = inp["relpath"].format(lang=lang)
                dest = os.path.join(raw_dir, f"{inp['id']}_{lang}.ttl.bz2")
                url = f"{base}/{rel}"
                exp = inp["bytes"].get(lang)
                _p(f"下载 {inp['id']}_{lang} ({exp/1024/1024:.1f} MiB)")
                rec = _download_one(url, dest, exp)
                _p(f"  -> {inp['id']}_{lang} {rec['bytes']/1024/1024:.1f} MiB "
                   f"{'ok' if rec['ok'] else '失败: ' + rec['note']}")
                entries.append({
                    "id": f"{inp['id']}_{lang}", "role": inp["role"], "lang": lang,
                    "url": url, "local_path": os.path.relpath(dest, project_root),
                    "expected_bytes": exp, **rec,
                })
        else:
            rel = inp["relpath"]
            dest = os.path.join(raw_dir, f"{inp['id']}.ttl.bz2")
            url = f"{base}/{rel}"
            exp = inp["bytes"].get("_single")
            _p(f"下载 {inp['id']} ({exp/1024/1024:.1f} MiB)")
            rec = _download_one(url, dest, exp)
            _p(f"  -> {inp['id']} {rec['bytes']/1024/1024:.1f} MiB "
               f"{'ok' if rec['ok'] else '失败: ' + rec['note']}")
            entries.append({
                "id": inp["id"], "role": inp["role"], "lang": None,
                "url": url, "local_path": os.path.relpath(dest, project_root),
                "expected_bytes": exp, **rec,
            })
    return entries


def raw_path(project_root: str, release: str, file_id: str) -> str:
    return os.path.join(project_root, RAW_SUBDIR, release, f"{file_id}.ttl.bz2")


# ------------------------------------------------------- 流式扫描与派生视图

def scan_bz2(path: str, keep, on_triple, stats: Dict) -> None:
    """逐行扫描 .bz2，用 keep(pred_iri) 过滤后调用 on_triple(lineno, s, p, o, kind, lang)。

    统计写入 stats：triples(读入的非空三元组行), parsed, bad(解析失败), kept。
    """
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            if not line or line.startswith("#") or not line.strip():
                continue
            stats["lines"] += 1
            t = util.parse_line(line)
            if t is None:
                stats["bad"] += 1
                continue
            s, p, o, kind, olang = t
            stats["triples"] += 1
            if keep is not None and p not in keep:
                continue
            stats["kept"] += 1
            on_triple(lineno, s, p, o, kind, olang)


def build_edges(cfg: Config, project_root: str, lang: str,
                stats_out: Dict) -> str:
    """构建某语言图的白名单关系出边视图。

    输出 TSV: subject_iri \t pred_curie \t object \t kind \t obj_lang \t src_id \t lineno
    保留来源，支持逐样本回溯到源文件与行号（任务书 §7）。
    """
    rels = {util.expand(r) for r in cfg.raw["relations"]}
    out_dir = os.path.join(project_root, PROC_SUBDIR)
    util.ensure_dir(out_dir)
    out_path = os.path.join(out_dir, f"edges_{lang}.tsv")

    srcs = []
    for f in cfg.raw["inputs"]:
        if f["role"] == "fact" and lang in f["langs"]:
            srcs.append((f"{f['id']}_{lang}", raw_path(project_root, cfg.release_id,
                                                       f"{f['id']}_{lang}")))

    stats = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
    per_src = {}
    with open(out_path, "w", encoding="utf-8") as out:
        for src_id, path in srcs:
            if not os.path.exists(path):
                raise FileNotFoundError(f"缺少输入文件: {path}")
            s2 = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
            def emit(lineno, s, p, o, kind, olang, _src=src_id, _out=out):
                if kind == "iri":
                    _out.write(f"{s}\t{util.curie(p)}\t{o}\tiri\t\t{_src}\t{lineno}\n")
                elif kind == "literal":
                    val = o.replace("\t", " ").replace("\n", " ")
                    _out.write(f"{s}\t{util.curie(p)}\t{val}\tliteral\t{olang or ''}\t{_src}\t{lineno}\n")
            scan_bz2(path, rels, emit, s2)
            per_src[src_id] = dict(s2)
            for k in stats:
                stats[k] += s2[k]
    stats_out[lang] = {"total": stats, "per_source": per_src,
                       "output": os.path.relpath(out_path, project_root)}
    return out_path


def build_types(cfg: Config, project_root: str, lang: str, stats_out: Dict) -> str:
    """构建 rdf:type 视图（dbo: 类），用于根实体分层与桥接类型核验。"""
    rdf_type = util.expand("rdf:type")
    keep = {rdf_type}
    out_dir = os.path.join(project_root, PROC_SUBDIR)
    util.ensure_dir(out_dir)
    out_path = os.path.join(out_dir, f"types_{lang}.tsv")

    srcs = []
    for f in cfg.raw["inputs"]:
        if f["role"] == "types" and lang in f["langs"]:
            srcs.append((f"{f['id']}_{lang}", raw_path(project_root, cfg.release_id,
                                                       f"{f['id']}_{lang}")))
    stats = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
    with open(out_path, "w", encoding="utf-8") as out:
        for src_id, path in srcs:
            if not os.path.exists(path):
                raise FileNotFoundError(f"缺少输入文件: {path}")
            def emit(lineno, s, p, o, kind, olang, _out=out):
                if kind == "iri" and o.startswith(util.NS["dbo"]):
                    _out.write(f"{s}\t{util.curie(o)}\n")
            scan_bz2(path, keep, emit, stats)
    stats_out[lang] = {"total": stats, "output": os.path.relpath(out_path, project_root)}
    return out_path


def build_labels(cfg: Config, project_root: str, lang: str, stats_out: Dict) -> str:
    rdfs_label = util.expand("rdfs:label")
    out_dir = os.path.join(project_root, PROC_SUBDIR)
    util.ensure_dir(out_dir)
    out_path = os.path.join(out_dir, f"labels_{lang}.tsv")
    srcs = []
    for f in cfg.raw["inputs"]:
        if f["role"] == "labels" and lang in f["langs"]:
            srcs.append((f"{f['id']}_{lang}", raw_path(project_root, cfg.release_id,
                                                       f"{f['id']}_{lang}")))
    stats = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
    with open(out_path, "w", encoding="utf-8") as out:
        for src_id, path in srcs:
            if not os.path.exists(path):
                raise FileNotFoundError(f"缺少输入文件: {path}")
            def emit(lineno, s, p, o, kind, olang, _out=out):
                if kind == "literal" and olang == lang:
                    val = o.replace("\t", " ").replace("\n", " ")
                    _out.write(f"{s}\t{val}\n")
            scan_bz2(path, {rdfs_label}, emit, stats)
    stats_out[lang] = {"total": stats, "output": os.path.relpath(out_path, project_root)}
    return out_path


def build_qid_map(cfg: Config, project_root: str, stats_out: Dict) -> str:
    """WP2：从 sameas-all-wikis 建立 QID -> {lang: 本地资源} 的评测参考映射。

    参考映射与原图分开存储（任务书 §2.5），只在评测侧使用。
    """
    al = cfg.raw["alignment"]
    sameas = util.expand(al["predicate"])
    qid_ns = al["qid_ns"]
    langs = cfg.languages
    res_ns = {l: al["resource_ns"][l] for l in langs}
    out_dir = os.path.join(project_root, PROC_SUBDIR)
    util.ensure_dir(out_dir)
    out_path = os.path.join(out_dir, "qid_map.tsv")

    path = raw_path(project_root, cfg.release_id, "sameas_all_wikis")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少对齐文件: {path}")
    stats = {"lines": 0, "triples": 0, "bad": 0, "kept": 0}
    rows: Dict[str, Dict[str, str]] = {}
    def emit(lineno, s, p, o, kind, olang):
        if kind != "iri" or not s.startswith(qid_ns) or s.startswith(qid_ns + "Category:"):
            return
        qid = s[len(qid_ns):]
        if not qid.startswith("Q"):
            return
        for l in langs:
            if o.startswith(res_ns[l]):
                rows.setdefault(qid, {})[l] = o
    scan_bz2(path, {sameas}, emit, stats)

    n_both = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for qid, d in rows.items():
            if len(d) == len(langs):
                n_both += 1
            parts = [qid]
            for l in langs:
                parts.append(d.get(l, ""))
            out.write("\t".join(parts) + "\n")
    stats_out["qid_map"] = {
        "total": stats,
        "qids_total": len(rows),
        "qids_with_all_langs": n_both,
        "output": os.path.relpath(out_path, project_root),
    }
    return out_path


def prepare(cfg: Config, project_root: str, skip_download: bool = False) -> Dict:
    """完整 prepare：下载核验 + 派生视图。返回准备阶段统计。"""
    result: Dict = {"release": cfg.release_id, "started": util.now_iso()}
    if not skip_download:
        result["inputs"] = download_inputs(cfg, project_root)
        bad = [e for e in result["inputs"] if not e["ok"]]
        if bad:
            result["status"] = "blocked"
            result["blocked_reason"] = f"{len(bad)} 个输入下载或校验失败"
            result["finished"] = util.now_iso()
            return result
    result["edges"] = {}
    result["types"] = {}
    result["labels"] = {}
    for lang in cfg.languages:
        _p(f"构建 {lang} 白名单出边视图")
        build_edges(cfg, project_root, lang, result["edges"])
        _p(f"  -> {lang} edges: {result['edges'][lang]['total']}")
        _p(f"构建 {lang} 类型视图")
        build_types(cfg, project_root, lang, result["types"])
        _p(f"  -> {lang} types: {result['types'][lang]['total']}")
        _p(f"构建 {lang} 标签视图")
        build_labels(cfg, project_root, lang, result["labels"])
        _p(f"  -> {lang} labels: {result['labels'][lang]['total']}")
    _p("构建 QID 参考映射（sameas-all-wikis，最大单文件）")
    build_qid_map(cfg, project_root, result)
    _p(f"  -> qid_map: {result['qid_map']['qids_total']} 个 QID，"
       f"{result['qid_map']['qids_with_all_langs']} 个含全部语言")
    result["status"] = "ok"
    result["finished"] = util.now_iso()
    return result
