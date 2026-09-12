from __future__ import annotations

import unittest

from utils import (
    coerce_text_content,
    extract_json_object,
    normalize_chat_completions_endpoint,
    split_text_chunks,
    validate_generated_persona,
)


class UtilsTests(unittest.TestCase):
    def test_extract_json_from_fence(self) -> None:
        result = extract_json_object('```json\n{"personality":"温柔","reply_style":"简短"}\n```')
        self.assertEqual(result["personality"], "温柔")

    def test_extract_json_from_surrounding_text(self) -> None:
        result = extract_json_object('说明如下： {"personality":"自然"} 结束')
        self.assertEqual(result, {"personality": "自然"})

    def test_split_chunks_keeps_limit(self) -> None:
        chunks = split_text_chunks("第一段内容\n\n第二段内容\n\n第三段内容", 10)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))

    def test_normalize_endpoint(self) -> None:
        self.assertEqual(
            normalize_chat_completions_endpoint("https://api.example.com/v1"),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(
            normalize_chat_completions_endpoint("https://api.example.com/v1/chat/completions"),
            "https://api.example.com/v1/chat/completions",
        )

    def test_coerce_list_content(self) -> None:
        content = [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]
        self.assertEqual(coerce_text_content(content), "第一段\n第二段")


class ValidateGeneratedPersonaTests(unittest.TestCase):
    def test_rejects_empty_plan_style(self) -> None:
        with self.assertRaisesRegex(ValueError, "行为风格为空"):
            validate_generated_persona(
                "这是足够长的人格设定文本内容。",
                "表达自然。",
                "",
                max_personality_chars=12000,
                max_reply_style_chars=8000,
                max_plan_style_chars=8000,
            )

    def test_rejects_oversized_plan_style(self) -> None:
        with self.assertRaisesRegex(ValueError, "行为风格超过配置上限"):
            validate_generated_persona(
                "这是足够长的人格设定文本内容。",
                "表达自然。",
                "x" * 101,
                max_personality_chars=12000,
                max_reply_style_chars=8000,
                max_plan_style_chars=100,
            )

    def test_accepts_valid_three_fields(self) -> None:
        validate_generated_persona(
            "这是足够长的人格设定文本内容。",
            "表达自然。",
            "1. 不重复执行相同 action",
            max_personality_chars=12000,
            max_reply_style_chars=8000,
            max_plan_style_chars=8000,
        )


if __name__ == "__main__":
    unittest.main()

