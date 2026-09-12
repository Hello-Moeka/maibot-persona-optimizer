"""优化版本的规范化持久化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json
import os
import re


_RESULT_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")


def atomic_write_text(path: Path, content: str) -> None:
    """在同目录中原子替换文本文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    os.replace(temporary_path, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """原子写入 JSON。"""

    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class ResultStorage:
    """管理 settings/latest 与 history 版本。"""

    def __init__(self, settings_dir: Path, history_limit: int) -> None:
        self.settings_dir = settings_dir
        self.history_dir = settings_dir / "history"
        self.history_limit = max(1, int(history_limit))

    def initialize(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def save_result(self, payload: dict[str, Any]) -> None:
        result_id = str(payload.get("id") or "")
        if not _RESULT_ID_PATTERN.fullmatch(result_id):
            raise ValueError("优化结果 ID 格式无效")
        self.initialize()

        json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        markdown_text = self._build_markdown(payload)
        toml_text = self._build_toml(payload)

        atomic_write_text(self.history_dir / f"{result_id}.json", json_text)
        atomic_write_text(self.history_dir / f"{result_id}.md", markdown_text)
        atomic_write_text(self.settings_dir / "latest.json", json_text)
        atomic_write_text(self.settings_dir / "latest.md", markdown_text)
        atomic_write_text(self.settings_dir / "latest.toml", toml_text)
        self._prune_history()

    def list_records(self) -> list[dict[str, Any]]:
        self.initialize()
        records: list[dict[str, Any]] = []
        for path in sorted(self.history_dir.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            try:
                message_count = int(payload.get("message_count") or 0)
            except (TypeError, ValueError):
                message_count = 0
            records.append(
                {
                    "id": str(payload.get("id") or path.stem),
                    "created_at": str(payload.get("created_at") or ""),
                    "trigger": str(payload.get("trigger") or ""),
                    "model": str(payload.get("model") or ""),
                    "message_count": message_count,
                    "change_summary": str(payload.get("change_summary") or ""),
                }
            )
        return records

    def load_record(self, identifier: str = "latest") -> dict[str, Any] | None:
        normalized = str(identifier or "latest").strip().lower()
        if normalized in {"latest", "最新"}:
            path = self.settings_dir / "latest.json"
        elif _RESULT_ID_PATTERN.fullmatch(normalized):
            path = self.history_dir / f"{normalized}.json"
        elif normalized.isdigit():
            index = int(normalized) - 1
            records = self.list_records()
            if index < 0 or index >= len(records):
                return None
            path = self.history_dir / f"{records[index]['id']}.json"
        else:
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _prune_history(self) -> None:
        json_paths = sorted(self.history_dir.glob("*.json"), reverse=True)
        for json_path in json_paths[self.history_limit :]:
            result_id = json_path.stem
            for suffix in (".json", ".md"):
                candidate = self.history_dir / f"{result_id}{suffix}"
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    continue

    @staticmethod
    def _build_markdown(payload: dict[str, Any]) -> str:
        return f"""# MaiBot 人设优化版本

- 版本：`{payload.get('id', '')}`
- 生成时间：{payload.get('created_at', '')}
- 触发方式：{payload.get('trigger', '')}
- 使用模型：{payload.get('model', '')}
- 数据范围：{payload.get('window_start', '')} 至 {payload.get('window_end', '')}
- 消息数：{payload.get('message_count', 0)}

## 管理员要求

{payload.get('requirement', '')}

## 聊天概括

{payload.get('chat_summary', '')}

## 优化方向

{payload.get('optimization_directions', '')}

## 改动说明

{payload.get('change_summary', '')}

## 优化后人格设定

{payload.get('optimized_personality', '')}

## 优化后表达风格

{payload.get('optimized_reply_style', '')}

## 优化后行为风格

{payload.get('optimized_plan_style', '')}

## 优化前人格设定

{payload.get('source_personality', '')}

## 优化前表达风格

{payload.get('source_reply_style', '')}

## 优化前行为风格

{payload.get('source_plan_style', '')}
"""

    @staticmethod
    def _build_toml(payload: dict[str, Any]) -> str:
        personality = json.dumps(str(payload.get("optimized_personality") or ""), ensure_ascii=False)
        reply_style = json.dumps(str(payload.get("optimized_reply_style") or ""), ensure_ascii=False)
        plan_style = json.dumps(str(payload.get("optimized_plan_style") or ""), ensure_ascii=False)
        return f"""# 由人设优化插件生成。请审阅后复制到 MaiBot 的 bot_config.toml。
# 版本: {payload.get('id', '')}

[personality]
personality = {personality}
reply_style = {reply_style}
plan_style = {plan_style}
"""
