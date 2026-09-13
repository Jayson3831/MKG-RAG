"""冻结配置的加载、校验与哈希。

配置哈希在正式统计前写入运行目录 manifest；运行中不得修改配置对象。
"""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml

from . import util


class ConfigError(RuntimeError):
    pass


class Config:
    def __init__(self, raw: Dict[str, Any], path: str, digest: str):
        self.raw = raw
        self.path = path
        self.digest = digest          # 配置文件内容的 SHA-256
        self._frozen = True

    # --- 便捷访问 ---
    @property
    def release_id(self) -> str:
        return self.raw["release"]["id"]

    @property
    def languages(self):
        return list(self.raw["release"]["languages"])

    def __getitem__(self, k):
        return self.raw[k]

    def get(self, k, default=None):
        return self.raw.get(k, default)

    # --- 校验 ---
    def validate(self) -> None:
        errs = []
        r = self.raw
        for key in ("experiment", "release", "inputs", "relations", "templates",
                    "sampling", "budget", "judgement", "metrics", "audit",
                    "alternative_paths"):
            if key not in r:
                errs.append(f"缺少配置节: {key}")
        if "en" not in self.languages or "fr" not in self.languages:
            errs.append("release.languages 必须同时包含 en 与 fr")
        # 关系白名单必须是 dbo: 共享 ontology
        for rel in r.get("relations", {}):
            if not rel.startswith("dbo:"):
                errs.append(f"关系白名单仅允许共享 ontology 属性: {rel}")
        # 模板跳必须都在白名单内
        rels = set(r.get("relations", {}))
        for t in r.get("templates", []) + r.get("completion_templates", []):
            for hop in t["chain"]:
                if hop not in rels:
                    errs.append(f"模板 {t['id']} 的跳 {hop} 不在关系白名单内")
            if "split" in t and len(t["split"]) != len(t["chain"]):
                errs.append(f"模板 {t['id']} 的 split 长度与 chain 不一致")
            if t.get("split") and set(t["split"]) - set(self.languages):
                errs.append(f"模板 {t['id']} 的 split 含未声明的语言")
        # 空白分母口径
        if r["metrics"].get("empty_denominator_repr", "null") != None:
            errs.append("空分母必须表示为 null，避免输出 0% 造成误导")
        # 人工状态初始必须 pending
        if r["audit"].get("human_status_initial") != "pending":
            errs.append("audit.human_status_initial 初始必须为 pending")
        if errs:
            raise ConfigError("配置校验失败:\n  - " + "\n  - ".join(errs))

    def summary(self) -> Dict[str, Any]:
        """可写入 manifest 的摘要（不含大对象）。"""
        return {
            "experiment": self.raw["experiment"],
            "config_version": self.raw["config_version"],
            "config_path": os.path.basename(self.path),
            "config_sha256": self.digest,
            "release": self.raw["release"]["id"],
            "languages": self.languages,
            "seed": self.raw["sampling"]["seed"],
            "entity_target": self.raw["sampling"]["entity_target"],
            "track_a_candidates": self.raw["budget"]["track_a_candidates"],
            "track_b_attempts": self.raw["budget"]["track_b_attempts"],
            "n_templates": len(self.raw["templates"]),
            "n_completion_templates": len(self.raw["completion_templates"]),
        }


def load(path: str) -> Config:
    if not os.path.exists(path):
        raise ConfigError(f"配置不存在: {path}")
    with open(path, "rb") as f:
        blob = f.read()
    raw = yaml.safe_load(blob.decode("utf-8"))
    cfg = Config(raw, path, util.sha256_bytes(blob))
    cfg.validate()
    return cfg
