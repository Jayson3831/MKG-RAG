"""评测图的内存索引、参考映射与查询执行。

单图与联合查询使用相同的白名单、语义和标准化规则（任务书 §7.3）；
所有答案可追溯到输入三元组（来源文件 id + 未压缩行号）。
映射边只连接身份，不计为答案事实（任务书 §7.4）。
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import util

Edge = Tuple[str, str, str, str, int]   # object, kind, obj_lang, src_id, lineno


class Graph:
    """单个语言图的索引。仅包含白名单关系，不截断出边。"""

    def __init__(self, lang: str):
        self.lang = lang
        self.edges: Dict[str, Dict[str, List[Edge]]] = {}
        # 反向索引：object -> predicate -> list of (subject, src_id, lineno)
        # 用于逆关系替代路径复核（任务书 §7.5）。
        self.rev: Dict[str, Dict[str, List[Tuple]]] = {}
        self.types: Dict[str, Set[str]] = {}
        self.labels: Dict[str, str] = {}
        self.res_ns = util.LANG_RES_NS[lang]

    def load(self, project_root: str, proc_dir: str = "data/processed/e001") -> None:
        base = os.path.join(project_root, proc_dir)
        with open(os.path.join(base, f"edges_{self.lang}.tsv"), encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 7:
                    continue
                s, p, o, kind, olang, src, lineno = parts[:7]
                self.edges.setdefault(s, {}).setdefault(p, []).append(
                    (o, kind, olang, src, int(lineno)))
                if kind == "iri":
                    self.rev.setdefault(o, {}).setdefault(p, []).append(
                        (s, src, int(lineno)))
        tp = os.path.join(base, f"types_{self.lang}.tsv")
        if os.path.exists(tp):
            with open(tp, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) == 2:
                        self.types.setdefault(parts[0], set()).add(parts[1])
        lp = os.path.join(base, f"labels_{self.lang}.tsv")
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 2 and parts[0] not in self.labels:
                        self.labels[parts[0]] = parts[1]

    @classmethod
    def from_triples(cls, lang: str, triples: Iterable[Tuple]) -> "Graph":
        """用内存三元组构造图，仅供正确性测试使用的微型合成图。

        triples 元素为 (subject, predicate_curie_or_iri, object, kind)，
        可选第 5 项为来源标签。与 load() 产生完全相同的内部结构，
        因此测试跑的是与正式运行同一条代码路径。
        """
        g = cls(lang)
        for t in triples:
            s, p, o, kind = t[0], t[1], t[2], t[3]
            src = t[4] if len(t) > 4 else "synthetic"
            pc = util.curie(util.expand(p))
            g.edges.setdefault(s, {}).setdefault(pc, []).append((o, kind, None, src, 0))
            if kind == "iri":
                g.rev.setdefault(o, {}).setdefault(pc, []).append((s, src, 0))
        return g

    def add_type(self, subj: str, cls_curie: str) -> None:
        self.types.setdefault(subj, set()).add(cls_curie)

    # --- 访问 ---
    def out(self, subj: str, pred: str) -> List[Edge]:
        return self.edges.get(subj, {}).get(pred, [])

    def incoming(self, obj: str, pred: str) -> List[Tuple]:
        """逆关系：返回 (subject, src_id, lineno)。"""
        return self.rev.get(obj, {}).get(pred, [])

    def subjects(self) -> Iterable[str]:
        return self.edges.keys()

    def degree(self, subj: str) -> int:
        return sum(len(v) for v in self.edges.get(subj, {}).values())

    def has_class(self, subj: str, cls: str) -> bool:
        return cls in self.types.get(subj, ())

    def label(self, subj: str) -> str:
        return self.labels.get(subj, util.local_name(subj))


class Alignment:
    """评测参考映射：QID <-> 各语言本地资源。

    与原图分开存储，仅用于评测侧对齐；运行时可见性属 E002（任务书 §2.5）。
    """

    def __init__(self, langs: List[str]):
        self.langs = langs
        self.by_qid: Dict[str, Dict[str, str]] = {}
        self.qid_of: Dict[Tuple[str, str], str] = {}   # (lang, iri) -> qid

    @classmethod
    def from_map(cls, langs: List[str], mapping: Dict[str, Dict[str, str]]) -> "Alignment":
        """用内存映射构造对齐，仅供正确性测试。"""
        a = cls(langs)
        for qid, d in mapping.items():
            a.by_qid[qid] = dict(d)
            for l, iri in d.items():
                a.qid_of[(l, iri)] = qid
        return a

    def load(self, project_root: str, proc_dir: str = "data/processed/e001") -> None:
        path = os.path.join(project_root, proc_dir, "qid_map.tsv")
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 1 + len(self.langs):
                    continue
                qid = parts[0]
                d = {}
                for i, l in enumerate(self.langs):
                    iri = parts[1 + i]
                    if iri:
                        d[l] = iri
                        self.qid_of[(l, iri)] = qid
                self.by_qid[qid] = d

    def translate(self, iri: str, from_lang: str, to_lang: str) -> Optional[str]:
        """把某语言图内的实体换成另一语言图内的同一实体；无映射返回 None。

        返回 None 表示映射缺失，调用方须记为未决（unresolved），
        不得当作「单图不存在该事实」。
        """
        qid = self.qid_of.get((from_lang, iri))
        if qid is None:
            return None
        return self.by_qid.get(qid, {}).get(to_lang)

    def shared_iris(self, lang: str) -> Set[str]:
        return {d[lang] for d in self.by_qid.values() if lang in d}

    def qid(self, lang: str, iri: str) -> Optional[str]:
        return self.qid_of.get((lang, iri))

    def canon(self, lang: str, iri: str) -> str:
        """答案身份归一，仅用于集合比较。

        优先用参考映射的 QID；映射缺失时回退到 (语言, 本地名小写)，并带上语言
        前缀以免把两图同名但未证实同一的实体误判为同一个。
        未归一化的原值另行保存，不覆盖（任务书 §7.8）。
        """
        q = self.qid_of.get((lang, iri))
        if q is not None:
            return q
        return f"{lang}:{util.norm_answer(iri)}"


# ------------------------------------------------------------------ 查询执行

def query_single(graph: Graph, start: str, chain: List[str]):
    """单图正向求解 chain。返回 (answers, paths)。

    answers 为终点 IRI 集合；paths 为每条推导的边序列，用于证据证书。
    """
    frontier = [(start, [])]
    for hop in chain:
        nxt = []
        for node, path in frontier:
            for rec in graph.out(node, hop):
                if rec[1] != "iri":
                    continue          # 字面量不作实体路径中间点
                nxt.append((rec[0], path + [(graph.lang, node, hop, rec)]))
        frontier = nxt
        if not frontier:
            break
    return {n for n, _ in frontier}, [p for _, p in frontier]


def query_joint(graphs: Dict[str, Graph], align: Alignment, start_lang: str,
                start: str, chain: List[str], split: List[str]):
    """跨图求解。split[i] 指定 chain[i] 从哪个图读取事实；跨图处经参考映射换身份。

    split 为空表示**并集语义**（集合补全模板）：每跳在所有图中分别求解，
    答案取并集。这与逐跳指定图归属的严格连接语义不同，两者不得混用。

    返回 (answers, records, unresolved)：
      answers    终点 IRI 集合
      records    [(answer, path, bridges)]，path 为 (g_lang, s, p, edge) 序列
      unresolved 因映射缺失而中断的分支计数（单列为未决，不记为负例）
    """
    union = not split
    if not union and len(split) != len(chain):
        raise ValueError("split 长度必须与 chain 一致，或留空表示并集语义")
    targets_for = (lambda i: list(graphs.keys())) if union else (lambda i: [split[i]])

    frontier = [(start, start_lang, [], [])]
    unresolved = 0
    for i, hop in enumerate(chain):
        nxt = []
        for node, node_lang, path, bridges in frontier:
            for g_lang in targets_for(i):
                if node_lang != g_lang:
                    tgt = align.translate(node, node_lang, g_lang)
                    if tgt is None:
                        unresolved += 1
                        continue
                    nd, nl = tgt, g_lang
                    br = bridges + [{"lang": g_lang, "iri": tgt,
                                     "from_lang": node_lang}]
                else:
                    nd, nl, br = node, node_lang, bridges
                for rec in graphs[g_lang].out(nd, hop):
                    if rec[1] != "iri":
                        continue
                    nxt.append((rec[0], g_lang, path + [(g_lang, nd, hop, rec)], br))
        frontier = nxt
        if not frontier:
            break
    answers = {n for n, _, _, _ in frontier}
    return answers, [(n, p, b) for n, _, p, b in frontier], unresolved
