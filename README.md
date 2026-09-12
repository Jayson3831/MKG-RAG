# MKG RAG

当前阶段：E001 任务书已就绪，等待远程 coding agent 开发与验证；尚无实测结果。

- [会话交接与启动提示词](docs/handoff.md)：当前状态、远程开发提示词及新会话审核入口。
- [研究思路](docs/research/research-ideas.md)：持续迭代的当前方案，验证后形成完整技术方案；淘汰过程单列记录。
- [E001 任务书](docs/tasks/E001.md)：执行方案、coding agent 交付要求及用户审核指南。
- [实验验证计划](docs/experiments/validation-plan.md)：验证路线、候选实验及共用规范。
- [实验验证记录](docs/experiments/validation-log.md)：仅保存验证完成后的可行性分析结果。
- [文件管理约定](AGENTS.md)：代码、数据与运行产物的位置。
- [Git 开发与推送规则](docs/git-workflow.md)：实验分支开发与每日备份，确认版本后经 PR 合并主线。
- [SFTP 同步说明](docs/sftp-sync.md)：日常只下载审核结果，大数据保留在服务器，代码通过 Git 同步。

```text
docs/research/       研究思路与当前技术方案
docs/tasks/          各实验独立任务书与审核指南
docs/experiments/    验证计划与验证后分析结果（分文件）
src/                可复用验证代码
scripts/            数据准备与实验入口
configs/            版本、抽样、查询及运行配置
tests/              必要的正确性测试
data/raw/           原始数据，下载后保持不变
data/processed/     标准化数据、索引与候选样本
outputs/runs/       按运行编号保存日志、指标、证据和图表
```

代码与数据目录已预留，目前为空。根目录不存放临时报告、下载文件或实验输出。
