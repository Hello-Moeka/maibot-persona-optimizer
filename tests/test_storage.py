from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import unittest

from storage import ResultStorage


def build_payload(result_id: str) -> dict[str, object]:
    return {
        "id": result_id,
        "created_at": "2026-07-10T12:00:00+08:00",
        "trigger": "manual",
        "model": "planner",
        "window_start": "2026-07-10T11:00:00+08:00",
        "window_end": "2026-07-10T12:00:00+08:00",
        "message_count": 12,
        "requirement": "更自然",
        "chat_summary": "聊天概括",
        "optimization_directions": "优化方向",
        "change_summary": "减少模板化表达",
        "optimized_personality": "这是优化后的完整人格设定。",
        "optimized_reply_style": "表达自然简短。",
        "source_personality": "原人格",
        "source_reply_style": "原风格",
    }


class StorageTests(unittest.TestCase):
    def test_save_and_load_latest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            storage = ResultStorage(Path(temporary_directory) / "settings", history_limit=5)
            payload = build_payload("20260710-120000-ab12cd34")
            storage.save_result(payload)

            latest = storage.load_record("latest")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["id"], payload["id"])
            self.assertTrue((storage.settings_dir / "latest.toml").exists())

    def test_numeric_history_lookup_and_prune(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            storage = ResultStorage(Path(temporary_directory) / "settings", history_limit=2)
            storage.save_result(build_payload("20260710-120000-ab12cd34"))
            storage.save_result(build_payload("20260710-130000-bc23de45"))
            storage.save_result(build_payload("20260710-140000-cd34ef56"))

            self.assertEqual(len(storage.list_records()), 2)
            newest = storage.load_record("1")
            self.assertIsNotNone(newest)
            self.assertEqual(newest["id"], "20260710-140000-cd34ef56")


if __name__ == "__main__":
    unittest.main()

