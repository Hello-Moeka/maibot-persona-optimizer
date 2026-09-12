"""MaiBot 人设优化插件入口。"""

from __future__ import annotations

from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import asyncio
import json
import os
import re
import uuid

from maibot_sdk import Command, MaiBotPlugin, ON_BOT_CONFIG_RELOAD, ON_MODEL_CONFIG_RELOAD

from .config import CONFIG_VERSION, PersonaOptimizerConfig
from .llm_client import LLMRouter, ModelSelection
from .prompts import build_direction_prompt, build_persona_prompt, build_summary_prompt
from .storage import ResultStorage, atomic_write_json
from .utils import (
    extract_json_object,
    iso_now,
    now_local,
    parse_iso_datetime,
    public_endpoint_label,
    split_text_chunks,
    validate_generated_persona,
)


PLUGIN_ID = "local.maimai.persona-optimizer"
STATE_SCHEMA_VERSION = 1
SUPPORTED_MODEL_MODES = {"planner", "replyer", "maibot_task", "openai_compatible"}


class OptimizationBusyError(RuntimeError):
    """已有优化任务运行中。"""


@dataclass(frozen=True, slots=True)
class ChatRecordBatch:
    """一次采集得到的聊天记录。"""

    text: str
    message_count: int
    stream_count: int
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True, slots=True)
class OptimizationOutcome:
    """一次优化执行结果。"""

    saved: bool
    message: str
    result_id: str = ""
    message_count: int = 0
    stream_count: int = 0
    change_summary: str = ""


class PersonaOptimizerPlugin(MaiBotPlugin):
    """按小时分析聊天并生成人设优化版本。"""

    config_model = PersonaOptimizerConfig
    config_reload_subscriptions = {ON_BOT_CONFIG_RELOAD, ON_MODEL_CONFIG_RELOAD}

    def __init__(self) -> None:
        super().__init__()
        self._state_path: Path | None = None
        self._state: dict[str, Any] = self._default_state()
        self._storage: ResultStorage | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

    def get_components(self) -> list[dict[str, Any]]:
        """收集组件并把 SDK 的扩展元数据展开到 Host 标准层级。"""

        components = super().get_components()
        for component in components:
            metadata = component.get("metadata")
            if not isinstance(metadata, dict):
                continue
            extension_metadata = metadata.pop("metadata", None)
            if isinstance(extension_metadata, dict):
                metadata.update(extension_metadata)
        return components

    async def on_load(self) -> None:
        """初始化持久化目录并启动小时调度器。"""

        if self.config.plugin.config_version != CONFIG_VERSION:
            self.ctx.logger.warning(
                "插件配置版本为 %s，当前代码版本为 %s；建议在 WebUI 检查配置",
                self.config.plugin.config_version,
                CONFIG_VERSION,
            )

        data_dir = self.ctx.paths.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = data_dir / "state.json"
        self._state = self._load_state()
        self._storage = ResultStorage(data_dir / "settings", self.config.storage.history_limit)
        self._storage.initialize()

        if self._effective_enabled() and self._effective_requirement() and not self._state.get("next_run_at"):
            await self._schedule_from_now()

        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(),
            name="persona_optimizer_hourly_scheduler",
        )
        self.ctx.logger.info("人设优化插件已加载，设定目录：%s", self._storage.settings_dir)

    async def on_unload(self) -> None:
        """停止调度器。"""

        self._stop_event.set()
        self._wake_event.set()
        task = self._scheduler_task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._scheduler_task = None
        self.ctx.logger.info("人设优化插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """响应插件自身及主配置热更新。"""

        del config_data, version
        if scope == "self" and self._storage is not None:
            self._storage.history_limit = self.config.storage.history_limit
            if self._effective_enabled() and self._effective_requirement():
                await self._schedule_from_now()
            else:
                await self._update_state(next_run_at="")
        if scope in {"self", ON_BOT_CONFIG_RELOAD, ON_MODEL_CONFIG_RELOAD}:
            self._wake_event.set()
            self.ctx.logger.info("检测到 %s 配置更新，调度状态已刷新", scope)

    @Command(
        "persona_optimizer",
        description="配置、触发和查看 MaiBot 人设优化",
        pattern=r"^(?:/人设优化|/persona_opt|/po)(?:\s+(?P<args>[\s\S]*))?\s*$",
        timeout_ms=900000,
    )
    async def handle_persona_optimizer_command(
        self,
        stream_id: str = "",
        platform: str = "",
        user_id: str = "",
        matched_groups: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        """处理全部 QQ 管理指令。"""

        arguments = self._extract_command_arguments(matched_groups, kwargs)
        action, _, payload = arguments.partition(" ")
        normalized_action = action.strip().lower()
        payload = payload.strip()

        if not normalized_action or normalized_action in {"help", "帮助"}:
            if not self.config.security.allow_public_help and not await self._is_administrator(platform, user_id):
                return await self._command_reply(stream_id, False, "你没有权限查看该插件帮助。")
            return await self._command_reply(stream_id, True, self._help_text())

        if not await self._is_administrator(platform, user_id):
            return await self._command_reply(stream_id, False, "你没有权限控制人设优化插件。")

        try:
            if normalized_action in {"status", "状态"}:
                return await self._command_reply(stream_id, True, self._build_status_text())
            if normalized_action in {"requirement", "要求"}:
                return await self._handle_requirement_command(stream_id, payload)
            if normalized_action in {"run", "立即", "手动", "触发"}:
                return await self._handle_manual_run_command(stream_id, payload)
            if normalized_action in {"history", "历史", "记录"}:
                return await self._command_reply(stream_id, True, self._build_history_text(payload))
            if normalized_action in {"view", "查看"}:
                return await self._command_reply(stream_id, True, self._build_record_text(payload or "latest"))
            if normalized_action in {"model", "模型"}:
                return await self._handle_model_command(stream_id, payload)
            if normalized_action in {"enable", "开启"}:
                await self._set_override("enabled", True)
                if self._effective_requirement():
                    await self._schedule_from_now()
                    message = "自动人设优化已开启，下次执行时间已重新计算。"
                else:
                    await self._update_state(next_run_at="")
                    message = "自动人设优化已开启；设置优化要求后才会安排任务。"
                return await self._command_reply(stream_id, True, message)
            if normalized_action in {"disable", "关闭"}:
                await self._set_override("enabled", False)
                await self._update_state(next_run_at="")
                self._wake_event.set()
                return await self._command_reply(stream_id, True, "自动人设优化已关闭，手动触发仍可使用。")
            if normalized_action in {"reset", "重置"}:
                await self._reset_command_overrides()
                return await self._command_reply(stream_id, True, "QQ 指令覆盖项已清除，恢复使用 config.toml 配置。")
        except OptimizationBusyError:
            return await self._command_reply(stream_id, False, "已有一轮优化正在执行，请稍后再试。")
        except Exception as error:
            self.ctx.logger.exception("处理人设优化命令失败")
            return await self._command_reply(stream_id, False, f"操作失败：{error}")

        return await self._command_reply(stream_id, False, "未知子命令。发送 /人设优化 帮助 查看用法。")

    async def _handle_requirement_command(self, stream_id: str, payload: str) -> tuple[bool, str, bool]:
        if not payload:
            requirement = self._effective_requirement()
            text = f"当前优化要求：\n{requirement}" if requirement else "当前未设置优化要求。"
            return await self._command_reply(stream_id, True, text)

        if payload.lower() in {"clear", "清除", "清空"}:
            await self._set_override("requirement", "")
            await self._update_state(next_run_at="")
            self._wake_event.set()
            return await self._command_reply(stream_id, True, "优化要求已清除，自动任务将暂停。")

        if len(payload) > 4000:
            return await self._command_reply(stream_id, False, "优化要求过长，请控制在 4000 字以内。")
        await self._set_override("requirement", payload)
        await self._schedule_from_now()
        return await self._command_reply(
            stream_id,
            True,
            f"优化要求已保存。插件将在 {self.config.optimization.interval_minutes} 分钟后自动执行；也可发送 /人设优化 立即。",
        )

    async def _handle_manual_run_command(self, stream_id: str, payload: str) -> tuple[bool, str, bool]:
        if not self._effective_requirement():
            return await self._command_reply(stream_id, False, "请先发送 /人设优化 要求 <内容>。")
        hours = 1
        if payload:
            try:
                hours = int(payload)
            except ValueError:
                return await self._command_reply(stream_id, False, "用法：/人设优化 立即 [回看小时数]")
        if hours < 1 or hours > 168:
            return await self._command_reply(stream_id, False, "回看小时数必须在 1 到 168 之间。")
        if self._run_lock.locked():
            raise OptimizationBusyError

        await self._send_text(stream_id, f"开始分析最近 {hours} 小时聊天，完成后会自动保存优化版本。")
        outcome = await self._run_optimization(trigger="manual", hours=float(hours))
        if not outcome.saved:
            return await self._command_reply(stream_id, False, outcome.message)
        response = (
            f"人设优化完成并已保存。\n"
            f"版本：{outcome.result_id}\n"
            f"样本：{outcome.message_count} 条消息 / {outcome.stream_count} 个聊天流\n"
            f"改动：{outcome.change_summary}\n"
            f"发送 /人设优化 查看 最新 可查看完整设定。"
        )
        return await self._command_reply(stream_id, True, response)

    async def _handle_model_command(self, stream_id: str, payload: str) -> tuple[bool, str, bool]:
        if not payload:
            selection = self._effective_model_selection()
            custom_endpoint = public_endpoint_label(selection.custom_endpoint)
            response = (
                f"当前模型：{selection.display_name}\n"
                f"自定义接口：{custom_endpoint}\n"
                f"API Key：{'已配置' if selection.custom_api_key else '未配置'}"
            )
            return await self._command_reply(stream_id, True, response)

        model_action, _, model_payload = payload.partition(" ")
        normalized = model_action.strip().lower()
        model_payload = model_payload.strip()

        if normalized in {"list", "列表"}:
            available = await self.ctx.llm.get_available_models()
            tasks = [str(item) for item in available] if isinstance(available, list) else []
            return await self._command_reply(
                stream_id,
                True,
                "可用 MaiBot 模型任务：" + ("、".join(tasks) if tasks else "未读取到"),
            )
        if normalized in {"planner", "planne", "规划"}:
            await self._set_override("model_mode", "planner")
            return await self._command_reply(stream_id, True, "已切换为 planner 模型任务。")
        if normalized in {"replyer", "回复"}:
            await self._set_override("model_mode", "replyer")
            return await self._command_reply(stream_id, True, "已切换为 replyer 模型任务。")
        if normalized in {"task", "任务"}:
            if not model_payload:
                return await self._command_reply(stream_id, False, "用法：/人设优化 模型 任务 <任务名>")
            available = await self.ctx.llm.get_available_models()
            task_names = {str(item) for item in available} if isinstance(available, list) else set()
            if model_payload not in task_names:
                return await self._command_reply(
                    stream_id,
                    False,
                    f"未找到模型任务 {model_payload}。请先发送 /人设优化 模型 列表。",
                )
            await self._set_overrides({"model_mode": "maibot_task", "maibot_task_name": model_payload})
            return await self._command_reply(stream_id, True, f"已切换为 MaiBot 模型任务 {model_payload}。")
        if normalized in {"custom", "自定义"}:
            if not model_payload:
                return await self._command_reply(stream_id, False, "用法：/人设优化 模型 自定义 <模型标识>")
            await self._set_overrides({"model_mode": "openai_compatible", "custom_model_name": model_payload})
            configured = bool(self.config.model.custom_endpoint)
            suffix = "" if configured else "；仍需在 WebUI/config.toml 配置 custom_endpoint"
            return await self._command_reply(stream_id, True, f"已选择自定义模型 {model_payload}{suffix}。")
        if normalized in {"reset", "重置"}:
            await self._remove_overrides({"model_mode", "maibot_task_name", "custom_model_name"})
            return await self._command_reply(stream_id, True, "模型选择已恢复为 config.toml 配置。")

        return await self._command_reply(stream_id, False, "模型用法：planner、replyer、任务 <名称>、自定义 <模型标识>、列表。")

    async def _scheduler_loop(self) -> None:
        """按持久化的下次执行时间运行自动优化。"""

        while not self._stop_event.is_set():
            try:
                if not self._effective_enabled() or not self._effective_requirement():
                    await self._wait_for_wake(None)
                    continue

                next_run = parse_iso_datetime(self._state.get("next_run_at"))
                if next_run is None:
                    await self._schedule_from_now()
                    next_run = parse_iso_datetime(self._state.get("next_run_at"))
                if next_run is None:
                    await self._wait_for_wake(60)
                    continue

                delay = max(0.0, (next_run - now_local()).total_seconds())
                if delay > 0 and await self._wait_for_wake(delay):
                    continue
                if self._stop_event.is_set():
                    break

                interval_minutes = self.config.optimization.interval_minutes
                if self._run_lock.locked():
                    self.ctx.logger.info("已有手动优化运行，本轮自动任务顺延")
                else:
                    try:
                        outcome = await self._run_optimization(
                            trigger="scheduled",
                            hours=interval_minutes / 60.0,
                        )
                        self.ctx.logger.info("自动人设优化：%s", outcome.message)
                    except Exception:
                        self.ctx.logger.exception("自动人设优化失败")
                await self._schedule_from_now()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.ctx.logger.exception("人设优化调度循环异常，60 秒后重试")
                await self._wait_for_wake(60)

    async def _run_optimization(self, trigger: str, hours: float) -> OptimizationOutcome:
        """执行概括、方向分析和完整人设生成。"""

        if self._run_lock.locked():
            raise OptimizationBusyError
        async with self._run_lock:
            await self._update_state(last_attempt_at=iso_now(), last_error="")
            try:
                source = await self._collect_chat_records(hours)
                minimum = self.config.optimization.minimum_messages
                if source.message_count < minimum:
                    message = f"聊天样本不足：仅 {source.message_count} 条，至少需要 {minimum} 条。"
                    await self._update_state(last_error=message)
                    return OptimizationOutcome(
                        saved=False,
                        message=message,
                        message_count=source.message_count,
                        stream_count=source.stream_count,
                    )

                selection = self._effective_model_selection()
                router = LLMRouter(self.ctx, selection)
                model_names: list[str] = []

                chunks = split_text_chunks(source.text, self.config.optimization.summary_chunk_chars)
                summaries: list[str] = []
                for index, chunk in enumerate(chunks, start=1):
                    generated = await router.generate(
                        build_summary_prompt(chunk, index, len(chunks)),
                        self.config.model.summary_max_tokens,
                    )
                    summaries.append(f"### 分块 {index}\n{generated.text}")
                    model_names.append(generated.model)
                combined_summary = "\n\n".join(summaries)

                requirement = self._effective_requirement()
                direction_result = await router.generate(
                    build_direction_prompt(combined_summary, requirement),
                    self.config.model.direction_max_tokens,
                )
                model_names.append(direction_result.model)

                current_personality = str(await self.ctx.config.get("personality.personality", "") or "").strip()
                current_reply_style = str(await self.ctx.config.get("personality.reply_style", "") or "").strip()
                current_plan_style = str(await self.ctx.config.get("personality.plan_style", "") or "").strip()
                if not current_personality:
                    raise RuntimeError("未能从 MaiBot 主配置读取 personality.personality")

                persona_result = await router.generate(
                    build_persona_prompt(
                        current_personality=current_personality,
                        current_reply_style=current_reply_style,
                        current_plan_style=current_plan_style,
                        requirement=requirement,
                        summaries=combined_summary,
                        directions=direction_result.text,
                    ),
                    self.config.model.persona_max_tokens,
                )
                model_names.append(persona_result.model)
                optimized = extract_json_object(persona_result.text)
                optimized_personality = str(optimized.get("personality") or "").strip()
                optimized_reply_style = str(optimized.get("reply_style") or current_reply_style).strip()
                optimized_plan_style = str(optimized.get("plan_style") or "").strip()
                change_summary = str(optimized.get("change_summary") or "已按聊天表现与管理员要求优化。").strip()
                self._validate_generated_persona(
                    optimized_personality,
                    optimized_reply_style,
                    optimized_plan_style,
                )

                result_id = f"{now_local().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
                unique_models = list(dict.fromkeys(name for name in model_names if name))
                payload = {
                    "schema_version": 1,
                    "id": result_id,
                    "created_at": iso_now(),
                    "trigger": trigger,
                    "model_mode": selection.mode,
                    "model": "、".join(unique_models) or selection.display_name,
                    "window_start": source.window_start.isoformat(timespec="seconds"),
                    "window_end": source.window_end.isoformat(timespec="seconds"),
                    "message_count": source.message_count,
                    "stream_count": source.stream_count,
                    "requirement": requirement,
                    "chat_summary": combined_summary,
                    "optimization_directions": direction_result.text,
                    "source_personality": current_personality,
                    "source_reply_style": current_reply_style,
                    "source_plan_style": current_plan_style,
                    "optimized_personality": optimized_personality,
                    "optimized_reply_style": optimized_reply_style,
                    "optimized_plan_style": optimized_plan_style,
                    "change_summary": change_summary,
                }
                if self._storage is None:
                    raise RuntimeError("结果存储尚未初始化")
                self._storage.save_result(payload)
                await self._update_state(
                    last_success_at=payload["created_at"],
                    last_result_id=result_id,
                    last_error="",
                    successful_runs=int(self._state.get("successful_runs") or 0) + 1,
                )
                return OptimizationOutcome(
                    saved=True,
                    message=f"已保存优化版本 {result_id}",
                    result_id=result_id,
                    message_count=source.message_count,
                    stream_count=source.stream_count,
                    change_summary=change_summary,
                )
            except Exception as error:
                await self._update_state(last_error=f"{type(error).__name__}: {error}"[:800])
                raise

    async def _collect_chat_records(self, hours: float) -> ChatRecordBatch:
        """读取时间窗内聊天，按聊天流整理为可读文本。"""

        window_end = now_local()
        window_start = window_end - timedelta(hours=max(hours, 1 / 60))
        raw_messages = await self.ctx.message.get_by_time(
            str(window_start.timestamp()),
            str(window_end.timestamp()),
            limit=self.config.optimization.max_total_messages,
            limit_mode="latest",
            include_binary_data=False,
        )
        messages = raw_messages if isinstance(raw_messages, list) else []

        allowed_platforms = {item.strip().lower() for item in self.config.chat_source.platforms if item.strip()}
        excluded_streams = {item.strip() for item in self.config.chat_source.excluded_stream_ids if item.strip()}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for message in messages:
            if not isinstance(message, dict):
                continue
            platform = str(message.get("platform") or "").strip().lower()
            if allowed_platforms and platform not in allowed_platforms:
                continue
            stream_id = str(message.get("session_id") or "").strip()
            if not stream_id or stream_id in excluded_streams:
                continue

            message_info = message.get("message_info")
            message_info = message_info if isinstance(message_info, dict) else {}
            group_info = message_info.get("group_info")
            is_group = isinstance(group_info, dict) and bool(str(group_info.get("group_id") or "").strip())
            if is_group and not self.config.chat_source.include_group_chats:
                continue
            if not is_group and not self.config.chat_source.include_private_chats:
                continue
            if self.config.chat_source.exclude_commands and bool(message.get("is_command")):
                continue
            plain_text = str(message.get("processed_plain_text") or "").strip()
            if plain_text.startswith(("/人设优化", "/persona_opt", "/po")):
                continue
            grouped[stream_id].append(message)

        max_per_stream = self.config.optimization.max_messages_per_stream
        stream_items: list[tuple[str, list[dict[str, Any]]]] = []
        for stream_id, stream_messages in grouped.items():
            stream_messages.sort(key=self._message_timestamp)
            stream_items.append((stream_id, stream_messages[-max_per_stream:]))
        stream_items.sort(
            key=lambda item: self._message_timestamp(item[1][-1]) if item[1] else 0.0,
            reverse=True,
        )
        stream_items = stream_items[: self.config.optimization.max_streams]

        sections: list[str] = []
        included_messages = 0
        total_chars = 0
        max_total_chars = self.config.optimization.max_total_chars
        for stream_index, (_stream_id, stream_messages) in enumerate(stream_items, start=1):
            readable = await self.ctx.message.build_readable(
                stream_messages,
                replace_bot_name=True,
                timestamp_mode="normal",
                truncate=False,
            )
            if not isinstance(readable, str) or not readable.strip():
                readable = self._fallback_readable(stream_messages)
            readable = readable.strip()
            if not readable:
                continue

            label = self._stream_label(stream_messages, stream_index)
            section = f"## {label}\n{readable}"
            separator_length = 2 if sections else 0
            remaining = max_total_chars - total_chars - separator_length
            if remaining <= 0:
                break
            if len(section) > remaining:
                section = section[:remaining].rstrip() + "\n[记录因字符上限截断]"
            sections.append(section)
            total_chars += len(section) + separator_length
            included_messages += len(stream_messages)
            if total_chars >= max_total_chars:
                break

        return ChatRecordBatch(
            text="\n\n".join(sections),
            message_count=included_messages,
            stream_count=len(sections),
            window_start=window_start,
            window_end=window_end,
        )

    def _validate_generated_persona(self, personality: str, reply_style: str, plan_style: str) -> None:
        validate_generated_persona(
            personality,
            reply_style,
            plan_style,
            max_personality_chars=self.config.optimization.max_personality_chars,
            max_reply_style_chars=self.config.optimization.max_reply_style_chars,
            max_plan_style_chars=self.config.optimization.max_plan_style_chars,
        )

    async def _is_administrator(self, platform: str, user_id: str) -> bool:
        normalized_user = str(user_id or "").strip().lower()
        normalized_platform = str(platform or "").strip().lower()
        if not normalized_user:
            return False
        candidates = {normalized_user}
        if normalized_platform:
            candidates.add(f"{normalized_platform}:{normalized_user}")

        administrators = {
            str(item).strip().lower()
            for item in self.config.security.administrators
            if str(item).strip()
        }
        if candidates & administrators:
            return True
        if not self.config.security.inherit_plugin_management_permissions:
            return False

        inherited = await self.ctx.config.get("plugin.permission", [])
        inherited = inherited if isinstance(inherited, list) else []
        inherited_permissions = {
            str(item).strip().lower()
            for item in inherited
            if str(item).strip()
        }
        return bool(candidates & inherited_permissions)

    def _effective_enabled(self) -> bool:
        overrides = self._state.get("overrides")
        if isinstance(overrides, dict) and isinstance(overrides.get("enabled"), bool):
            return overrides["enabled"]
        return bool(self.config.plugin.enabled)

    def _effective_requirement(self) -> str:
        overrides = self._state.get("overrides")
        if isinstance(overrides, dict) and "requirement" in overrides:
            return str(overrides.get("requirement") or "").strip()
        return self.config.optimization.requirement.strip()

    def _effective_model_selection(self) -> ModelSelection:
        overrides = self._state.get("overrides")
        overrides = overrides if isinstance(overrides, dict) else {}
        mode = str(overrides.get("model_mode") or self.config.model.mode).strip()
        if mode not in SUPPORTED_MODEL_MODES:
            mode = self.config.model.mode
        task_name = str(overrides.get("maibot_task_name") or self.config.model.maibot_task_name).strip()
        custom_model_name = str(overrides.get("custom_model_name") or self.config.model.custom_model_name).strip()
        return ModelSelection(
            mode=mode,
            maibot_task_name=task_name,
            temperature=self.config.model.temperature,
            custom_endpoint=self.config.model.custom_endpoint,
            custom_api_key=self.config.model.custom_api_key,
            custom_model_name=custom_model_name,
            custom_timeout_seconds=self.config.model.custom_timeout_seconds,
        )

    async def _schedule_from_now(self) -> None:
        next_run = now_local() + timedelta(minutes=self.config.optimization.interval_minutes)
        await self._update_state(next_run_at=next_run.isoformat(timespec="seconds"))
        self._wake_event.set()

    async def _wait_for_wake(self, timeout_seconds: float | None) -> bool:
        """等待配置唤醒；返回 True 表示被唤醒而非超时。"""

        if self._wake_event.is_set():
            self._wake_event.clear()
            return True
        try:
            if timeout_seconds is None:
                await self._wake_event.wait()
            else:
                await asyncio.wait_for(self._wake_event.wait(), timeout=max(0.0, timeout_seconds))
        except asyncio.TimeoutError:
            return False
        self._wake_event.clear()
        return True

    async def _set_override(self, key: str, value: Any) -> None:
        await self._set_overrides({key: value})

    async def _set_overrides(self, values: dict[str, Any]) -> None:
        async with self._state_lock:
            overrides = self._state.setdefault("overrides", {})
            if not isinstance(overrides, dict):
                overrides = {}
                self._state["overrides"] = overrides
            overrides.update(values)
            self._persist_state()
        self._wake_event.set()

    async def _remove_overrides(self, keys: set[str]) -> None:
        async with self._state_lock:
            overrides = self._state.get("overrides")
            if isinstance(overrides, dict):
                for key in keys:
                    overrides.pop(key, None)
            self._persist_state()
        self._wake_event.set()

    async def _reset_command_overrides(self) -> None:
        async with self._state_lock:
            self._state["overrides"] = {}
            self._state["next_run_at"] = ""
            self._persist_state()
        if self._effective_enabled() and self._effective_requirement():
            await self._schedule_from_now()
        self._wake_event.set()

    async def _update_state(self, **values: Any) -> None:
        async with self._state_lock:
            self._state.update(values)
            self._persist_state()

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        atomic_write_json(self._state_path, self._state)

    def _load_state(self) -> dict[str, Any]:
        if self._state_path is None or not self._state_path.exists():
            return self._default_state()
        try:
            loaded = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("state.json 顶层不是对象")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            corrupt_path = self._state_path.with_name(f"state.corrupt-{now_local().strftime('%Y%m%d-%H%M%S')}.json")
            with suppress(OSError):
                os.replace(self._state_path, corrupt_path)
            self.ctx.logger.warning("状态文件损坏，已重建：%s", error)
            return self._default_state()

        state = self._default_state()
        state.update(loaded)
        if not isinstance(state.get("overrides"), dict):
            state["overrides"] = {}
        state["schema_version"] = STATE_SCHEMA_VERSION
        return state

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "overrides": {},
            "next_run_at": "",
            "last_attempt_at": "",
            "last_success_at": "",
            "last_result_id": "",
            "last_error": "",
            "successful_runs": 0,
        }

    def _build_status_text(self) -> str:
        requirement = self._effective_requirement()
        selection = self._effective_model_selection()
        next_run = str(self._state.get("next_run_at") or "未安排")
        last_success = str(self._state.get("last_success_at") or "尚无")
        last_result = str(self._state.get("last_result_id") or "尚无")
        last_error = str(self._state.get("last_error") or "无")
        if len(requirement) > 300:
            requirement = requirement[:300] + "…"
        if len(last_error) > 300:
            last_error = last_error[:300] + "…"
        return (
            f"自动优化：{'开启' if self._effective_enabled() else '关闭'}\n"
            f"执行间隔：{self.config.optimization.interval_minutes} 分钟\n"
            f"模型：{selection.display_name}\n"
            f"优化要求：{requirement or '未设置'}\n"
            f"下次执行：{next_run}\n"
            f"最近成功：{last_success}\n"
            f"最近版本：{last_result}\n"
            f"最近错误：{last_error}"
        )

    def _build_history_text(self, payload: str) -> str:
        if self._storage is None:
            return "结果存储尚未初始化。"
        records = self._storage.list_records()
        if not records:
            return "还没有保存的人设优化版本。"
        page = 1
        if payload:
            with suppress(ValueError):
                page = max(1, int(payload))
        page_size = 10
        start = (page - 1) * page_size
        selected = records[start : start + page_size]
        if not selected:
            return "该页没有记录。"
        lines = [f"已保存的人设优化（第 {page} 页）："]
        for index, record in enumerate(selected, start=start + 1):
            summary = str(record.get("change_summary") or "").replace("\n", " ")
            if len(summary) > 80:
                summary = summary[:80] + "…"
            lines.append(f"{index}. {record['id']}｜{record['message_count']} 条｜{summary}")
        lines.append("发送 /人设优化 查看 <序号|版本ID|最新> 查看详情。")
        return "\n".join(lines)

    def _build_record_text(self, identifier: str) -> str:
        if self._storage is None:
            return "结果存储尚未初始化。"
        record = self._storage.load_record(identifier)
        if record is None:
            return "未找到该优化版本。"
        text = (
            f"人设优化版本 {record.get('id', '')}\n"
            f"时间：{record.get('created_at', '')}\n"
            f"模型：{record.get('model', '')}\n"
            f"改动：{record.get('change_summary', '')}\n\n"
            f"【优化后人格】\n{record.get('optimized_personality', '')}\n\n"
            f"【优化后表达风格】\n{record.get('optimized_reply_style', '')}\n\n"
            f"【优化后行为风格】\n{record.get('optimized_plan_style', '')}\n\n"
            f"【优化方向】\n{record.get('optimization_directions', '')}"
        )
        limit = self.config.storage.command_output_chars
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n…内容较长，完整版本已保存在插件 settings 目录。"
        return text

    async def _command_reply(self, stream_id: str, success: bool, text: str) -> tuple[bool, str, bool]:
        await self._send_text(stream_id, text)
        return success, text, True

    async def _send_text(self, stream_id: str, text: str) -> None:
        if stream_id:
            await self.ctx.send.text(text, stream_id)

    @staticmethod
    def _extract_command_arguments(matched_groups: dict[str, Any] | None, kwargs: dict[str, Any]) -> str:
        if isinstance(matched_groups, dict):
            captured = matched_groups.get("args")
            if isinstance(captured, str):
                return captured.strip()
        raw_text = str(kwargs.get("text") or "").strip()
        return re.sub(r"^(?:/人设优化|/persona_opt|/po)\s*", "", raw_text, flags=re.IGNORECASE).strip()

    @staticmethod
    def _message_timestamp(message: dict[str, Any]) -> float:
        try:
            return float(message.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _stream_label(messages: list[dict[str, Any]], index: int) -> str:
        if not messages:
            return f"聊天流 {index}"
        message_info = messages[-1].get("message_info")
        message_info = message_info if isinstance(message_info, dict) else {}
        group_info = message_info.get("group_info")
        if isinstance(group_info, dict) and group_info.get("group_id"):
            group_name = str(group_info.get("group_name") or "未命名群聊").strip()
            return f"群聊 {index}：{group_name}"
        user_info = message_info.get("user_info")
        user_info = user_info if isinstance(user_info, dict) else {}
        nickname = str(user_info.get("user_nickname") or "私聊用户").strip()
        return f"私聊 {index}：{nickname}"

    @staticmethod
    def _fallback_readable(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            message_info = message.get("message_info")
            message_info = message_info if isinstance(message_info, dict) else {}
            user_info = message_info.get("user_info")
            user_info = user_info if isinstance(user_info, dict) else {}
            nickname = str(user_info.get("user_nickname") or "未知用户")
            content = str(message.get("processed_plain_text") or "").strip()
            if not content:
                raw_message = message.get("raw_message")
                if isinstance(raw_message, list):
                    parts: list[str] = []
                    for segment in raw_message:
                        if not isinstance(segment, dict):
                            continue
                        segment_type = str(segment.get("type") or "")
                        if segment_type == "text":
                            value = segment.get("content") or segment.get("data") or ""
                            parts.append(str(value))
                        elif segment_type:
                            parts.append(f"[{segment_type}]")
                    content = "".join(parts).strip()
            if content:
                lines.append(f"{nickname}：{content}")
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return """人设优化指令
/人设优化 状态
/人设优化 要求 <优化目标>
/人设优化 要求 清除
/人设优化 立即 [回看小时数]
/人设优化 模型 planner
/人设优化 模型 replyer
/人设优化 模型 任务 <MaiBot任务名>
/人设优化 模型 自定义 <模型标识>
/人设优化 模型 列表
/人设优化 历史 [页码]
/人设优化 查看 <序号|版本ID|最新>
/人设优化 开启 / 关闭
/人设优化 重置

自定义接口地址和 API Key 只能在 WebUI 或 config.toml 中配置。"""


def create_plugin() -> PersonaOptimizerPlugin:
    """创建插件实例。"""

    return PersonaOptimizerPlugin()
