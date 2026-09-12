# VS Code SFTP 同步

适用于本机已安装的 `Natizyskunk.sftp 1.16.3`。连接配置在 [sftp.json](../.vscode/sftp.json)，共用排除规则在 [sftp.ignore](../.vscode/sftp.ignore)。配置尚未填写服务器信息，也未测试远程连接。

## 使用步骤

1. 在 VS Code 打开整个项目目录。编辑 `.vscode/sftp.json`，填写 `host`、`username`、`remotePath`，按实际端口修改 `port`。`remotePath` 必须是远程项目目录的绝对路径。
2. 若使用私钥认证，增加 `privateKeyPath`，值为本机私钥的绝对路径；加密私钥可设置 `passphrase: true` 使用提示框。不在项目里复制私钥。
3. 仅首次准备远程项目且尚未建立 Git 工作流时，执行 `SFTP: Set Profile`，选择 `bootstrap-upload`，再执行 `SFTP: Sync Local -> Remote`。不上传实验输出；Git 工作流建立后代码和文档统一通过 Git 同步。
4. 远程任务结束后，选择 `download-review`，执行 `SFTP: Sync Remote -> Local`。只下载 `outputs/runs/` 中未被排除的审核产物，不覆盖代码、文档和工程配置。该 profile 为默认模式。
5. 后续代码与文档使用 [Git 分支工作流](git-workflow.md) 同步，不用初始化上传覆盖远程开发。下载证据后核对对应运行的 code_commit。

本配置按手动选择的同步方向覆盖目标文件，不依赖两台机器的修改时间。关闭了自动上传、打开时下载、监视器自动传输及同步删除；本地没有数据目录不会触发远程数据删除。

## 同步范围

| 内容 | 初始化上传 | 日常下载 |
|---|---|---|
| `src/`、`scripts/`、`configs/`、`tests/`、依赖声明 | 仅首次准备项目 | 否，使用 Git |
| `AGENTS.md`、`README.md`、`docs/` | 仅首次准备项目 | 否，使用 Git |
| 运行指标、manifest、配置快照、候选/查询/证据 JSONL、审核 HTML、常规日志 | 否 | 是 |
| `data/` 全部、数据集、模型、索引、虚拟环境、缓存 | 否 | 否 |
| `.vscode/`、版本库、agent 本地状态、凭据 | 否 | 否 |
| 压缩包、数据库、模型权重及数组等大型格式 | 否 | 否 |
| `outputs/incoming/`、归档、运行目录中的 `server-only/`、全量 `normalization.jsonl` | 否 | 否 |

保留 `.gitignore` 文件本身用于远程项目管理，但 SFTP 不使用其规则：Git 忽略的运行结果仍可下载。审核证据使用普通 JSON/JSONL/HTML/TXT，压缩格式会被排除；小型 RDF 证据切片可以放在运行目录的 `evidence/`，原始大 RDF 文件必须放 `data/`。

## 远程 agent 的交付约定

- 大数据只放 `data/raw/`、`data/processed/` 或运行目录的 `server-only/`，不复制到可下载的审核目录，不通过符号链接绕过排除规则。
- 全量标准化映射、图数据库、嵌入、缓存及超大调试日志保留在服务器。提供小型标准化样本及源文件定位，审核证书指向完整原始数据的位置和 SHA-256。
- 完整候选账本、查询答案、证据证书及审计记录保留供审核，不能为压缩体积静默截断或只交付成功案例。
- 交付前列出可下载文件的大小与 SHA-256，保存为运行目录中的 `download-manifest.json`。清单区分可下载文件和仅服务器保留的文件，给出可下载总大小；不把清单自身纳入哈希列表。
- 建议每次运行的回传内容控制在 200 MB 内、单文件 50 MB 内。这是交付检查目标，**SFTP 配置本身不按文件大小限流或过滤**。如果完整必需证据超出目标，先报告具体文件和体积，不自动省略证据。
- 文件传回本地后，可直接让审核 agent 检查 `outputs/runs/<运行编号>/`。需要完整图才能独立重放的检查仍在服务器进行。

两个 profile 的名字不会限制操作方向，须搭配上述对应命令。目录/后缀规则用于正常项目同步，不应把远程文件浏览器中手动强制下载被排除文件当作同等保护。具体字段见 [插件配置文档](https://github.com/Natizyskunk/vscode-sftp/wiki/Configuration)。
