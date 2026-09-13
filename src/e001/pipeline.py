"""E001 流水线编排：prepare → run → recompute → verify → audit-export。

运行目录约定（AGENTS.md）：outputs/runs/<YYYYMMDD-HHMMSS>_E001/
每个正式运行记录真实 code_commit、冻结配置与哈希、输入校验值、环境、命令与日志；
工程验收与研究审核在 run-status.json 中分开记录，互不代替。
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from typing import Dict, List, Optional

from . import audit as audit_mod
from . import checks as checks_mod
from . import util
from .config import Config
from .graph import Alignment, Graph
from .metrics import (diversity_metrics, overlap_metrics, population_overlap,
                      track_a_metrics)
from .sample import build_shared_pool, population_sample, stratified_sample
from .tracks import track_a, track_b


# ------------------------------------------------------------------ 运行环境

def run_id(experiment: str = "E001", root: str = "outputs/runs") -> str:
    return os.path.join(root, f"{time.strftime('%Y%m%d-%H%M%S')}_{experiment}")


def git_info(project_root: str) -> Dict:
    """记录代码快照。dirty 为真表示工作区有未提交改动，正式运行不应出现。"""
    def _git(*args):
        r = subprocess.run(["git", "-C", project_root, *args],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return {
        "code_commit": commit,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_dirty": bool(status),
        "dirty_files": (status or "").splitlines()[:50],
        "describe": _git("describe", "--always", "--dirty"),
    }


def environment_info() -> Dict:
    def _py(mod):
        try:
            m = __import__(mod)
            return getattr(m, "__version__", "unknown")
        except Exception:                                   # noqa: BLE001
            return None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "packages": {m: _py(m) for m in ("yaml", "requests", "numpy", "tqdm")},
        "tz": util.TZ_NAME,
    }


# ------------------------------------------------------------ 派生记录生成

def normalization_records(cfg: Config, results: List[Dict],
                          align: Alignment) -> List[Dict]:
    """记录每次答案比较所用的归一化，以及**未被计入**的差异类型。

    验证计划 §2.3：标签、摘要、注释、sameAs 链接不计入答案事实重叠。
    本记录把「比较了什么、没比较什么」写死在产物里，避免事后解释。
    """
    rows = []
    for r in results:
        if not r.get("answers_joint"):
            continue
        rows.append({
            "question_id": r["question_id"],
            "answers_raw": r["answers_joint"],
            "answers_canon": r.get("answers_joint_canon", []),
            "answers_lang": r.get("answers_joint_lang"),
            "rule": "lowercase_localname + QID identity",
            "identity_source": cfg.raw["alignment"]["source"],
            "counted_as_distinct_facts": True,
            "not_counted_as_complementary": [
                "rdfs:label 语言标签差异",
                "摘要 / 注释文本",
                "owl:sameAs 映射边本身",
                "数值单位与日期精度差异",
                "同一实体的不同本地 URI 写法",
            ],
            "note": ("同一 QID 的不同本地 URI 记为同一答案，不视为两图互补；"
                     "无参考映射的答案按 (语言, 本地名小写) 归一，不跨语言合并。"),
        })
    return rows


def certificates(cfg: Config, results: List[Dict]) -> List[Dict]:
    """逐候选证据证书：结论 + 可回溯到源文件与未压缩行号的证据路径。"""
    out = []
    for r in results:
        out.append({
            "question_id": r["question_id"],
            "template_id": r["template_id"],
            "state": r["state"],
            "chain": r["chain"],
            "split": r["split"],
            "answers": {
                "joint": r.get("answers_joint", []),
                "en": r.get("answers_en", []),
                "fr": r.get("answers_fr", []),
            },
            "joint_evidence": r.get("joint_records", [])[:5],
            "single_evidence": {
                lang: r.get(f"single_paths_{lang}", [])[:5]
                for lang in cfg.languages
            },
            "alternative_paths": {
                "status": r.get("alt_check_status"),
                "truncated": r.get("alt_check_truncated", False),
                "search_cost": r.get("alt_search_cost", {}),
                "hits": r.get("alt_path_hits", []),
            },
            "partial_single_answers": r.get("partial_single_answers", []),
            "unresolved_branches": r.get("unresolved_branches", 0),
            "consistency_error": r.get("consistency_error"),
            "reason": r.get("reason"),
            "source_coordinates": ("每条 joint_evidence/single_evidence 记录均带 "
                                   "src（源文件 id）与 lineno（未压缩行号）"),
        })
    return out


# ------------------------------------------------------------------ 主流程

def _load_graphs(cfg: Config, project_root: str) -> Dict[str, Graph]:
    graphs = {}
    for lang in cfg.languages:
        g = Graph(lang)
        g.load(project_root)
        graphs[lang] = g
    return graphs


def run(cfg: Config, project_root: str, run_dir: str,
        command: str, limit: Optional[int] = None) -> Dict:
    """执行正式运行。limit 仅用于冒烟测试，正式运行必须为 None。"""
    t0 = time.time()
    util.ensure_dir(run_dir)
    log_path = os.path.join(run_dir, "run.log")

    def log(msg: str) -> None:
        line = f"[{util.now_iso()}] {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # --- 冻结配置与代码快照 ---
    util.write_json(os.path.join(run_dir, "config.json"), cfg.raw)
    with open(os.path.join(run_dir, "command.txt"), "w", encoding="utf-8") as f:
        f.write(command + "\n")
    meta = {"run_dir": os.path.relpath(run_dir, project_root),
            "experiment": cfg.raw["experiment"],
            "started": util.now_iso(),
            "config": cfg.summary(),
            "git": git_info(project_root),
            "environment": environment_info(),
            "smoke_limit": limit,
            "command": command}
    util.write_json(os.path.join(run_dir, "manifest.json"), meta)
    if meta["git"]["worktree_dirty"]:
        log(f"警告：工作区有未提交改动，本次 code_commit 不足以复现："
            f"{meta['git']['dirty_files']}")

    # --- 正确性检查（本次代码快照的真实输出）---
    log("执行正确性检查")
    chk = checks_mod.run_all(cfg)
    util.write_json(os.path.join(run_dir, "checks.json"), chk)
    log(f"正确性检查 {chk['n_passed']}/{chk['n_total']} 通过")

    # --- 载入图与参考映射 ---
    log("载入派生视图")
    graphs = _load_graphs(cfg, project_root)
    align = Alignment(cfg.languages)
    align.load(project_root)
    graph_meta = {l: {"subjects": len(graphs[l].edges),
                      "typed": len(graphs[l].types)} for l in cfg.languages}

    # --- 抽样 ---
    log("构建共享实体池并分层抽样")
    pool_res = build_shared_pool(cfg, graphs, align)
    pool = pool_res["pool"]
    s = cfg.raw["sampling"]
    sampled = stratified_sample(cfg, pool, s["seed"], s["entity_target"])
    if limit:
        sampled = sampled[:limit]
    pop = population_sample(cfg, graphs)
    align_stats = {
        "source": cfg.raw["alignment"]["source"],
        "qids_total": len(align.by_qid),
        "qids_with_all_langs": pool_res["n_qid_both"],
        "qids_present_in_both_graphs": pool_res["n_both_present"],
        "qids_stratified": pool_res["n_stratified"],
        "note": ("共享池按「两图均存在」筛选，只用于桥接分析；"
                 "实体总体重合率另用独立总体样本估计。"),
    }
    util.write_json(os.path.join(run_dir, "sampling.json"), {
        "generated_at": util.now_iso(),
        "seed": s["seed"],
        "entity_target": s["entity_target"],
        "n_pool": len(pool),
        "n_sampled": len(sampled),
        "shortfall": max(0, s["entity_target"] - len(sampled)),
        "per_stratum": {st: sum(1 for x in sampled if x["stratum"] == st)
                        for st in {x["stratum"] for x in sampled}} if sampled else {},
        "alignment": align_stats,
        "population_sample": {
            l: {"total_entities": pop[l]["total_entities"],
                "sample_size": pop[l]["sample_size"]} for l in cfg.languages},
        "sample": [{k: v for k, v in x.items() if k != "classes"} for x in sampled],
        "note": ("共享实体样本不足目标数时如实记录缺口，不缩小判定口径。"
                 "总体重合率不得在本样本上估计。"),
    })

    # --- 候选生成与判定 ---
    log(f"轨道 A：在 {len(sampled)} 个已抽样实体上生成候选")
    ta = track_a(cfg, graphs, sampled)
    judged: Dict[str, Dict] = {}
    for i, c in enumerate(ta["candidates"], start=1):
        judged[c["question_id"]] = judge_one(cfg, graphs, align, c)
        if i % 200 == 0:
            log(f"  轨道 A 判定 {i}/{len(ta['candidates'])}")
    ta_by_id = judged
    # 候选元数据与判定结果合并保存：单一账本同时携带「问的是什么」与「查到什么」
    results: List[Dict] = [{**c, **judged[c["question_id"]]}
                           for c in ta["candidates"]]

    log("轨道 B：定向挖掘跨图路径")
    tb = track_b(cfg, graphs, sampled,
                 lambda c, idx: judge_one(cfg, graphs, align, c))
    b_qualified = list(tb["qualified"])      # track_b 已合并候选与判定结果

    # 合并账本：轨道 A 全部候选（含各类失败）+ 轨道 B 全部尝试。
    # 两轨分母独立，track 字段区分，统计时绝不混合（任务书 §3）。
    ledger = list(results)
    ledger += b_qualified
    ledger += tb_failures_as_results(tb)
    results += b_qualified                    # 两轨查询记录一并归档
    all_by_id = {r["question_id"]: r for r in results}

    # --- 指标 ---
    log("计算指标")
    a_ledger = [x for x in ledger if x.get("track") == "A"]
    m = {
        "generated_at": util.now_iso(),
        "track_a": track_a_metrics(a_ledger),
        "track_a_diversity": diversity_metrics(a_ledger),
        "track_b": {"attempts": tb["attempts"], "budget": tb["budget"],
                    "reached_budget": tb["reached_budget"],
                    "n_qualified": tb["n_qualified"], "n_failed": tb["n_failed"],
                    "pass_rate": tb["pass_rate"],
                    "pass_rate_denominator": tb["pass_rate_denominator"],
                    "note": tb["note"]},
        "overlap": overlap_metrics(cfg, pop, align_stats, graph_meta,
                                   population_overlap(graphs, align, pop,
                                                      cfg.languages)),
        "track_a_generation": {k: v for k, v in ta.items() if k != "candidates"},
    }
    util.write_json(os.path.join(run_dir, "metrics.json"), m)

    # --- 产物 ---
    log("写出运行产物")
    util.write_jsonl(os.path.join(run_dir, "candidates.jsonl"), ledger)
    util.write_jsonl(os.path.join(run_dir, "queries.jsonl"), results)
    util.write_jsonl(os.path.join(run_dir, "certificates.jsonl"),
                     certificates(cfg, results))
    util.write_jsonl(os.path.join(run_dir, "normalization.jsonl"),
                     normalization_records(cfg, results, align))

    # --- 审核包 ---
    log("导出审核包与诊断包")
    qualified = [x for x in ledger
                 if x.get("track") == "A" and x.get("state") in
                 ("strict_join", "completion", "single_graph")]
    non_qualified = [x for x in ledger if x.get("state") not in
                     ("strict_join", "completion", "single_graph")]
    selection = audit_mod.select_audit(cfg, qualified)
    diagnostic = audit_mod.select_diagnostic(cfg, non_qualified)
    led_by_id = {x["question_id"]: x for x in ledger}
    labels = {x["question_id"]: x.get("question_template", "") for x in ledger}
    rows = audit_mod.init_audit_records(cfg, selection, all_by_id)
    util.write_jsonl(os.path.join(run_dir, "audit.jsonl"), rows)
    util.write_json(os.path.join(run_dir, "audit-selection.json"),
                    {"formal": selection, "diagnostic": diagnostic,
                     "generated_at": util.now_iso()})
    html_doc = audit_mod.render_html(cfg, os.path.basename(run_dir), selection,
                                     all_by_id, led_by_id, labels)
    with open(os.path.join(run_dir, "audit-samples.html"), "w",
              encoding="utf-8") as f:
        f.write(html_doc)

    # --- 运行状态 ---
    elapsed = time.time() - t0
    mem = read_meminfo()
    status = {
        "experiment": cfg.raw["experiment"],
        "run_dir": os.path.relpath(run_dir, project_root),
        "started": meta["started"],
        "finished": util.now_iso(),
        "elapsed_seconds": round(elapsed, 1),
        "code_commit": meta["git"]["code_commit"],
        "worktree_dirty": meta["git"]["worktree_dirty"],
        "config_sha256": cfg.digest,
        "release": cfg.release_id,
        "engineering_acceptance": {
            "status": "completed" if chk["all_passed"] else "completed_with_failures",
            "checks_passed": chk["n_passed"],
            "checks_total": chk["n_total"],
            "note": "自动检查只证明实现按声明执行，不等于研究结论成立。",
        },
        "research_review": {
            "status": "pending_human_review",
            "human_status": cfg.raw["audit"]["human_status_initial"],
            "audit_sample_size": selection["sample_size"],
            "audit_target": selection["target"],
            "audit_shortfall": selection["shortfall"],
            "note": ("研究判断待人工审核；audit.jsonl 中 human_status 一律为 "
                     "pending，自动检查不得填成人工审核。"),
        },
        "resource_usage": {"elapsed_seconds": round(elapsed, 1),
                           "meminfo": mem},
        "artifacts": sorted(os.listdir(run_dir)),
    }
    util.write_json(os.path.join(run_dir, "run-status.json"), status)
    log(f"完成：{status['engineering_acceptance']['status']}，"
        f"耗时 {status['elapsed_seconds']}s")
    return status


def tb_failures_as_results(tb: Dict) -> List[Dict]:
    """轨道 B 的失败样本单独入账，不污染轨道 A 的分母。"""
    return [{"track": "B", "state": f["state"], "question_id": f["question_id"],
             "template_id": f["template_id"], "root_qid": f["root_qid"],
             "reason": f.get("reason")}
            for f in tb.get("failures_sample", [])]


def judge_one(cfg: Config, graphs: Dict[str, Graph], align: Alignment,
              cand: Dict) -> Dict:
    """判定单个候选；未决/异常一律记为 unresolved，不静默丢弃。"""
    from .judge import judge_candidate
    try:
        return judge_candidate(cfg, graphs, align, cand, cand["root_iris"])
    except Exception as exc:                                 # noqa: BLE001
        return {"question_id": cand["question_id"], "state": "unresolved",
                "answers_joint": [], "answers_en": [], "answers_fr": [],
                "alt_check_status": "error", "consistency_error": None,
                "reason": f"判定异常，记为未决: {type(exc).__name__}: {exc}"}


def read_meminfo() -> Dict:
    out = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k in ("MemTotal", "MemAvailable", "SwapTotal"):
                    out[k] = v.strip()
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ 复算与导出

def recompute_run(cfg: Config, project_root: str, run_dir: str) -> Dict:
    """只读已保存的候选账本重算指标，不重跑查询，用于与原始结果对账。"""
    ledger = list(util.read_jsonl(os.path.join(run_dir, "candidates.jsonl")))
    a = [x for x in ledger if x.get("track") == "A"]
    out = {
        "recomputed_at": util.now_iso(),
        "source_run_dir": os.path.relpath(run_dir, project_root),
        "config_sha256": cfg.digest,
        "n_ledger_rows": len(ledger),
        "track_a": track_a_metrics(a),
        "track_a_diversity": diversity_metrics(a),
        "track_b_attempts": sum(1 for x in ledger if x.get("track") == "B"),
        "note": ("复算只读 candidates.jsonl，不调用判定函数、不重跑查询；"
                 "与 metrics.json 的差异即表示两者口径不一致，须先查清。"),
    }
    orig_path = os.path.join(run_dir, "metrics.json")
    if os.path.exists(orig_path):
        orig = util.read_json(orig_path)
        out["diff_vs_metrics_json"] = diff_metrics(orig, out)
    util.write_json(os.path.join(run_dir, "recomputed.json"), out)
    return out


def audit_export(cfg: Config, project_root: str, run_dir: str,
                 out_dir: Optional[str] = None,
                 max_bytes: int = 200 * 1024 * 1024,
                 max_file_bytes: int = 50 * 1024 * 1024) -> Dict:
    """生成审核包并写出 download-manifest.json。

    只导出 outputs/runs/ 下的审核产物；原始数据、全量派生视图、索引与环境
    留在服务器（AGENTS.md SFTP 规则）。总量或单文件超限时如实报告并列为
    server-only，不静默删除必要证据。
    """
    rid = os.path.basename(run_dir.rstrip("/"))
    out_dir = out_dir or os.path.join(project_root, "outputs", "review", rid)
    util.ensure_dir(out_dir)

    wanted = ["audit-samples.html", "audit-selection.json", "audit.jsonl",
              "certificates.jsonl", "normalization.jsonl", "checks.json",
              "metrics.json", "run-status.json", "manifest.json",
              "recomputed.json", "verify.json", "sampling.json"]
    entries, server_only = [], []
    total = 0
    for name in wanted:
        src = os.path.join(run_dir, name)
        if not os.path.exists(src):
            continue
        size = os.path.getsize(src)
        rel = os.path.join(rid, name)
        if size > max_file_bytes or total + size > max_bytes:
            server_only.append({
                "path": rel, "bytes": size,
                "reason": ("单文件超过 50 MB" if size > max_file_bytes
                           else "审核包总量将超过 200 MB"),
                "note": "必要证据保留在服务器，未删除；如需请单独按需下载。"})
            continue
        dst = os.path.join(out_dir, name)
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                b = fi.read(1 << 20)
                if not b:
                    break
                fo.write(b)
        entries.append({"path": name, "bytes": size,
                        "sha256": util.sha256_file(dst)})
        total += size

    manifest = {
        "run_id": rid,
        "generated_at": util.now_iso(),
        "experiment": cfg.raw["experiment"],
        "release": cfg.release_id,
        "config_sha256": cfg.digest,
        "files": entries,
        "n_files": len(entries),
        "total_bytes": total,
        "total_mib": round(total / 1024 / 1024, 3),
        "limits": {"total_bytes": max_bytes, "per_file_bytes": max_file_bytes},
        "within_limits": total <= max_bytes,
        "server_only": server_only,
        "server_only_note": ("以下内容按规则留在服务器，未纳入下载包："
                             "原始 .bz2 数据、全量派生视图（edges/types/labels/"
                             "qid_map）、运行日志与环境。"),
    }
    util.write_json(os.path.join(out_dir, "download-manifest.json"), manifest)
    # manifest 自身的哈希不含在 files 内（避免自指），由交付摘要记录
    return manifest


def diff_metrics(orig: Dict, new: Dict) -> Dict:
    """比较关键比率的复算一致性。"""
    keys = ("denominator_valid_candidates", "R_strict", "R_completion", "R_multi")
    o = orig.get("track_a", {})
    n = new.get("track_a", {})
    return {k: {"metrics_json": o.get(k), "recomputed": n.get(k),
                "match": o.get(k) == n.get(k)} for k in keys}
