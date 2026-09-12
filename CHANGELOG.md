# 更新日志

## Unreleased

- 优化结果增加行为风格（`personality.plan_style`）：提示词、校验、QQ 查看、Markdown 与 `latest.toml` 全链路输出。

## 1.0.0 - 2026-07-10

- 实现每 60 分钟自动读取聊天并优化人设。
- 实现聊天概括、优化方向、最终人设三阶段流程。
- 支持 planner、replyer、任意 MaiBot 模型任务和 OpenAI 兼容模型。
- 实现 QQ 手动触发、状态、要求、模型、历史、查看和开关指令。
- 使用 SDK 标准持久化目录保存 JSON、Markdown 与 TOML 版本。
- 加入管理员鉴权、私聊默认关闭、提示词注入隔离和原子写入。

