"""不依赖 MaiBot 运行时的通用辅助函数。"""

from __future__ import annotations

from datetime import datetime
from json import JSONDecodeError, JSONDecoder
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import json
import re


def now_local() -> datetime:
    """返回带本地时区的当前时间。"""

    return datetime.now().astimezone()


def iso_now() -> str:
    """返回秒级 ISO 时间。"""

    return now_local().isoformat(timespec="seconds")


def parse_iso_datetime(value: Any) -> datetime | None:
    """安全解析 ISO 时间。"""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取第一个合法 JSON 对象。"""

    normalized = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, flags=re.IGNORECASE | re.DOTALL)
    if fenced is not None:
        normalized = fenced.group(1).strip()

    try:
        direct = json.loads(normalized)
    except JSONDecodeError:
        direct = None
    if isinstance(direct, dict):
        return direct

    decoder = JSONDecoder()
    for position, character in enumerate(normalized):
        if character != "{":
            continue
        try:
            candidate, _end = decoder.raw_decode(normalized[position:])
        except JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    raise ValueError("模型未返回合法 JSON 对象")


def split_text_chunks(text: str, max_chars: int) -> list[str]:
    """优先按段落边界拆分文本。"""

    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")
    normalized = str(text or "").strip()
    if not normalized:
        return []

    paragraphs = re.split(r"\n{2,}", normalized)
    chunks: list[str] = []
    current_parts: list[str] = []
    current_length = 0

    def flush_current() -> None:
        nonlocal current_parts, current_length
        if current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = []
            current_length = 0

    for paragraph in paragraphs:
        clean_paragraph = paragraph.strip()
        if not clean_paragraph:
            continue
        if len(clean_paragraph) > max_chars:
            flush_current()
            for start in range(0, len(clean_paragraph), max_chars):
                chunks.append(clean_paragraph[start : start + max_chars])
            continue

        added_length = len(clean_paragraph) + (2 if current_parts else 0)
        if current_parts and current_length + added_length > max_chars:
            flush_current()
        current_parts.append(clean_paragraph)
        current_length += len(clean_paragraph) + (2 if len(current_parts) > 1 else 0)

    flush_current()
    return chunks


def normalize_chat_completions_endpoint(endpoint: str) -> str:
    """把 base URL 规范化为 OpenAI Chat Completions 地址。"""

    normalized = str(endpoint or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("未配置自定义模型接口地址")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("自定义模型接口必须是有效的 http(s) 地址")

    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        final_path = path
    elif path.endswith("/v1"):
        final_path = f"{path}/chat/completions"
    elif not path:
        final_path = "/v1/chat/completions"
    else:
        final_path = f"{path}/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, final_path, parsed.query, ""))


def public_endpoint_label(endpoint: str) -> str:
    """只保留接口协议、主机和路径，隐藏查询参数。"""

    try:
        parsed = urlsplit(str(endpoint or "").strip())
    except ValueError:
        return "未配置"
    if not parsed.netloc:
        return "未配置"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def coerce_text_content(content: Any) -> str:
    """兼容 OpenAI 兼容接口的字符串或多段文本响应。"""

    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            value = item.get("text") or item.get("content")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(part.strip() for part in parts if part.strip()).strip()

