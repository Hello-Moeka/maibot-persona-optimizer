"""MaiBot 模型任务与 OpenAI 兼容接口路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import asyncio
import json

from .utils import coerce_text_content, normalize_chat_completions_endpoint


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """一次优化使用的模型选择。"""

    mode: str
    maibot_task_name: str
    temperature: float
    custom_endpoint: str
    custom_api_key: str
    custom_model_name: str
    custom_timeout_seconds: int

    @property
    def display_name(self) -> str:
        if self.mode == "maibot_task":
            return f"MaiBot 任务/{self.maibot_task_name}"
        if self.mode == "openai_compatible":
            return f"自定义/{self.custom_model_name or '未配置'}"
        return f"MaiBot 任务/{self.mode}"


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """标准化模型返回。"""

    text: str
    model: str


class LLMRouter:
    """按用户选择调用 MaiBot 或自定义模型。"""

    def __init__(self, context: Any, selection: ModelSelection) -> None:
        self._context = context
        self._selection = selection

    async def generate(self, prompt: str, max_tokens: int) -> GenerationResult:
        """生成文本并统一检查错误。"""

        if self._selection.mode == "openai_compatible":
            return await self._generate_openai_compatible(prompt, max_tokens)

        task_name = self._selection.mode
        if task_name == "maibot_task":
            task_name = self._selection.maibot_task_name.strip()
        if not task_name:
            raise RuntimeError("未配置 MaiBot 模型任务名")

        result = await self._context.llm.generate(
            prompt,
            model=task_name,
            temperature=self._selection.temperature,
            max_tokens=max_tokens,
        )
        if not isinstance(result, dict) or not result.get("success"):
            error = "未知错误"
            if isinstance(result, dict):
                error = str(result.get("error") or result.get("response") or "未知错误")
            raise RuntimeError(f"MaiBot LLM 调用失败：{error}")
        response = str(result.get("response") or "").strip()
        if not response:
            raise RuntimeError("MaiBot LLM 返回空内容")
        model = str(result.get("model") or task_name).strip()
        return GenerationResult(text=response, model=model)

    async def _generate_openai_compatible(self, prompt: str, max_tokens: int) -> GenerationResult:
        endpoint = normalize_chat_completions_endpoint(self._selection.custom_endpoint)
        model_name = self._selection.custom_model_name.strip()
        if not model_name:
            raise RuntimeError("未配置自定义模型名称")

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._selection.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        return await asyncio.to_thread(self._perform_openai_request, endpoint, payload)

    def _perform_openai_request(self, endpoint: str, payload: dict[str, Any]) -> GenerationResult:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        api_key = self._selection.custom_api_key.strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._selection.custom_timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            error_body = error.read(800).decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"自定义模型接口返回 HTTP {error.code}：{error_body or error.reason}") from error
        except URLError as error:
            raise RuntimeError(f"无法连接自定义模型接口：{error.reason}") from error
        except TimeoutError as error:
            raise RuntimeError("自定义模型接口请求超时") from error

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError("自定义模型接口返回的不是合法 JSON") from error

        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            api_error = parsed.get("error") if isinstance(parsed, dict) else None
            raise RuntimeError(f"自定义模型接口缺少 choices：{api_error or '响应结构不兼容'}")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else choices[0].get("text")
        text = coerce_text_content(content)
        if not text:
            raise RuntimeError("自定义模型接口返回空内容")
        returned_model = str(parsed.get("model") or self._selection.custom_model_name).strip()
        return GenerationResult(text=text, model=returned_model)

