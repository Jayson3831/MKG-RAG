"""E001：DBpedia 多语言知识图谱跨图事实互补性审计。

模块划分：
    util       公共工具（IRI、命名空间、哈希、JSONL）
    config     冻结配置的加载与校验
    prepare    WP1/WP2 输入下载核验与可追溯派生视图
    graph      内存索引、参考映射与查询执行
    sample     WP4 分层抽样与独立总体样本
    tracks     WP4 轨道 A / 轨道 B 候选生成
    judge      WP5 必要性复核与互斥状态分类
    metrics    WP3/WP6 指标与复算
    audit      WP6 人工审核包与诊断包导出
    verify     WP6 指定样本的独立复核（直读原始 .bz2）
    pipeline   完整流水线编排
"""

__all__ = ["util", "config", "prepare", "graph", "sample", "tracks",
           "judge", "metrics", "audit", "verify", "pipeline"]
