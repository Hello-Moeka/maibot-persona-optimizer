from __future__ import annotations

import unittest

from utils import (
    coerce_text_content,
    extract_json_object,
    normalize_chat_completions_endpoint,
    split_text_chunks,
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


if __name__ == "__main__":
    unittest.main()

