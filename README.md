# MaiBot 人设优化器

[![MaiBot](https://img.shields.io/badge/MaiBot-1.0.12%2B-blue)](https://github.com/Mai-with-u/MaiBot)
[![SDK](https://img.shields.io/badge/maibot--plugin--sdk-2.7%2B-green)](https://github.com/Mai-with-u/MaiBot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

管理员设置优化要求后，插件默认每 60 分钟读取最近一小时聊天，依次完成：

1. 分块概括聊天中的实际表现
2. 根据管理员要求生成有证据支撑的优化方向
3. 从 MaiBot 主配置读取当前 `personality.personality`、`personality.reply_style` 与 `personality.plan_style`
4. 生成完整的新人格、表达风格与行为风格
5. 保存 JSON、Markdown 和可复制的 TOML 版本

插件 ID：`github.kumburovicbranko682-boop.persona-optimizer`

仓库：https://github.com/kumburovicbranko682-boop/maibot-persona-optimizer

## 兼容性

| 项目 | 要求 |
|------|------|
| MaiBot | `1.0.12` ~ `1.x` |
| maibot-plugin-sdk | `2.7.0` ~ `2.x` |
| Python | 跟随 MaiBot（3.10+） |
| 第三方依赖 | 无 |

## 安装

将本仓库内容放入 MaiBot 插件目录，例如：

```text
MaiBot/
└─ plugins/
   └─ persona_optimizer/
      ├─ _manifest.json
      ├─ plugin.py
      ├─ config.toml
      └─ ...
```

随后在 MaiBot WebUI 中加载并启用插件。

## 首次配置

至少配置一名管理员。在 WebUI 或 `config.toml` 中填写：

```toml
[security]
administrators = ["qq:123456789"]
```

默认还会继承 MaiBot 主配置 `plugin.permission` 中的管理员。

也可以用 QQ 指令设置优化要求：

```text
/人设优化 要求 保持温柔和耐心，但减少模板化安慰，多结合聊天上下文自然回应
```

设置要求后，首次自动优化会在一个间隔后执行（默认 60 分钟）。需要立即执行时使用 `/人设优化 立即`。

## QQ 指令

| 指令 | 说明 |
|------|------|
| `/人设优化 帮助` | 查看帮助 |
| `/人设优化 状态` | 查看开关、要求、模型、下次执行与最近错误 |
| `/人设优化 要求 <内容>` | 保存优化要求并重新计算下次执行时间 |
| `/人设优化 要求 清除` | 清除要求并暂停自动任务 |
| `/人设优化 立即 [小时]` | 手动分析最近 N 小时，默认 1 小时 |
| `/人设优化 模型 planner` | 使用 MaiBot planner 任务 |
| `/人设优化 模型 replyer` | 使用 MaiBot replyer 任务 |
| `/人设优化 模型 列表` | 查看可用 MaiBot 模型任务 |
| `/人设优化 模型 任务 <名称>` | 使用其他已注册 MaiBot 模型任务 |
| `/人设优化 模型 自定义 <模型标识>` | 使用配置好的 OpenAI 兼容接口 |
| `/人设优化 历史 [页码]` | 查看已保存版本 |
| `/人设优化 查看 <序号\|版本ID\|最新>` | 查看某个优化版本 |
| `/人设优化 开启` / `/人设优化 关闭` | 控制自动任务 |
| `/人设优化 重置` | 清除 QQ 指令覆盖项，恢复配置文件设置 |

QQ 指令写入的是插件专属持久化状态，不会改动插件源码或 MaiBot 主配置。

## 模型选择

### planner / replyer

插件把 `planner` 或 `replyer` 作为 MaiBot 模型任务名传给官方 `ctx.llm.generate()`，实际模型、负载均衡和重试策略由 MaiBot 的模型配置负责。

### 其他 MaiBot 任务

先发送 `/人设优化 模型 列表`，再使用 `/人设优化 模型 任务 utils` 等指令选择现有任务。

### 自定义模型

支持 OpenAI Chat Completions 兼容接口。在 WebUI 或 `config.toml` 中设置：

```toml
[model]
mode = "openai_compatible"
custom_endpoint = "https://api.example.com/v1"
custom_api_key = "YOUR_API_KEY"
custom_model_name = "your-model"
```

`custom_endpoint` 可填写服务根地址、`/v1` 版本地址，或完整的 `/v1/chat/completions` 地址。

出于安全考虑，QQ 指令只能切换自定义模型标识，不能写入接口地址或 API Key。

## 设定目录

输出位于插件持久化路径：

```text
data/plugins/github.kumburovicbranko682-boop.persona-optimizer/settings/
├─ latest.json
├─ latest.md
├─ latest.toml
└─ history/
```

- `latest.toml`：只包含 `[personality]`，便于审阅后复制到 `bot_config.toml`
- `latest.md`：包含聊天概括、优化方向、优化前后设定
- `latest.json`：完整结构化结果
- `history/`：历史版本，数量由 `storage.history_limit` 控制

本插件不会修改 `bot_config.toml`；生成结果须由管理员审阅后手动应用。

## 隐私与安全

- 默认只分析 QQ 群聊，不读取私聊
- 默认排除命令消息，可用 `excluded_stream_ids` 排除指定聊天流
- 原始聊天不会写入优化历史，只保存模型生成的概括与方向
- 提示词明确把聊天记录视为不可信数据，降低提示词注入风险
- 使用自定义接口时，相关数据会发送给该接口，请自行确认其隐私政策

## 开发验证

```powershell
python -m compileall -q .
python -m unittest discover -s tests -v
```

## 许可证

MIT License
