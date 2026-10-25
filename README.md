# Unit Test Repair Agent

一个围绕 **pytest 失败反馈**构建的迷你 Coding Agent：读取代码、定位原因、精确修改、运行测试，并根据新的报错继续迭代。每次运行在独立 Git worktree 中进行；失败会回滚，成功会导出补丁，全过程记录 JSONL trace。

这不是调用一次 LLM 生成答案的示例，也不依赖 Agent 框架。Agent loop、四种工具、路径防护、测试执行、worktree 隔离、回滚、轨迹、基准任务和评测报告都在本仓库中。

## 1. 你会得到什么

- 一个有界的 ReAct 风格循环：模型响应 → 工具调用 → 观察结果 → 再次决策。
- 四种真实工具：`read_file`、`edit_file`、`search_code`、`run_tests`。
- OpenAI 兼容的 Function Calling 接口，默认示例为 DeepSeek，可替换提供商和模型。
- Git worktree 隔离：原仓库不会被 Agent 直接修改。
- 目标测试通过后自动运行完整测试集，检查是否引入回归。
- 失败回滚、worktree 清理、成功/失败补丁分离。
- JSONL 逐步轨迹：模型决策、工具参数、测试观察、token 使用量和最终状态。
- 20 个已验证的代码缺陷任务，以及成功率、平均步数、token 和可选成本统计。
- 不需要 API Key 的离线测试和脚本化假模型测试。

**当前没有填写任何“修复成功率”。** 只有实际执行 `evaluate` 后生成的 `evaluation.md` 才能作为实验结果；不同模型的结果不能混用。

## 2. 项目结构

```text
unit-test-repair-agent/
├── repair_agent/
│   ├── agent.py          # 有界 Agent 主循环、全量回归检查、运行汇总
│   ├── models.py         # 模型协议、OpenAI 兼容工具调用适配器
│   ├── tools.py          # 四种工具、schema、路径防护、pytest 执行
│   ├── workspace.py      # Git worktree、diff、reset/clean 回滚与清理
│   ├── benchmark.py      # 缺陷任务加载、临时仓库生成、基准有效性检查
│   ├── evaluation.py     # 顺序评测、指标聚合、Markdown/JSON 报告
│   ├── prompts.py        # 系统提示词和初始失败反馈
│   ├── config.py         # .env 配置和参数校验
│   ├── trace.py          # JSONL trace 与 JSON 落盘
│   ├── cli.py            # run / evaluate / validate-benchmark 命令
│   └── __main__.py       # python -m repair_agent 入口
├── benchmarks/
│   ├── base_project/     # 原始 numberkit 小包及 17 个 pytest 测试
│   └── mutations.json    # 20 个精确代码变异及对应失败测试
├── tests/
│   ├── test_agent.py     # 脚本化假模型：成功修复、失败回滚，不调 API
│   ├── test_tools.py     # 精确替换、路径防护、搜索和测试工具
│   └── test_benchmark.py # 任务完整性与抽样基准验证
├── .env.example          # 配置模板，不包含真实密钥
├── pyproject.toml        # 包信息、依赖与 pytest 配置
└── runs/                 # 运行后生成；已被 Git 忽略
```

## 3. 环境准备

必需：

- Windows / macOS / Linux；以下命令以 Windows PowerShell 为主。
- Python **3.10 或更高**，推荐 3.13。
- Git，且 `git --version` 可正常执行。
- 推荐安装 [uv](https://docs.astral.sh/uv/)，也可用普通 `pip`。
- 只有真实 Agent 运行才需要 LLM API Key；离线验证不需要。

本项目的 pytest 使用当前虚拟环境的 Python。若要修复其他仓库，目标项目的测试依赖也要安装到同一个环境。

## 4. 安装

### 使用 uv（推荐）

```powershell
cd d:\code\unit-test-repair-agent
uv venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
```

不必激活环境，后续直接调用 `.venv\Scripts\python.exe`，可避免 PowerShell 激活策略问题。

### 使用普通 Python / pip

```powershell
cd d:\code\unit-test-repair-agent
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

macOS / Linux 将 Python 路径替换为 `.venv/bin/python`，安装命令为：

```bash
uv venv --python 3.13
uv pip install --python .venv/bin/python -e ".[dev]"
```

## 5. 第一步：不花 API 费用验证项目

在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m pytest
```

预期：所有测试通过。`test_agent.py` 使用脚本化假模型，完整走一遍“编辑 → 测试 → 全量验证 → 导出补丁”和“编辑 → 失败 → 回滚”，不会请求网络模型。

再执行：

```powershell
.venv\Scripts\python.exe -m repair_agent validate-benchmark
```

预期输出包括：

```text
OK      CLEAN expected=pass
OK      M01   expected=fail
...
OK      M20   expected=fail
Validated 20 mutations plus the clean baseline.
```

这条命令实际做了两件事：

1. 原始 `numberkit` 项目的完整测试集必须通过。
2. 每个变异版本必须至少导致一个测试失败，防止“无效缺陷”污染成功率。

如果这两步不能通过，不要先配置模型；先解决本地 Git、Python 或 pytest 问题。

## 6. 配置真实模型

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://api.deepseek.com
MODEL_ID=deepseek-chat
```

- `MODEL_ID` 以你的提供商账户当前可用模型为准，不要机械照抄示例。
- 也支持已有 `DEEPSEEK_API_KEY` 作为密钥回退，但推荐统一使用 `LLM_API_KEY`。
- 更换 OpenAI 兼容服务时，同时修改 `LLM_BASE_URL`、`MODEL_ID` 和密钥。
- 模型必须支持 Chat Completions 的 Function Calling / tool calls；纯推理但不支持工具调用的接口不能直接运行本项目。
- `.env` 已被 `.gitignore` 忽略，不要把密钥写进源码或提交到 GitHub。

常用运行参数：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `MAX_STEPS` | 12 | 最大模型决策步数，不是工具调用总数 |
| `TEST_TIMEOUT_SECONDS` | 45 | 单次 pytest 超时 |
| `MAX_TOOL_OUTPUT_CHARS` | 6000 | 回喂模型的最大测试/文件输出长度 |
| `VERIFY_ALL_TESTS` | true | 目标通过后是否运行完整测试集 |
| `INPUT_PRICE_PER_MILLION` | 0 | 可选：每百万输入 token 的美元价格 |
| `OUTPUT_PRICE_PER_MILLION` | 0 | 可选：每百万输出 token 的美元价格 |

价格保持 0 时仍统计 token，但不估算费用。价格需要按提供商当期价格自行填写。

## 7. 第二步：只跑一个缺陷任务

先跑 `M01`，不要直接跑 20 题：

```powershell
.venv\Scripts\python.exe -m repair_agent evaluate --task M01 --max-steps 8
```

程序会自动：

1. 把原始 fixture 复制到临时目录。
2. 注入 `M01` 缺陷并提交为基线。
3. 创建隔离 worktree。
4. 运行目标测试，把失败信息交给模型。
5. 让模型读代码、修改并重新测试。
6. 目标通过后运行完整测试集。
7. 保存 trace、补丁和单题结果，清理临时 worktree。

默认报告位于 `runs/evaluation_时间戳/`。重点查看：

```text
runs/evaluation_时间戳/
├── evaluation.md             # 真实指标表
├── evaluation.json           # 完整机器可读结果
└── tasks/M01/
    ├── trace.jsonl           # 每一步决策与工具观察
    ├── baseline.json         # 修复前的失败输出
    ├── attempt.patch         # 最后一次尝试的改动，无论成败
    ├── final.patch           # 仅成功时保留的有效补丁
    └── summary.json          # 单题状态、步数、token、耗时、回滚情况
```

**如何确认它真的工作了**

- `summary.json` 中 `success` 为 `true`，`reason` 为 `all_tests_passed`。
- `trace.jsonl` 中存在 `edit_file`、`run_tests` 和 `full_suite_verification`。
- `final.patch` 非空，并包含针对生产代码的改动。
- 临时 worktree 已删除；原始 fixture 没有被改写。

## 8. 修复自己的 Git 仓库

目标仓库必须：

- 已初始化 Git，并至少有一个 commit。
- 没有已跟踪文件的未提交修改；先 commit 或 stash。
- 当前存在一个可复现的失败 pytest 目标。
- 测试依赖已安装到本项目虚拟环境。

执行：

```powershell
.venv\Scripts\python.exe -m repair_agent run `
  --repo d:\path\to\your-repo `
  --test tests/test_example.py::test_case `
  --max-steps 12
```

PowerShell 的反引号是续行符；也可以把命令写在一行。

成功后不会直接修改原仓库，而是生成 `final.patch`。人工审查后再决定是否应用：

```powershell
cd d:\path\to\your-repo
git apply --check d:\code\unit-test-repair-agent\runs\你的运行目录\final.patch
git apply d:\code\unit-test-repair-agent\runs\你的运行目录\final.patch
```

失败时 `final.patch` 为空，`attempt.patch` 只用于复盘，不建议直接应用。

## 9. 第三步：运行完整 20 题评测

先确认单题已成功，并了解 API 成本，再运行：

```powershell
.venv\Scripts\python.exe -m repair_agent evaluate --max-steps 12
```

调试期可以只跑前 3 题：

```powershell
.venv\Scripts\python.exe -m repair_agent evaluate --limit 3
```

或重复指定任务：

```powershell
.venv\Scripts\python.exe -m repair_agent evaluate --task M03 --task M07 --task M13
```

报告包含：

- 修复成功率：完整测试集通过才计成功。
- 平均模型步数。
- 输入、输出和总 token。
- 可选美元成本估算。
- 每题失败原因，便于做 badcase 归因。

评测是顺序执行的，避免并发请求导致限流，也避免多个任务争用本机资源。内置 fixture 是小型、可控的教学基准，不代表 SWE-bench 或大型仓库上的表现。

## 10. 主链路：先理解这 6 步

```text
失败 pytest
   ↓
Git worktree 隔离 + 基线失败观察
   ↓
LLM 根据 system prompt 和失败信息选择工具
   ↓
read/search → edit → run_tests → 新观察
   ↓                         │
   └──── 未通过且步数未耗尽 ────┘
   ↓
目标通过 → 全量 pytest → 成功导出 patch / 失败回滚
```

## 11. 关键模块与设计取舍

### `agent.py`：为什么是有界循环

开放式 Agent 可能反复修改、持续消耗 token。这里用 `max_steps` 限制模型决策次数，并把“模型主动停止”“步数耗尽”“全量回归失败”区分为不同原因，便于失败分析。

### `tools.py`：为什么使用精确字符串替换

让模型重写整个文件容易引入无关变化，也使 patch 难以审查。精确替换要求 `old_string` 唯一；出现 0 次或多次都会失败并反馈给模型。代价是模型必须先读文件，且大段重构不方便——这是有意选择的可审计性。

`run_tests` 不使用 `shell=True`，不接收任意命令，只接收 pytest 目标；路径解析后必须仍位于 worktree 内，`.git` 禁止访问。`edit_file` 还会在工具层拒绝修改 `tests/`、`test/` 或 `test_*.py`，防止模型通过改测试伪造成功。

### `workspace.py`：为什么原仓库始终不变

所有编辑发生在 detached worktree。失败时执行 `git reset --hard <基线>` 和 `git clean -fd`，作用范围仅是本次临时 worktree；随后导出尝试补丁并清理。成功时先导出最终补丁再清理。

### `agent.py`：为什么目标通过还不够

只修复一个测试可能破坏其他功能。因此目标通过后默认运行 `pytest .`。全量失败会把新观察回喂模型继续修复。`--verify-target-only` 仅用于调试，不建议用于正式指标。

### `models.py`：为什么不用框架

OpenAI 兼容适配器明确展示 `assistant(tool_calls) → tool → assistant` 消息序列，方便理解和替换模型。它不等于支持所有提供商的特殊扩展。

### `benchmark.py`：为什么要先验证缺陷

一个“注入后测试仍通过”的任务没有意义。`validate-benchmark` 将 CLEAN 必须 pass、MUTANT 必须 fail 作为前置条件，避免把无效题计入评测。

## 12. 常见问题

**`Missing LLM_API_KEY`**  
已在项目根目录创建 `.env` 了吗？是否填入了有效密钥？离线测试不需要它，但 `run` 和 `evaluate` 需要。

**模型返回了文本，但没有工具调用**  
检查模型是否支持 Function Calling。`trace.jsonl` 可看到实际响应；必要时换用支持工具调用的模型。

**`ModuleNotFoundError` 或目标仓库测试无法启动**  
本项目会调用当前 `.venv` 中的 Python。把目标仓库测试依赖也安装进该环境，然后先在目标仓库手动运行同一 pytest 命令。

**`Target repository has tracked uncommitted changes`**  
先提交或暂存目标仓库改动。程序故意不在状态不明确的仓库上创建基线。

**pytest 超时**  
增大 `TEST_TIMEOUT_SECONDS`，或先缩小测试目标。大型全量回归检查可能明显慢于单测。

**中文输出乱码**  
测试子进程已设置 `PYTHONUTF8=1`。如果终端本身乱码，可在 PowerShell 执行 `$env:PYTHONUTF8="1"` 后重试。

**成本估算为空**  
这是预期行为。只有配置非零 token 单价后才估算；token 使用量始终来自真实 API 响应。

## 13. 当前边界

- 只支持修改已有文本文件，没有“新建文件”工具。
- 不是通用 shell Agent，也不是安全的多租户沙箱；请在可丢弃的测试仓库中使用。
- 只处理本地 pytest，不包含浏览器、网络检索或跨仓库依赖修复。
- 内置 20 题用于验证闭环和比较模型/策略，不应宣称代表真实大型项目修复能力。
- 尚未接入 LangGraph、MCP、Web UI 或并发评测；这些不是当前跑通所必需。
- 真实模型效果取决于模型能力、提示词、步数预算和任务难度，必须实测。

简历描述应使用实际完成的范围和真实报告数字，不要把计划中的功能写成已实现。
