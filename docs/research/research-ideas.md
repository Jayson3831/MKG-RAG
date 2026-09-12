# 多图谱 Agentic GraphRAG 研究思路

> 持续迭代的当前研究方案。更新：2026-09-11。状态：待验证，尚未形成整体可行性结论。
> 主章节呈现现阶段采用的方案；淘汰与替代过程单列于第 12 章。方法见 [验证计划](../experiments/validation-plan.md)，执行要求见 [E001 任务书](../tasks/E001.md)，验证后的分析见 [验证记录](../experiments/validation-log.md)。

## 1. 一句话定位

研究目标是：在多个彼此独立、仅部分对齐甚至没有显式映射的知识图谱中，让 Agent 根据问题动态选择图谱，在线完成实体/关系/Schema 对齐，拼接跨图证据路径，并在来源冲突、版本差异和检索成本约束下给出可验证答案或主动拒答。

核心是在查询时发现、验证并利用图谱之间的连接关系。

## 2. 拟解决的问题

给定多个图谱：

\[
\mathcal{G}=\{G_1,G_2,\ldots,G_n\}
\]

给定自然语言问题 \(q\)，Agent 需要联合完成：

1. 识别问题中的信息角色和约束；
2. 发现需要访问的最小图谱集合；
3. 在线对齐实体、关系、类、属性和值；
4. 在各图谱中检索局部子图；
5. 通过不确定映射拼接跨图证据路径；
6. 处理时间、来源、粒度和事实冲突；
7. 决定继续检索、回答或拒答。

多图必要性以固定快照、明确的评测图范围、查询语义和允许推理规则为边界。联合查询的参考答案为 A*；若联合证据足以支持完整答案，而任一单图都不能完整支持 A*，则属于多图必要问题。

区分两种情况：

- **严格跨图连接**：各单图答案集合均为空，联合证据才能完成有效答案的推导；
- **答案集合补全**：各单图都不能给出完整答案集合，但至少一个单图能给出部分答案。应与严格跨图连接分开报告。

单图可回答性在声明的完整评测范围内核验，覆盖等价关系、逆关系及其他允许的替代证据路径。检索失败与查询超时单列为未决状态；可回答性结论限定于当前快照和推理规则。答案采用实体或值的集合表示。

## 3. 术语与五类实验方案

### 3.1 同源/近同构与异源/异构

- **同源/近同构**：图谱来自同一知识生态、相同或相近 Schema，实体类型和关系语义大体可对应，但事实集合应具有互补性。
- **异源/异构**：图谱来自不同机构、抽取管线或领域，实体类型、Schema、关系粒度、访问协议和可信度均可能不同。

同源图谱的实验价值由其有效互补事实所支持的问题数量、多样性和质量共同决定。

### 3.2 五类实验

1. **多语言图**：DBpedia-en/fr/de 等。适合跨语言实体对齐和语言特有事实，但常见实体和事实可能高度重合，应先测量。
2. **跨时间图**：DBpedia-TKG 或 Wikidata 不同快照。适合历史状态、事实删除/修改和时间冲突；普通新版快照往往不能单独证明多图互补。
3. **拆分图**：从 Wikidata/DBpedia 投影出 Schema 相同但关系、主题或数据来源不同的 named graphs。最容易严格控制“必须访问多个图”，但对真实对齐的考验较弱。
4. **近同构图**：DBpedia+YAGO+Wikidata，OpenAlex+OpenAIRE+Crossref，OpenStreetMap+GeoNames+Wikidata 等。实体类型相近，可作为自然互补候选；来源依赖、共享上游和事实重合需单独核查，不能默认独立。
5. **异源图**：网络安全、医学、法律、文化遗产等领域的不同类型图谱。最能体现 Schema 对齐、跨图 Join、冲突和来源验证。

## 4. 当前研究进展与研究缺口

### 4.1 Agentic GraphRAG

- [Think-on-Graph](https://arxiv.org/abs/2307.07697)：LLM 逐步选择实体和关系进行 KG 探索。
- [KG-Agent](https://aclanthology.org/2025.acl-long.468/)：工具调用、KG executor 和记忆。
- [Graph-R1](https://arxiv.org/abs/2507.21892)：多轮图检索和 RL。
- [A2RAG](https://arxiv.org/abs/2601.21162)：证据充分性控制、成本感知和逐级检索。
- [ARK](https://aclanthology.org/2026.acl-long.714/)：在全局搜索和局部图扩展之间自适应切换。
- [LegalGraphRAG](https://aclanthology.org/2026.acl-long.1738/)：Researcher、Auditor、Adjudicator 多 Agent 验证框架。
- [Agentic GraphRAG Survey](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6713979)：2026 年总体分类综述。

共同边界：绝大多数工作在单一逻辑图上运行；论文中的 heterogeneous graph 往往指一张图内部的异质节点，而不是多个自治 KG 之间的异构对齐。

### 4.2 多 KG 对齐与联邦 KGQA

- [OpenEA](https://vldb.org/pvldb/vol13/p2326-sun.pdf)：实体对齐基准和方法综述。
- [MultiEA](https://arxiv.org/abs/2408.00662)：三个以上 KG 的实体对齐。
- [EA-Agent](https://aclanthology.org/2026.acl-long.1420/)：多步 Agent 实体对齐。
- [SchemaForge](https://arxiv.org/abs/2508.01815)：异构 KG 集合中的 Schema 选择、Text-to-SPARQL 和反事实验证。
- [Agentic SPARQL](https://arxiv.org/abs/2603.06582)：endpoint discovery、Schema exploration、source selection 和联邦 KGQA benchmark。
- [RAG in the Wild](https://arxiv.org/abs/2507.20059)：混合知识源中的 source routing 和真实场景失败模式。

共同边界：

- 实体对齐通常是离线、成对、全局的；
- 联邦查询通常假设 Schema 映射和 endpoint 已知；
- 多 KG QA 常常通过文本化或分别检索规避显式对齐；
- 缺少“查询时局部对齐 + 跨图证据链 + 冲突校准”的闭环。

## 5. 候选知识图谱

### 5.1 同源/近同构

| 图谱 | 用途 | 注意事项 |
|---|---|---|
| DBpedia-en/fr/de/ja | 跨语言实验 | 固定同一 release 的语言独立抽取文件，核对源 dump 日期与配置；避免来源混合或已统一实体标识的集合掩盖任务 |
| YAGO3 | 多语言、时间和空间属性 | 旧版本，适合作为稳定基线；[下载](https://yago-knowledge.org/downloads/yago-3) |
| YAGO4 | Wikidata、Schema.org 和 SHACL | 与 DBpedia/Wikidata 有实体链接；[下载](https://yago-knowledge.org/downloads/yago-4) |
| Wikidata 快照 | 跨版本、关系投影拆分 | 全量很大，建议先做主题子图 |
| DBpedia-TKG | 历史版本和时间事实 | 适合时间推理，不应直接等同于独立互补图 |
| DBpedia + YAGO + Wikidata | 自然近同构互补 | 必须实测事实重合率 |
| OpenAlex + OpenAIRE + Crossref | 科研论文、作者、机构和引用 | 共享论文实体，但元数据和引用边互补 |
| OpenStreetMap + GeoNames + Wikidata | 地理地点和语义类型 | 适合地点、行政区和设施问题 |

### 5.2 异源

#### 网络安全：首选主实验

```text
资产图/CMDB
NVD/CVE/CPE
MITRE ATT&CK
CISA KEV
OSV/GitHub Advisory
```

问题示例：哪些服务器运行了已被 KEV 收录的漏洞软件？对应哪些 ATT&CK 技术？是否有修复版本？

来源：

- [NVD](https://www.nist.gov/itl/nvd)
- [MITRE ATT&CK](https://attack.mitre.org/)
- [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
- [OSV](https://osv.dev/)

#### 生物医学：第二个异源实验

```text
合成患者图
Human Phenotype Ontology
Disease Ontology/MONDO
Open Targets
Reactome
ChEMBL
ClinicalTrials.gov
```

问题示例：根据患者症状和检验结果，哪些疾病候选匹配？对应哪些靶点和通路？是否有符合年龄和阶段条件的临床试验？

注意：使用合成患者图，不直接使用真实患者数据；DrugBank 访问和再分发限制较多，不建议作为公开 benchmark 核心数据源。

来源：[Disease Ontology](https://www.sparql.disease-ontology.org/)、[Open Targets](https://platform-docs.opentargets.org/)、[Reactome](https://reactome.org/)、[ChEMBL](https://www.ebi.ac.uk/chembl/)、[ClinicalTrials.gov API](https://clinicaltrials.gov/data-about-studies/learn-about-api)

#### 其他可选异源领域

- 科研：OpenAlex + 论文/数据集图 + GitHub/Zenodo。
- 文化遗产：Europeana + Getty AAT/ULAN + Wikidata + GeoNames/OSM。
- 法律合规：EUR-Lex/ELI + CourtListener + 企业/制裁图。
- 软件供应链：GitHub 依赖图 + OSV/NVD + 包仓库元数据。

## 6. DBpedia 多语言适用性验证原则

两轮验证路线及共用口径维护于 [验证计划](../experiments/validation-plan.md)，第一轮执行方案及指标见 [E001 任务书](../tasks/E001.md)。[验证记录](../experiments/validation-log.md) 仅收录验证完成后的分析结论。本节保留当前研究原则。

1. **分轮验证**：第一轮确认真实互补及跨图问题的可构造性；第二轮测试在线对齐、证据连接、选图和成本。
2. **来源与局部身份**：使用语言独立文件，保留各图实体标识、三元组来源及原始值。QID 和跨语言链接作为经过抽查的评测参考映射，运行时可见性由实验条件指定。
3. **语义标准化**：共享 ontology 谓词使用完整标识；本地属性使用经核验的显式语义映射。日期保留精度，数值按兼容单位转换，文本保留语言标签和原文；无法确认等价的项单列。
4. **证据范围**：分层抽样实体，完整读取目标关系邻域。局部邻域用于候选发现；单图必要性复核覆盖声明的完整评测图及允许推理范围。
5. **差异审计**：有效互补、抽取错误、映射错误、时间差异、粒度差异与事实冲突分别记录。
6. **独立统计口径**：固定候选采样规则后统计多图必要率；定向挖掘轨道独立报告合格样本数、通过率和多样性。
7. **能力覆盖**：mapping-based 数据验证实体对齐和事实连接，本地 infobox 属性用于关系对齐扩展；双图实验验证连接，多个候选图和混合可回答性问题验证选图、停止与拒答。
8. **适用性判断**：综合问题数量、实体与模板多样性、人工核验质量、对齐覆盖率及泄漏检查结果，确定图谱承担的实验角色。

数据依据：DBpedia 官方区分使用本地属性的 Generic Extraction 与基于映射规则的 Mappings-based Extraction，后者覆盖范围与前者不同。参考 [官方数据说明](https://www.dbpedia.org/resources/individual/) 与 [发布说明](https://www.dbpedia.org/resources/snapshot-release/)。这些说明不代替具体文件的核验。

## 7. 方法框架

### 7.1 离线部分

为每个图谱建立：

1. Graph Card：实体类型、关系、语言、时间、接口、成本和权限；
2. Schema embedding 索引；
3. 实体标签、别名和外部 ID 索引；
4. 本地图内的向量＋符号检索索引；
5. 按实验条件许可的候选跨图映射索引。

参考答案所用的评测映射应与运行时索引隔离；不得通过 QID、sameAs、统一 URI、缓存或 Graph Card 隐式暴露被隐藏的测试映射。

不提前合并全部实体和三元组。

### 7.2 在线 Agent 流程

```text
问题解析
→ 信息角色识别
→ Graph Card 检索
→ 候选图谱选择
→ 局部实体/关系/Schema 对齐
→ 各图局部检索
→ 跨图路径拼接
→ 类型、时间和来源验证
→ 继续检索、回答或拒答
```

图谱选择可以优化：

\[
S^*=\arg\max_S[Coverage(q,S)+BridgeConfidence(S)-\lambda Cost(S)]
\]

映射评分应综合：

- 标签和别名相似度；
- 类型兼容性；
- 邻居结构；
- 关系兼容性；
- 时间一致性；
- 来源可信度。

## 8. QA 和 Agent 样本设计

每个样本保存跨图证据证书；仅在最小性已验证时称为“最小证据”。以下是严格跨图连接样本的结构示意：

```json
{
  "question": "...",
  "answer": "...",
  "required_graphs": ["G1", "G2"],
  "bridge_mappings": ["..."],
  "evidence_path": ["..."],
  "single_graph_results": {"G1": [], "G2": []},
  "necessity_type": "strict_join",
  "evaluation_scope": "固定快照、评测图及允许推理规则的配置引用",
  "alternative_path_check": "复核记录引用",
  "conflict_state": "none"
}
```

样本生成流程：

```text
按固定采样规则生成候选 / 定向挖掘跨图候选（分别统计）
→ 得到结构化查询、候选证据、答案和 provenance
→ 分别执行单图/多图查询
→ 复核替代证据路径、映射与事实质量
→ 将样本标记为严格跨图连接、集合补全、单图可回答或证据不足
→ 为主跨图子集筛选，同时保留对照与失败原因
→ 生成自然语言问题并检查语义一致性
```

建议覆盖：

- 跨图多跳事实问答；
- 跨语言实体对齐；
- 跨时间状态查询；
- 多图比较和排序；
- 冲突识别与来源选择；
- Agent 选图、继续检索、停止或拒答。

主划分按跨语言实体等价类分组，防止同一实体的不同语言版本跨训练/测试泄漏；另设问题模板、图谱或时间泛化划分。记录桥接实体及邻域重叠，不默认所有划分轴必须同时使用。

## 9. 基线与指标

### 基线

1. 每个单图独立 GraphRAG；
2. 所有图谱广播检索；
3. 离线对齐后合并图；
4. Oracle source selection；
5. Oracle alignment + GraphRAG；
6. 在线 alignment-aware Agent。

### 指标

- Answer EM/F1；
- Source Recall；
- Alignment Precision/Recall/F1；
- Evidence/Path Recall；
- 跨图 Join 正确率；
- 冲突识别率；
- 拒答准确率和校准误差；
- Token、延迟、工具调用次数；
- 不必要图谱访问率。

## 10. 当前方案的验证状态与适用范围

1. DBpedia 多语言图处于待验证阶段；其主实验或辅助实验角色由两轮验证结果确定。
2. 数据适用性依据固定采样口径下的必要率、合格产量、多样性和质量综合判断；实体与事实 Jaccard 作为辅助指标。
3. 跨时间实验围绕经核验的历史状态及事实删除/修改构造问题，并注明各快照的时间语义。
4. 拆分图承担受控实验角色，自然多源互补由保留来源的原始图谱组合验证。
5. mapping-based 多语言实验覆盖实体对齐和连接；复杂 Schema 对齐、来源独立性和冲突处理分别配置对应数据与验证任务。
6. 网络安全作为异源主实验候选，合成患者医学图作为候选第二领域，两者均待验证。

## 11. 下一步

按 [E001 任务书](../tasks/E001.md) 完成数据审计；根据结果决定是否进入第二轮、补充其他语言或转向其他图谱。实现验证代码时遵守根目录 [文件管理约定](../../AGENTS.md)。验证方案与任务书分别维护，完成验证后的分析写入 [验证记录](../experiments/validation-log.md)；有效结论同步更新本文件的当前方案，淘汰原因与替代方案记入第 12 章。

整体方案的可行性经验证后，在本文件中形成完整技术方案，覆盖问题定义、数据与接口、算法流程、关键模块、证据与对齐机制、成本控制、评测结论、适用边界和实施安排。当前各项假设保持待验证标记。


## 12. 思路淘汰与替代记录

本章单独保存淘汰过程，主章节仅呈现当前方案。验证结论引用实验验证记录，逐样本证据与日志引用运行产物，避免重复维护。

### 2026-09-11：验证设计审查

证据性质：方法设计审查，**尚无实验推翻记录**。以下调整依据统计或语义上的问题，不代表已完成数据实验。当前方法见 [E001 任务书](../tasks/E001.md) 与 [共用证据口径](../experiments/validation-plan.md#2-统一证据口径)。

| 已淘汰做法 | 淘汰原因 | 当前采用的思路 |
|---|---|---|
| 截取每实体前 20～50 条出边后判断单图缺失 | 截断可能漏掉单图答案证据，人为制造互补 | 完整读取目标关系邻域，并在声明的评测范围复核 |
| 删除属性语言前缀、无条件去除文本语言标签 | 名称相似不能证明语义等价，翻译差异也可能被误计 | 显式语义映射、保留原文及语言信息，未知项单列 |
| 从共享实体样本估计总体实体重合 | 样本已按重合条件筛选，无法代表总体 | 使用完整实体清单或独立总体样本 |
| 从筛选后的跨图样本估计天然必要率 | 采样目标已限定跨图，会高估发生率 | 固定候选采样与定向挖掘分开统计 |
| 只凭低事实重合率和高必要率决定主实验资格 | 噪声、模板集中和样本偏差也能产生这些指标 | 综合有效产量、多样性、质量及独立评测条件 |
| 要求答案只能为单个实体，或只检查选中的跨图路径 | 排除了合法集合答案，也未排除单图替代证据 | 采用答案集合，分类严格连接与集合补全，复核替代路径 |

后续每条记录包含：日期、旧思路、被推翻的具体假设、实验编号/运行编号或其他依据、失败原因与适用范围、新思路及其验证状态。仅在证据支持时标记“实验推翻”；尚未验证的替代思路标记“待验证”。
