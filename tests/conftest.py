"""実 LLM 1 回転チェック用の repo 固有 fixture。"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def agent_config() -> dict:
    # tests/integration/test_end_to_end.py:57-65 の happy path で使う
    # tests/integration/test_end_to_end.py:10-19 の offline fetcher に合わせる。
    def _fake_fetch(url, timeout):
        if "algolia" in url:
            return 200, json.dumps(
                {
                    "hits": [
                        {
                            "points": 10 + i,
                            "num_comments": i,
                            "title": f"p{i}",
                            "objectID": str(i),
                        }
                        for i in range(16)
                    ]
                }
            )
        if "reddit" in url:
            return 200, json.dumps(
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "score": 5 + i,
                                    "num_comments": i,
                                    "title": f"r{i}",
                                    "permalink": f"/r/x/{i}",
                                }
                            }
                            for i in range(16)
                        ]
                    }
                }
            )
        return 404, ""

    return {"fetcher": _fake_fetch}


@pytest.fixture
def case_payload() -> str:
    # tests/integration/test_end_to_end.py:57-65 の happy path の invoke 第1引数に合わせる。
    return "植物性プロテイン"


@pytest.fixture
def live_input_context() -> dict:
    # tests/integration/test_end_to_end.py:57-65 の happy path と同じ 16 行の売上データを渡す。
    sales = "date,sales\n" + "\n".join(f"2026-01-{i + 1:02d},{100 + i * 3}" for i in range(16))
    return {"inline_data": sales}
