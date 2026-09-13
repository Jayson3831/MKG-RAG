"""E001 公共工具：IRI、命名空间、哈希与 JSONL 读写。

保持无副作用、可单测。所有时间戳带时区（AGENTS.md）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
TZ_NAME = "Asia/Shanghai"

NS = {
    "dbo": "http://dbpedia.org/ontology/",
    "dbr": "http://dbpedia.org/resource/",
    "dbp_fr": "http://fr.dbpedia.org/resource/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "wkd": "http://wikidata.dbpedia.org/resource/",
}
NS_REV = {v: k for k, v in NS.items()}

# 语言图的资源命名空间：en 用无语言前缀的 dbpedia.org/resource
LANG_RES_NS = {"en": NS["dbr"], "fr": NS["dbp_fr"]}


def now_iso() -> str:
    """带时区的当前时间戳。"""
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_json(obj) -> str:
    """对 JSON 做规范化序列化后取哈希，键序稳定。"""
    return sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":")))


def curie(iri: str) -> str:
    """完整 IRI -> 前缀式，如 dbo:birthPlace；无匹配则原样返回。"""
    for prefix, base in NS.items():
        if iri.startswith(base):
            return f"{prefix}:{iri[len(base):]}"
    return iri


def expand(curie_or_iri: str) -> str:
    if curie_or_iri.startswith("http://") or curie_or_iri.startswith("https://"):
        return curie_or_iri
    if ":" in curie_or_iri:
        p, rest = curie_or_iri.split(":", 1)
        if p in NS:
            return NS[p] + rest
    return curie_or_iri


def local_name(iri: str) -> str:
    """去掉资源命名空间前缀，保留本地标识（含括号消歧后缀）。"""
    for base in (NS["dbr"], NS["dbp_fr"], NS["dbo"], NS["wkd"]):
        if iri.startswith(base):
            return iri[len(base):]
    return iri.rsplit("/", 1)[-1]


def norm_answer(iri: str) -> str:
    """答案归一化：去命名空间前缀后小写，用于集合比较。

    仅用于比较，不作唯一标识；未归一化答案另行保存（任务书 §7.8）。
    """
    return local_name(iri).lower()


# --- 流式解析 ---------------------------------------------------------------

# 匹配一行 TTL/N-Triples：<s> <p> <o> . 或 <s> <p> "lit"@lang .
_LINE_RE = re.compile(r"^<([^>]+)>\s+<([^>]+)>\s+(.*?)\s*\.\s*$")
_LIT_RE = re.compile(r'^"(.*)"(?:@([a-zA-Z-]+)|\^\^<([^>]+)>)?$')


def parse_line(line: str):
    """解析一行三元组。

    返回 (subject, predicate, object, obj_kind, obj_lang) 或 None。
    obj_kind ∈ {'iri','literal','bnode'}。非三元组行返回 None。
    """
    line = line.rstrip("\n")
    if not line or line.startswith("#"):
        return None
    m = _LINE_RE.match(line)
    if not m:
        return None
    s, p, o = m.group(1), m.group(2), m.group(3)
    if o.startswith("<") and o.endswith(">"):
        return s, p, o[1:-1], "iri", None
    if o.startswith("_:"):
        return s, p, o, "bnode", None
    lm = _LIT_RE.match(o)
    if lm:
        return s, p, lm.group(1), "literal", lm.group(2)
    return s, p, o, "literal", None


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_json(path: str, obj) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_jsonl(path: str, rows) -> int:
    ensure_dir(os.path.dirname(path))
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def read_jsonl(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
