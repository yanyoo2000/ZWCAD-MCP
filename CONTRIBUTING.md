# 贡献指南

感谢你愿意改进这个项目。本文说明如何提交改动、以及合并前必须满足什么。

## 快速上手

```bash
# 1. Fork 本仓库，然后克隆你自己的 fork
git clone https://github.com/<你的用户名>/ZWCAD-MCP.git
cd ZWCAD-MCP

# 2. 安装依赖
npm install                          # JS 侧（实际只有开发期工具）
pip install fastmcp pyzwcad pyzwcadmech comtypes pywin32   # Python 侧

# 3. 准备运行时环境（可选，用于真实调用验证）
node bridge.js --prepare
```

## 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Node.js | >= 18 | 启动器与连接器构建 |
| Python | >= 3.10 | 业务逻辑（COM 调用） |
| 中望CAD | 2024+ | 真实调用验证时需要；纯改代码可不装 |

## 项目结构

```
├── bridge.js              Node 启动器入口
├── lib/
│   ├── proxy.js           代理层：握手、转发、代理工具
│   └── errorlog.js        本地错误日志
├── runtime/zwcad2d/       Python 源码（本仓库为唯一源头）
│   └── server.py          MCP 服务与 40 个工具的实现
├── tools.json             ★ 从 Python 导出的工具清单（见下方「必须重新导出」）
├── connector/             各平台连接器元数据
└── scripts/               构建与测试脚本
```

## 改动后必须做的四件事

### 1. 如果改了 Python 工具，必须重新导出 tools.json

```bash
python scripts/export-tools.py
```

**这一步最容易忘，也最容易让 CI 失败。**

原因：代理层需要在整个 Python 运行时装好之前就应答 `tools/list`（这是首次使用不超时的关键）。所以工具清单在构建期从 Python 导出成静态文件 `tools.json`，随包分发。

改了 `@mcp.tool` 装饰的函数、增删工具、改工具描述或参数，都要重新导出。CI 会比对两者，不一致直接失败。

### 2. 增删工具后，同步文档里的工具数

```bash
npm run check:toolcount    # 校验，不一致列出问题
npm run fix:toolcount      # 自动修正总数
```

**为什么会有两个数字**——这不是笔误，是两个真实口径：

| 口径 | 数值 | 谁在用 |
|---|---|---|
| `python` | `tools.json` 的条目数 | PyPI 版、`python -m zwcad2d`、源码本身 |
| `user` | `python` + 代理层注入数 | npm 版、离线包 |

启动器会额外注入 `zwcad_wait_ready`（用于等 Python 运行时就绪），所以走 bridge 的渠道比直接跑 Python 多一个。

权威值由脚本从 `tools.json` 与 `lib/proxy.js` 的 `PROXY_TOOLS` 算出，**不要在脚本里手写数字**。加新声明位置时，在 `scripts/check-tool-count.js` 的 `CLAIMS` 里补一行并标注口径。

> 明细求和也会校验：README 里写的「平台 27 + 机械 11 + 诊断 2 + 启动器侧 1」加起来必须等于声明的总数。曾出现过写「共计 41 个」但明细只有 40 的自相矛盾。

> **GitHub 仓库描述（About）里不写工具数**。它不在仓库内，本地脚本扫不到，CI 也没有修改仓库设置的权限（`GITHUB_TOKEN` 不含 `administration` scope），一旦写死就只能靠人手动改——实践过一次，它停在 `39` 很久没动。About 只写描述性文字。

### 3. 跑本地检查

```bash
npm run check:connector    # 连接器目录结构
npm run check:toolcount    # 各处声明的工具数
npm test                   # 错误日志与轮转测试
node --check bridge.js     # 语法
```

### 4. 确认发布包干净

```bash
npm pack --dry-run | grep -i pycache
```

出现 `__pycache__` 说明 `.npmignore` 有问题（历史上漏进过一次）。

## 提交 PR

```bash
git checkout -b feat/your-feature   # 分支名用 feat/ 或 fix/ 前缀
git commit -m "feat: 简明描述改了什么"
git push origin feat/your-feature
```

然后在 GitHub 上向 `main` 发起 Pull Request。

### 提交信息约定

用 `<类型>: <说明>` 形式，类型取以下几种：

| 类型 | 用于 |
|---|---|
| `feat` | 新增工具或能力 |
| `fix` | 修 bug |
| `docs` | 只改文档 |
| `refactor` | 重构，行为不变 |
| `chore` | 版本号、依赖、构建配置 |
| `test` | 只改测试 |

说明部分用中文即可，写清「改了什么」和「为什么」。

### 版本号不要自己改

`package.json` 的 `version` 由维护者在发布时统一修改。如果你在 PR 里改了，合并时容易冲突。

## CI 会检查什么

PR 创建后自动运行（见 `.github/workflows/ci.yml`）：

| 检查 | 说明 |
|---|---|
| 连接器结构 | `connector/<平台>/<连接器>/` 下的必需文件齐全 |
| Python 可编译 | 所有 `.py` 能通过 `ast.parse` |
| JS 语法 | `bridge.js`、`lib/*.js`、`scripts/*.js` |
| tools.json 一致 | 重新导出后与仓库中的文件比对 |
| 单元测试 | `npm test` |
| 打包检查 | 产物文件列表、不含 `__pycache__` |
| 安装冒烟 | 打包后能被 npm 正常安装 |
| 版本一致 | 仅打 tag 时检查 tag 与 `package.json` |

**所有检查必须先本地跑通。** CI 失败的分支维护者不会 review。

## 审核标准

维护者 review 时会关注：

- **是否会破坏现有用户**：新增工具安全，改已有工具的参数或不返回结构需要谨慎
- **错误处理**：工具内部异常要用 `_err()` 包装，返回结构化错误而非抛异常
- **文档同步**：改了行为要更新 `README.md`，改了首次使用流程要更新 `connector/workbuddy/zwcad-mcp/skills/SKILL.md`
- **不要引入无法回退的写入**：涉及删实体、覆盖保存、关闭文档的操作，保持与现有工具一致的安全约束
- **不要在 README 写逐版本流水账**：版本历史由 GitHub Releases 自动生成
  （`publish.yml` 里的 `generate_release_notes: true` 会列出每个 PR 并附 diff 链接），
  npm 与 PyPI 包页也各有版本列表。README 里再抄一份就是第四份重复，
  而且是唯一需要手工维护的——实践过一次，16 个版本只覆盖了 11 个，
  两天就漂了 2 个。需要时只保留「怎么查当前版本」和指向 Releases 的链接。

完整的审查依据见 [`docs/review-checklist.md`](docs/review-checklist.md)：五维检查清单、
本项目的 9 类特有陷阱（版本号同步、tools.json 一致性、双渠道发布等）、
以及每条结论该用什么置信度表述。**提 PR 前按它自查一遍，能减少往返。**

## 发布流程（仅维护者）

打 tag 会同时发布到**三个渠道**，版本号由 `scripts/sync-version.py` 保证一致：

| 渠道 | 产物 | 面向 |
|---|---|---|
| **npm** | `zwcad-mcp-<ver>.tgz` | 普通开发机，`npx -y zwcad-mcp` |
| **PyPI** | `zwcad_mcp-<ver>-py3-none-any.whl` | 已有 Python 环境 |
| **GitHub Release** | `zwcad-mcp-<ver>-win-x64-offline.zip` | **内网 / 产线**，自带 Python 与依赖 |

> 离线包由 `scripts/build-offline.js` 在 CI 里构建，约 77 MB。
> 体积主要来自官方 CPython 完整发行版——**刻意不换精简版**，
> 因为 `pywin32` 与 `comtypes` 深度依赖 Windows 组件，
> 精简发行版省 40 MB 却可能引入难排查的兼容问题。
>
> 本地构建（`npm run build:offline`）用的是精简 Python，产物约 37 MB，
> 仅供调试用。**对外发布以 CI 产物为准**。

### 发布前检查清单

这几项是仓库外的前置条件，CI 查不出来，漏了就会发一半：

| 检查项 | 怎么确认 |
|---|---|
| **PyPI Trusted Publisher 已配置** | https://pypi.org/manage/project/zwcad-mcp/settings/publishing/ 里存在：Owner `yanyoo2000`、Repository `ZWCAD-MCP`、Workflow `publish.yml`、Environment `pypi` |
| **GitHub environment `pypi` 已建** | 仓库 Settings → Environments |
| **npm token 有效** | 仓库 Secrets 里的 `NPM_TOKEN` 未过期（granular token 默认 90 天） |
| **版本号已同步** | `python scripts/sync-version.py --check` 通过 |
| **npm 上该版本不存在** | `npm view zwcad-mcp version` 比对；已存在的版本号无法重复发布 |
| **离线包 job 的 `contents: write` 权限** | `publish.yml` 的 `offline` job 需要该权限才能上传 Release 附件；缺了会在「上传到 GitHub Release」步骤失败，而此时 npm/PyPI 已经发成功了 |

### 发布步骤

```bash
# 1. 升版本（改 package.json 后同步其余三处）
#    编辑 package.json 的 version
python scripts/sync-version.py

# 2. 提交并走 PR 合并到 main（分支保护要求）
# 3. 打 tag 并推送，触发发布
git tag v0.3.4 && git push origin v0.3.4
```

打 tag 后会产出四个产物，**版本号全部来自同一个 tag**：

| 产物 | 版本号写在 |
|---|---|
| npm 包 | `package.json` |
| PyPI wheel / sdist | `pyproject.toml` |
| 离线包 | 从 `package.json` 读取，文件名带版本 |
| **WorkBuddy 连接器包** | **`connector-meta.json`** |

CI 里有一道校验会逐一比对，任何一处与 tag 不符就失败。

### 上传 WorkBuddy 连接器时注意版本对齐

平台的审核会比对两件事：

1. `connector-meta.json` 里声明的 `version`
2. `mcp.json` 实际拉取的 npm 包版本

**两者不一致会被判定「声明版本与实际不符」而退回。**

踩过一次：提交表单时填的版本号与 npm 上实际存在的版本对不上
（填了一个从未发布过的号），而 `mcp.json` 里 `npx -y zwcad-mcp` 拉到的是 npm 的
`latest`，两者自然不符。

所以：

- **不要手填版本号**。下载 Release 里的 `zwcad-mcp-connector-<版本>.zip`，
  它的 `connector-meta.json` 已经和该 tag 对齐。
- 平台的表单如果要求填版本，填 tag 去掉 `v` 前缀后的部分（如 `v1.2.0` → `1.2.0`）。
- 每次发版后重新下载最新的连接器包，不要复用旧的。

> `mcp.json` 里写的是 `npx -y zwcad-mcp`（不带版本号），所以它总是拉 npm 上的
> `latest`。这意味着**连接器包必须与 npm 上的 latest 同版本**——发版后要重新
> 提交连接器，否则用户装到的版本会和声明的不一致。

> **版本号规则的变更**：从 `1.2.0` 起，项目版本号改为 `1.x` 线
> （此前是 `0.3.x`）。`1.2.0` 与 `0.3.13` 是**同一天的相邻发布**，
> 前者只是版本号重编号，不含额外功能变更。npm 与 PyPI 上仍保留 `0.1.x ~ 0.3.x`
> 的历史版本，用户固定版本时注意区分。

### 发布失败怎么补救

**关键：不要重打已有 tag。** tag 已存在时重打会冲突，且 npm 会报版本重复。

| 失败情况 | 正确做法 |
|---|---|
| npm 成功、PyPI 失败 | **只重跑那次 run 的 pypi job**（Actions 页面 → 该次运行 → 左侧 pypi job → Re-run jobs）。排查时注意：修复代码不会进入已发布的版本，只影响下一个版本 |
| npm 成功、离线包失败 | 若失败在「构建离线包」步骤（脚本有 bug），**必须发新版本号**——重跑 job 带不上新的构建脚本。若只是「上传到 GitHub Release」失败（权限/网络），重跑即可 |
| npm 失败、PyPI 未跑（因为依赖 npm） | 修复后**打新版本 tag**，不要复用旧 tag |
| 两个都失败 | 修复后打新版本 tag |

**为什么 PyPI 补发不能打新 tag**：如果 npm 已发出 0.3.3、PyPI 补发时打 v0.3.4，那 npm 会跳过「0.3.3 的修复内容」，两个渠道的 0.3.3 内容不一致。宁可重跑 job 让同一版本对齐。

**离线包 job 的特殊性**：它依赖 `scripts/build-offline.js` 本身。脚本有 bug 时，单独重跑那个 job 用的还是旧脚本，所以只能发新版本号。这与 PyPI 补发的处理方式相反，判断依据是「失败原因在脚本还是在环境」。

## 关于机械功能

`runtime/zwcad2d/server.py` 里分两类工具：

| 类型 | 依赖 | 说明 |
|---|---|---|
| 平台工具 | 只要 ZWCAD 运行 | 绘图、标注、查询、文档管理 |
| 机械工具 | 中望机械CAD + ZwmToolKit 类型库 | 图框、标题栏、BOM、球标 |

改机械部分要特别注意类型库加载的 5 级回退策略（见 `README.md`）。没有中望机械环境也能改代码，但**无法真实验证**，请在 PR 里说明这一点。

## 遇到问题

- 功能不符合预期 → 开 Issue，附上 `node bridge.js --doctor` 的输出
- 不确定怎么改 → 先开 Issue 讨论，避免白做
- CI 失败但本地通过 → 检查 Node/Python 版本是否与 CI 一致（Node 20 / Python 3.12）
