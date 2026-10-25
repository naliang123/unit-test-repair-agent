# 评测结果：步数预算消融实验

本项目一次真实评测记录，用于说明“步数预算（`max_steps`）”如何影响修复成功率，并给出一例可归因的失败分析。所有数字均来自 `deepseek-flash` 的真实运行，未预填、未混用模型。

## 1. 实验设置

| 项 | 值 |
|---|---|
| 评测对象 | 内置 20 个合成单测缺陷（`benchmarks/mutations.json`，基于 `numberkit`） |
| 模型 | `deepseek-flash` |
| 人工确认 | 关闭（`approve_edits=false`，评测模式自动关闭以保证可复现） |
| 控制变量 | 仅改变 `--max-steps`：2 / 4 / 12 |
| 判定标准 | 目标测试通过后，完整测试集也通过，才算成功 |

## 2. 汇总对照

| `max_steps` | 成功数 | 成功率 | 平均步数 | 总 token |
|---|---|---|---|---|
| 2 | 0 / 20 | 0% | 2.0 | 68,199 |
| 4 | 19 / 20 | 95% | 3.85 | 192,812 |
| 12 | 20 / 20 | 100% | 3.95 | 196,864 |

三次运行目录：

- `runs/evaluation_20261025_192646`（`max_steps=2`）
- `runs/evaluation_20261025_193020`（`max_steps=4`）
- `runs/evaluation_20261025_192117`（`max_steps=12`）

## 3. 结论

1. **步数预算是控制成功率的直接变量。** 预算从 2 提到 4，成功率从 0% 跃升到 95%；再提到 12 才达到 100%。
2. **这些任务通常需要 3–4 步完成。** 单次修复大致经历“读测试 → 读源码 → 改代码 → 跑测试”四个环节，2 步只够定位、来不及动手。
3. **失败模式干净、可解释。** 所有失败统一为 `step_budget_exhausted`，并触发回滚，不会留下半成品。

## 4. 失败案例：M17（空白符归一化只去首尾）

`max_steps=4` 时唯一失败的题。看它的 4 步轨迹：

| 步 | 动作 | 评价 |
|---|---|---|
| 1 | 搜索 `normalize_whitespace` + 读测试 | 正常 |
| 2 | 读源码 45–75 行，看到 bug：`return text.strip()` | 已定位病灶 |
| 3 | 又读源码 1–15 行 | 冗余一步，浪费预算 |
| 4 | `edit_file`，改为 `return " ".join(text.split())` | 改对了，但已无预算验证 |
| — | 本应 `run_tests` 验证 | 预算耗尽 → `step_budget_exhausted` |

要点：

- 模型的修复本身是**正确**的（`" ".join(text.split())` 即标准答案）；
- 但因第 3 步多读了一次文件，挤掉了最后验证的机会，被判失败并回滚；
- 回滚丢弃了这条“内容正确但未验证”的修改——**这正是设计意图：未验证的修改不得当作成功保留**；
- 在 `max_steps=12` 的同一任务中，模型走了更紧凑的“读→改→测”路径，4 步内成功。

结论：在预算边界附近，同一任务多一步少一步就可能决定成败，这直接体现了“有界循环 + 失败回滚”的工程价值。

## 5. 复现命令

```powershell
cd d:\code\unit-test-repair-agent

.venv\Scripts\python.exe -m repair_agent evaluate --max-steps 2
.venv\Scripts\python.exe -m repair_agent evaluate --max-steps 4
.venv\Scripts\python.exe -m repair_agent evaluate --max-steps 12
```

## 6. 边界说明

- 本基准为**合成单行缺陷**，用于验证 Agent 闭环、比较模型与策略，**不代表真实大型项目修复能力**。
- 结果与模型强相关：换模型后数字会变，不可与本文混用对比。
- 成本估算为 `null` 是因为未配置 `INPUT_PRICE_PER_MILLION` / `OUTPUT_PRICE_PER_MILLION`。

## 7. 简历可引用结论

> 对步数预算做单变量消融：`max_steps=2/4/12` 下成功率分别为 `0% / 95% / 100%`，平均步数 3.85–3.95。失败统一以 `step_budget_exhausted` 结束并触发回滚；典型失败案例 M17 显示模型在预算边界因多读一次文件而耗尽步数、未能验证修改，验证了有界循环与失败回滚的有效性。
