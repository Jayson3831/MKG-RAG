#!/usr/bin/env python3
"""E001 最小接口 CLI。

    prepare        下载并核验输入，构建可追溯派生视图
    run            执行正式运行，产出运行目录全部交付物
    recompute      只读 candidates.jsonl 复算指标，与 metrics.json 对账
    verify         指定 question_id 独立复核（直读原始 .bz2）
    audit-export   生成审核包与 download-manifest.json

正式运行前必须提交代码，使 manifest.json 记录到真实的 code_commit。
复现命令示例：

    python scripts/run_e001.py prepare --config configs/e001.yaml
    python scripts/run_e001.py run     --config configs/e001.yaml
    python scripts/run_e001.py recompute --run-dir outputs/runs/<id>
    python scripts/run_e001.py verify  --run-dir outputs/runs/<id> --sample 30
    python scripts/run_e001.py audit-export --run-dir outputs/runs/<id>
"""
from __future__ import annotations

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from e001 import pipeline, util, verify as verify_mod          # noqa: E402
from e001.config import ConfigError, load as load_config       # noqa: E402
from e001.graph import Alignment                               # noqa: E402
from e001.prepare import prepare as run_prepare                # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg(args):
    path = args.config
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    return load_config(path)


def _run_dir(args):
    d = args.run_dir
    if not os.path.isabs(d):
        d = os.path.join(PROJECT_ROOT, d)
    if not os.path.isdir(d):
        raise SystemExit(f"运行目录不存在: {d}")
    return d


def cmd_prepare(args) -> int:
    cfg = _cfg(args)
    res = run_prepare(cfg, PROJECT_ROOT, skip_download=args.skip_download)
    util.write_json(os.path.join(PROJECT_ROOT, "outputs", "prepare.json"), res)
    print(f"prepare 状态: {res['status']}")
    if res["status"] != "ok":
        print(f"受阻: {res.get('blocked_reason')}")
        return 1
    for lang in cfg.languages:
        print(f"  {lang} edges: {res['edges'][lang]['total']}")
    print(f"  qid_map: {res['qid_map']['qids_total']} 个 QID，"
          f"{res['qid_map']['qids_with_all_langs']} 个含全部语言")
    return 0


def cmd_run(args) -> int:
    cfg = _cfg(args)
    if args.run_dir:
        run_dir = _run_dir(args)
    else:
        run_dir = os.path.join(PROJECT_ROOT, pipeline.run_id(cfg.raw["experiment"]))
    command = "python " + " ".join(sys.argv)
    status = pipeline.run(cfg, PROJECT_ROOT, run_dir, command, limit=args.limit)
    print(f"运行目录: {run_dir}")
    print(f"工程验收: {status['engineering_acceptance']['status']} "
          f"({status['engineering_acceptance']['checks_passed']}/"
          f"{status['engineering_acceptance']['checks_total']})")
    print(f"研究审核: {status['research_review']['status']}")
    return 0 if status["worktree_dirty"] is False else 0


def cmd_recompute(args) -> int:
    cfg = _cfg(args)
    out = pipeline.recompute_run(cfg, PROJECT_ROOT, _run_dir(args))
    ta = out["track_a"]
    print(f"复算轨道 A 分母: {ta['denominator_valid_candidates']}，"
          f"R_strict={ta['R_strict']}，R_completion={ta['R_completion']}，"
          f"R_multi={ta['R_multi']}")
    for k, v in out.get("diff_vs_metrics_json", {}).items():
        print(f"  {k}: {'一致' if v['match'] else '不一致'} "
              f"({v['metrics_json']} vs {v['recomputed']})")
    return 0


def cmd_verify(args) -> int:
    cfg = _cfg(args)
    run_dir = _run_dir(args)
    ledger = list(util.read_jsonl(os.path.join(run_dir, "candidates.jsonl")))
    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    else:
        # 默认复核正式审核样本本身
        sel_path = os.path.join(run_dir, "audit-selection.json")
        if os.path.exists(sel_path):
            sel = util.read_json(sel_path)
            ids = [x["question_id"] for x in sel["formal"]["selected"]]
        else:
            rnd = random.Random(cfg.raw["audit"]["review_seed"])
            pool = [x["question_id"] for x in ledger if x.get("track") == "A"]
            ids = rnd.sample(pool, min(args.sample, len(pool)))
    if args.limit:
        ids = ids[:args.limit]
    align = Alignment(cfg.languages)
    align.load(PROJECT_ROOT)
    res = verify_mod.verify_ids(cfg, PROJECT_ROOT, align, ledger, ids)
    util.write_json(os.path.join(run_dir, "verify.json"), res)
    print(f"独立复核 {res['found']}/{len(ids)} 条；"
          f"与记录联合答案一致 {res['n_matches_recorded_joint']} 条，"
          f"一致率 {res['agreement_rate']}")
    if res["missing_ids"]:
        print(f"  未在账本中找到: {res['missing_ids'][:10]}")
    return 0


def cmd_audit_export(args) -> int:
    cfg = _cfg(args)
    m = pipeline.audit_export(cfg, PROJECT_ROOT, _run_dir(args),
                              out_dir=args.out,
                              max_bytes=args.max_bytes * 1024 * 1024,
                              max_file_bytes=args.max_file_mb * 1024 * 1024)
    print(f"审核包 {m['n_files']} 个文件，共 {m['total_mib']} MiB")
    if m["server_only"]:
        print(f"  超限保留在服务器: {len(m['server_only'])} 项")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="E001 DBpedia 多语言跨图审计")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--config", default="configs/e001.yaml")

    sp = sub.add_parser("prepare", help="下载核验输入并构建派生视图")
    common(sp)
    sp.add_argument("--skip-download", action="store_true")
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("run", help="执行正式运行")
    common(sp)
    sp.add_argument("--run-dir", default=None)
    sp.add_argument("--limit", type=int, default=None,
                    help="仅冒烟测试用，截断抽样实体数；正式运行不得设置")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("recompute", help="从候选账本复算指标")
    common(sp)
    sp.add_argument("--run-dir", required=True)
    sp.set_defaults(func=cmd_recompute)

    sp = sub.add_parser("verify", help="指定样本独立复核")
    common(sp)
    sp.add_argument("--run-dir", required=True)
    sp.add_argument("--ids", default=None, help="逗号分隔的 question_id")
    sp.add_argument("--sample", type=int, default=30)
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("audit-export", help="生成审核包与下载清单")
    common(sp)
    sp.add_argument("--run-dir", required=True)
    sp.add_argument("--out", default=None)
    sp.add_argument("--max-bytes", type=int, default=200, help="总量上限 MiB")
    sp.add_argument("--max-file-mb", type=int, default=50, help="单文件上限 MiB")
    sp.set_defaults(func=cmd_audit_export)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"缺少必要输入: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
