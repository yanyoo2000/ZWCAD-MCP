# PR 审查流程与清单

本文件是 ZWCAD-MCP 仓库的审查工作依据。每次收到 PR 审查任务，按本文件的顺序和清单执行，
不凭印象、不跳步。

> **审查者的定位**：找出真实问题，不是走形式盖章。误报会消耗信任，漏报会造成事故。
> **能力边界**：审查者不是 GitHub 账号，无法点击 Approve。审查意见写入 PR 评论，
> 合并由仓库所有者执行。

---

## 一、审查顺序

顺序是「先看事实，再看改动，最后看上下文」。倒过来容易带着先入判断读 diff。

### 第 1 步：查 CI 结果（事实层）

拿到 PR 号后，先看这条分支的 CI 到底跑成什么样。**这是唯一能直接观测运行时行为的信息源**，
优先级高于任何静态推断。

```bash
# PR 元信息（状态、是否已合并、改动规模）
curl -sS --noproxy '*' \
  -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/pulls/<N>

# 该分支 HEAD 的检查结论
curl -sS --noproxy '*' \
  -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/commits/<head-sha>/check-runs
```

**看三件事**：

1. **PR 是否已经合并**（`state` / `merged`）。已合并的 PR 仍可审查，但要说明「已合并，
   以下为事后审查」，措辞和给 open PR 的建议不同。
2. **每个 check 的 conclusion**。绿了不代表没问题（CI 覆盖不到的维度见第三节），
   红了要读失败注解定位真实原因。
3. **失败的具体步骤**。不要从配置推断哪一步会挂——拉 `jobs` 看每一步的 conclusion。

某步骤失败时，拉注解看原始报错，而不是猜：

```bash
curl -sS --noproxy '*' \
  -H "Authorization: Bearer $(gh auth token)" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/check-runs/<job-id>/annotations
```

### 第 2 步：读 diff（改动层）

```bash
# 完整 diff（比逐文件 API 快，也不漏跨文件关联）
curl -sS --noproxy '*' \
  -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github.v3.diff" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/pulls/<N>
```

本地已 fetch 的话 `git show <sha>` 更顺手。读 diff 时先看 `--stat` 的改动面：
**改了几个文件、有没有跨层**（JS 层 / Python 层 / 构建脚本 / 文档）。跨层改动要格外注意一致性。

### 第 3 步：读关键文件上下文（理解层）

diff 只显示「变了什么」，不显示「为什么这么写」。以下情况必须读完整文件：

- 改动涉及正则、路径拼接、编码、权限、超时等易错点
- 改动了 `@mcp.tool` 装饰的函数（要确认 tools.json 同步）
- 改动了版本号（要确认四处同步）
- 改动了 `.github/workflows/`（要确认 permissions、needs、tag 逻辑）
- 看似无害但「删掉的东西在别处有没有被引用」——grep 一遍再下结论

---

## 二、五维审查清单

按优先级从高到低。**高维度的真实问题比低维度的一堆 nitpick 更有价值。**

### 维度 1：正确性

逻辑有没有 bug、边界条件处不处理、会不会在某些输入下崩溃。

- [ ] 新增/修改的函数，对空值、空数组、单元素、超长输入的行为是什么
- [ ] 正则替换：`[\s\S]*?` 这类写法会不会跨过预想的分段边界（参见 `sync-version.py` 的 `[project]` 段案例）
- [ ] 路径拼接：Windows 反斜杠、POSIX 斜杠、`/tmp` 在 Git Bash 下传给 Python 的实际形态
- [ ] 异常处理：抛异常还是返回结构化错误？Python 工具必须用 `_err()` 包装，不能直接抛
- [ ] 文件 IO：`open()` 后有没有关闭、`newline=` 是否指定、编码是否显式
- [ ] 子进程：超时、退出码、stdout/stderr 是否会死锁
- [ ] 循环/递归：有无提前 `break` 导致漏判，有无无限循环风险

### 维度 2：破坏性

会不会伤害现有用户。**这是最容易被忽略、后果最严重的一维。**

- [ ] 已有工具的参数增删改？改名/改语义 = 破坏性，必须评估
- [ ] 返回结构变了？（字段改名、层级变化、类型从 str 变成 dict）
- [ ] npm 包内文件清单变了？用户可能依赖某些路径（`bridge.js`、`tools.json`）
- [ ] PyPI 包内结构变了？`runtime/` 与 `zwcad2d/` 的映射不能错
- [ ] 版本号是否被「降级」或跳过（npm 不允许重复版本号，PyPI 同理）
- [ ] 首次使用流程变了？握手时机、安装耗时、超时值
- [ ] 有没有「无法回退的写入」（删实体、覆盖保存、关闭文档）——这类操作的安全约束必须与现有工具一致

### 维度 3：一致性

改了 A 有没有同步 B。**本项目有一半的坑在这一维。**

- [ ] 改了 `@mcp.tool` → `tools.json` 重新导出了吗（`python scripts/export-tools.py`）
- [ ] 改了版本号 → 四处都同步了吗（见第三节陷阱 2）
- [ ] 改了行为 → README / SKILL.md 更新了吗
- [ ] 改了依赖版本 → `pyproject.toml` 与 `bridge.js` 的 `BASE_PACKAGES` 是否一致
  （两处是独立维护的副本，没有自动校验）
- [ ] 改了 CI 检查 → CONTRIBUTING 的「CI 会检查什么」表格更新了吗
- [ ] 改了发布流程 → CONTRIBUTING 的「发布流程」章节更新了吗
- [ ] 新增文件 → 是否需要进 `.npmignore` / `pyproject.toml` 的 include/exclude
- [ ] 注释说的和代码做的一致吗（t1 就抓到过 `needs: [ci, npm]` 的注释与串行实际效果不符）

### 维度 4：可维护性

- [ ] 注释解释的是「为什么」还是「是什么」？`# 递增 i` 这种是噪音
- [ ] 有没有重复逻辑？两处副本（如 `BASE_PACKAGES`）有没有注明「需同步」
- [ ] 魔法数字/字符串有没有提取成常量
- [ ] 命名是否表达意图（`check_only` 好过 `flag`）
- [ ] 新增脚本有没有 `--check` 这类无副作用模式（CI 与本地都依赖它）
- [ ] 错误信息是否可定位（给出文件、字段、期望值，而不只是「失败」）

### 维度 5：安全隐患

- [ ] 路径穿越：用户输入拼进路径前有没有过滤（参考 `hatch_info.py` 的 `re.sub(r"[^0-9A-Za-z]", ...)`）
- [ ] 命令注入：拼接 shell 命令的地方，输入是否可控
- [ ] LISP / SendCommand 注入：写入 CAD 的命令串里是否夹带用户输入
- [ ] 临时文件：路径可预测吗？权限？用完是否删除
- [ ] 凭据：workflow 里有没有硬编码 token；日志有没有打印敏感值
- [ ] GitHub Actions 权限：`permissions:` 显式声明后，未列出的 scope 降为 none（见第三节陷阱 6）
- [ ] 第三方 action 是否固定到 major tag 或 SHA

---

## 三、本项目的特有陷阱

以下每一条都来自真实踩坑。审查时逐条过一遍。

### 陷阱 1：tools.json 与 Python 工具不一致

`tools.json` 是构建期从 `runtime/zwcad2d/server.py` 导出的**静态清单**，代理层靠它在
Python 运行时尚未就绪时就应答 `tools/list`——这是「握手不等安装」的关键设计。

改了任何 `@mcp.tool` 装饰的函数（增删工具、改描述、改参数 schema）都必须重新导出：

```bash
python scripts/export-tools.py
```

CI 会重新导出并 `git diff`，不一致直接失败。**审查时**：只要 diff 里动了
`server.py` 中带 `@mcp.tool` 的部分，就去确认 `tools.json` 有没有一起变。

### 陷阱 2：版本号四处同步

以 `package.json` 为唯一源头，同步到：

| 位置 | 字段 | 消费者 |
|---|---|---|
| `package.json` | `version` | npm 发布、`bridge.js` 的 `pkgVersion()` |
| `pyproject.toml` | `[project] version` | PyPI 发布、wheel 文件名 |
| `runtime/zwcad2d/__init__.py` | `__version__` | `server.py` 传给 `FastMCP(version=...)` |
| `connector/workbuddy/zwcad-mcp/connector-meta.json` | `version` | 连接器平台元数据 |

```bash
python scripts/sync-version.py --check   # CI 也跑这个
```

**注意**：`sync-version.py` 只覆盖这四处。README 不在其中（见陷阱 3）。

### 陷阱 3：README 里的「当前版本号」会漂移

README 历史上写过「当前最新版 `0.3.x`」。这类写死的当前版本号，只要发布一个新版本就会
过期，而 `sync-version.py` 管不到 README，CI 也不会拦。

现在的做法是不写死，指向 npm 包页或让用户自己跑 `npm view zwcad-mcp version`。

**审查时**：diff 里出现 `README.md`，就 grep 一遍 `0\.\d+\.\d+`，确认残留的都是
「历史版本表格」里的记录（可以保留），而不是「当前版本」（会漂移）。

### 陷阱 4：双渠道发布，tag 已存在时不能重打

本仓库同时发 **npm**（Node 启动器 + Python 源码）和 **PyPI**（仅 Python 源码），包名相同。

- 发布由推 tag 触发（`v0.3.3` 形式），`publish.yml` 里 `ci` → `npm` → `pypi` 串行
- **npm 与 PyPI 都不允许重复发布同一版本号**
- npm 成功、PyPI 失败时：**只重跑那次 run 的 pypi job**（Actions → 该次运行 → pypi job → Re-run）

**为什么不能打新 tag 补发**：假如 npm 已发出 `0.3.3`，PyPI 补发时打 `v0.3.4`，
那 npm 的 `0.3.4` 会跳过「0.3.3 的修复内容」，两个渠道的 `0.3.3` 内容就不一致了。
宁可重跑 job 让同一版本对齐。

**审查时**：改动涉及发布流程，确认这几种失败路径的处理方式仍写在
`CONTRIBUTING.md` 的「发布失败怎么补救」里。

### 陷阱 5：Windows CI 上 Python 脚本的中文输出编码

CI 跑在 `windows-latest`。Python 脚本往 stdout 打中文，可能因默认编码（GBK / cp1252）
抛 `UnicodeEncodeError`，**脚本以非零码退出，表现成「检查失败」，实际检查是通过的**。

凡是会被 CI 直接调用的 Python 脚本，开头必须有：

```python
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
```

`sync-version.py`、`check-wheel.py`、`export-tools.py` 都已加。**新增脚本要检查这一点。**

另一个相关的坑：曾用 `python -c "..."` 内联多行脚本 + 中文输出，同样会炸。
复杂检查一律独立成 `.py` 文件。

### 陷阱 6：GitHub Actions 的 permissions 语义

job 级 `permissions:` 一旦显式声明，**未列出的 scope 全部降为 `none`**，不继承仓库默认值。

```yaml
permissions:
  contents: read   # 必须显式写
  id-token: write  # 可信发布（OIDC）需要
```

**但注意**：`actions/checkout` 在**公开仓库**上走匿名 clone，`contents: none` 时
依然能成功。这不是推测——run 41956985904 的 pypi job 就只声明了 `id-token: write`，
其日志里「Run actions/checkout@v4」的 conclusion 是 success。
所以「缺 `contents: read`」**不能**推断为「checkout 会失败」。
它的实际影响是语义不清晰、与同类 job 风格不一致。

**推断（未验证）**：需要 `contents: read` 的应是私有仓库 checkout、以及需要操作
release / issue / 写入仓库内容的步骤。公开仓库的只读 checkout 不需要。

### 陷阱 7：字段有同步 ≠ 字段有消费方

`__version__` 这类字段，同步了值不等于对外生效。项目里曾经 `__version__` 是 `0.3.2`，
但 `FastMCP(name=...)` 根本没传 `version`，导致客户端 `initialize` 响应里的
`serverInfo.version` 显示的是 **fastmcp 自己的版本**（2.14.7），排查时极易误判。

**审查时**：看到「修了硬编码/同步了某字段」，先 grep 一遍这个字段**谁在读**。
没有消费方的同步是自我安慰。

```bash
grep -rn "__version__" --include=*.py --include=*.js . | grep -v node_modules
```

### 陷阱 8：`/tmp` 在 Windows 上的路径转换

CI 的 `shell: bash` 步骤里写 `--outdir /tmp/pybuild`，Git Bash 会把它转成
`C:/Users/<user>/AppData/Local/Temp/pybuild` 再传给原生的 Python/Node。

这个转换是**正常且必要**的，不是 bug。实测确认过：在 Git Bash 下把 `/tmp/x` 传给
`python -c "import sys; print(sys.argv[1])"`，打印出的是 `C:/Users/<user>/AppData/Local/Temp/x`。
审查时不要把它误判成路径错误。

### 陷阱 9：两处维护的依赖清单

`pyproject.toml` 的 `dependencies` 和 `bridge.js` 的 `BASE_PACKAGES` 是**各自维护的副本**，
没有自动校验。改了依赖版本，两处都要改，否则「npm 启动器装的依赖」和
「pip install 装的依赖」会不一致。

---

## 四、置信度标注规则

这一节来自一次真实的误判：把「从配置静态推断出的运行时行为」写成了确定的 blocker 结论，
而当时并没有拿到运行日志。规则的目的就是防止同类问题。

### 规则 1：拿到运行日志才能说「会失败」

| 证据等级 | 措辞 | 举例 |
|---|---|---|
| 实际跑过 / 拿到日志 | 「会失败」「已失败」 | 拉到 run 的 steps，某步 conclusion=failure |
| 只看了配置 | 「**推断**可能失败」 | 看到 `permissions` 缺某项，推测 checkout 挂 |
| 完全没验证 | 「**未验证**」 | 没有环境跑 COM，无法确认工具真实行为 |

**禁止**：把配置推断写成确定结论（「因此 checkout 会以 X 错误失败」）。
真发生过的错误就是把「`contents` 降为 none」直接推成「checkout 必然失败」，
实际公开仓库走匿名 clone，checkout 是成功的，真实失败原因在别处。

### 规则 2：旁证要能区分竞争假设

「PyPI 上停在 0.1.4」这个观察，只能证明 **pypi job 没有成功**，
无法区分「checkout 挂了」还是「publish 挂了」。用它去反推具体失败点是不成立的推理。

**要求**：用一个证据支撑某个具体结论前，问一句「这个观察能否区分我的假设和另一种解释」。

### 规则 3：无法验证时明确写「未验证」

以下场景常常无法验证，必须如实标注，不要写成结论：

- 需要真实 ZWCAD / 中望机械环境的 COM 调用
- 需要 Windows runner 才能复现的编码问题（可本地用 `PYTHONIOENCODING` 模拟）
- 需要已发布状态才能验证的 registry 行为
- 需要仓库 Settings 层面的配置（Trusted Publisher、environment、secrets）

写法：「**未验证**：本机无中望机械环境，无法确认 `zwcad_xxx` 在真实类型库下的行为。」

### 规则 4：区分「事实」与「建议」

- **事实**：代码现在这样做 / 日志显示这样 / 实测结果是这样
- **建议**：我认为应该这样改

两者不要混在一句话里。评审意见的说服力来自事实部分准确。

### 规则 5：声明自己的验证动作

每条结论尽量说明是怎么验证的：跑过什么命令、看过哪个 run、读了哪个文件。
这让对方能复核，也让「未验证」的部分一目了然。

---

## 五、输出格式

### 每条问题的结构

```
【严重程度】文件:行号
问题：<客观描述现在是什么样>
为什么是问题：<会造成什么后果>
建议：<具体怎么改>
```

严重程度三级：

| 级别 | 判定标准 | 例子 |
|---|---|---|
| **blocker** | 会导致功能不可用、数据损坏、发布失败、安全问题，**且有证据支撑** | 日志显示发布步骤 `conclusion=failure` 且注解为 `invalid-publisher`（PyPI 未配 Trusted Publisher） |
| **suggestion** | 不阻塞合并，但会造成隐患/维护负担/可预见的错误 | 正则可能跨段误改、README 版本漂移 |
| **nitpick** | 风格、措辞、可选优化 | 注释措辞、变量命名 |

**准则**：

- blocker 必须有证据支撑（日志、实测、明确的代码路径），且要说明「什么条件下必然发生」
- 不确定严重程度时往下降一级，并在正文说明不确定性
- **不要为了显得认真而编造问题**。没有实质问题时明确说「可以合并」

### 评论正文模板

```markdown
## 独立审查：<PR 标题>

> 审查基于 <sha 前 7 位>，CI 状态：<通过/失败>。<若已合并，注明「已于 X 合并，以下为事后审查」>

### blocker

**1. `文件:行号` — 一句话结论**
<问题描述 + 证据 + 建议>

### suggestion

...

### nitpick

...

### 已验证无问题的部分

<列出实际验证过的关键点，避免对方重复排查；也体现审查覆盖度>

### 结论

**可以合并** / **建议修改后再合并** / **必须修改**
<一句话说明理由>
```

**「已验证无问题的部分」很重要**：它告诉对方哪些地方不用再查，也证明审查不是只看 diff 想象的。

---

## 六、结论三选一

| 结论 | 适用条件 |
|---|---|
| **可以合并** | 无 blocker、无实质 suggestion；nitpick 可以保留意见 |
| **建议修改后再合并** | 有 suggestion 级别的真实问题，但不阻塞（如文档漂移、可维护性） |
| **必须修改** | 存在 blocker：功能不可用、破坏现有用户、发布失败、安全问题 |

**边界处理**：

- 只有 nitpick → 「可以合并」，把 nitpick 作为可选建议列出
- 全部 suggestion 都是「未来可能出问题」→ 「建议修改后再合并」，说明不阻塞的原因
- 拿不准某条是不是 blocker → 降为 suggestion 并在正文写「不确定是否必然触发」

---

## 七、发布评论的操作

`gh` 的 TLS 实现会超时，用 `curl` 走 REST API。

### 生成 payload（必须用文件，不能内联）

内联 `--data '{"body":"中文"}'` 会因转义失败返回 400。

```bash
python -c "
import json
body = open('/tmp/review-body.md', encoding='utf-8').read()
json.dump({'body': body}, open('/tmp/comment.json','w',encoding='utf-8'), ensure_ascii=False)
"
```

### 发布

```bash
curl -sS --noproxy '*' -X POST \
  -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @/tmp/comment.json \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/issues/<PR号>/comments
```

`/issues/<N>/comments` 对 PR 同样有效。返回 201 表示成功，响应里的 `html_url`
就是评论地址。

### 发布后确认

```bash
curl -sS --noproxy '*' \
  -H "Authorization: Bearer $(gh auth token)" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/issues/<PR号>/comments
```

确认评论内容完整（特别是中文没乱码，表格没断行）。

---

## 附：常用命令速查

```bash
# 认证
gh auth token
gh auth status

# PR 元信息 / diff / 评论
curl -sS --noproxy '*' -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/pulls/<N>

curl -sS --noproxy '*' -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github.v3.diff" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/pulls/<N>

curl -sS --noproxy '*' -H "Authorization: Bearer $(gh auth token)" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/yanyoo2000/ZWCAD-MCP/commits/<sha>/check-runs

# 本地验证
python scripts/sync-version.py --check
python scripts/export-tools.py && git diff --stat tools.json
python -m build --wheel --outdir /tmp/pybuild
python scripts/check-wheel.py "$(ls /tmp/pybuild/*.whl | head -1)"
npm test
node scripts/build-connector.js --check

# 中文输出乱码时
PYTHONUTF8=1 python -c "..."
```
