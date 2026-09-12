# 行为风格（plan_style）优化设计

日期：2026-09-12  
状态：已批准（待实现）

## 问题

MaiBot `[personality]` 含三个核心文本字段：

| 字段 | 含义 |
|------|------|
| `personality` | 人格设定 |
| `reply_style` | 表达风格 |
| `plan_style` | 说话规则 / 行为风格 |

本插件优化管线只处理前两者。处理结果（QQ「查看」、Markdown、`latest.toml`）因此缺少行为风格建议。根因是端到端未接入 `personality.plan_style`，不是展示遗漏。

不在范围内：`experimental.private_plan_style`。

## 目标

与人格、表达风格完全对齐：读取当前 `plan_style` → 与 persona 同轮 LLM 优化 → 校验 → 写入结果 payload → QQ / Markdown / TOML 输出「优化后行为风格」。

成功标准：

1. 新跑一轮优化后，查看结果可见 `【优化后行为风格】`。
2. 导出的 `latest.toml` 含可拷贝的 `plan_style`。
3. 源配置 `plan_style` 为空时，仍生成完整可用行为风格。
4. 旧结果记录缺少该字段时，查看/导出不崩溃（显示为空）。

## 方案

采用**同管线三字段扩展**：在现有 `build_persona_prompt` / JSON 解析一步中增加 `plan_style`，不新增 LLM 轮次。

未选方案：单独再跑一轮行为风格生成（多成本）；事后从人格文本拆分（质量差、易重复）。

## 数据流

```
collect chats
  → summary LLM
  → direction LLM
  → 读取 personality.personality / reply_style / plan_style
  → persona LLM（JSON: personality, reply_style, plan_style, change_summary）
  → 校验
  → 保存 JSON / MD / TOML + QQ 查看
```

Payload 字段（`schema_version` 保持 `1`）：

- `source_plan_style`
- `optimized_plan_style`

旧记录无上述键：展示与导出按空字符串处理。

## 提示词

修改 `build_persona_prompt`：

1. 增加参数 `current_plan_style` 与输入块 `<current_plan_style>`。
2. 编辑要求明确分工：
   - 人格：她是谁、如何看待世界、如何与人相处
   - 表达风格：如何说话
   - 行为风格：说话/行动规则（何时回复、动作选择、避免重复、勿误回他人对话等），写成可执行条目
3. 源为空也必须生成完整可用 `plan_style`；无需修改时原样返回。
4. JSON schema 增加 `"plan_style"`；`change_summary` 在有改动时覆盖行为风格。

概括 / 方向提示词可轻量补充「关注互动规则与回复时机」，不另开轮次。

## 校验与配置

- 新增 `optimization.max_plan_style_chars`，默认 `8000`，写入 `config.py` 与 `config.toml`。
- `_validate_generated_persona` 增加 `plan_style`：非空；长度不超过上限。不做最短 10 字硬门槛（条目列表可能较短）。
- 读取：`plan_style` 缺失或空 → 当作 `""` 继续；仅 `personality` 缺失仍报错。
- 解析：缺字段 / 空 / 仅空白 → 失败，不回退为当前值糊弄通过。

## 展示与导出

| 位置 | 内容 |
|------|------|
| QQ `_build_record_text` | `【优化后行为风格】`，位于表达风格与优化方向之间 |
| Markdown | `## 优化后行为风格`、`## 优化前行为风格` |
| `latest.toml` | `[personality]` 下增加 `plan_style = ...`（`json.dumps` 与现字段一致） |

## 错误处理

- 模型生成行为风格为空 / 超长：抛出明确 `ValueError`，沿用现有失败路径。
- 不改任务状态机、权限、触发命令。

## 测试

- 更新 `tests/test_storage.py` fixture，断言 Markdown 与 TOML 含 `plan_style` / 行为风格标题。
- 为 `_validate_generated_persona` 增加空 `plan_style` 与超长 `plan_style` 的单测（可新建 `tests/test_plugin_validation.py`，或在现有可导入方式下就近放置）。
- 不强制端到端 LLM 测试。

## 改动文件

- `prompts.py`
- `plugin.py`
- `storage.py`
- `config.py`
- `config.toml`
- `tests/test_storage.py`
- README（若已列出优化输出字段则同步）

## 非目标

- 不优化 `multiple_reply_style` / `private_plan_style`
- 不升 `schema_version`
- 不自动写回 MaiBot 主配置（仍由管理员审阅后拷贝）
