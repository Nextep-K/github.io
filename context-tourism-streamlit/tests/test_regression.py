"""Dependency-light regression tests; do not import the Streamlit app module."""
import ast
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def isolated(*names, overrides=None):
    funcs = [
        node for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    if len(funcs) != len(names):
        raise AssertionError(f"Expected {names}, got {[node.name for node in funcs]}")
    scope = {"re": re, "datetime": datetime, "timezone": timezone, "ZoneInfo": ZoneInfo}
    scope.update(overrides or {})
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(Path("app.py")), "exec"), scope)
    return scope


class PilotRegressionTests(unittest.TestCase):
    def test_source_compiles(self):
        compile(SOURCE, "app.py", "exec")

    def test_korea_timestamp_has_offset(self):
        fn = isolated("now_iso")["now_iso"]
        self.assertTrue(fn().endswith("+09:00"))

    def test_price_mentions_are_not_transactions(self):
        fn = isolated("parse_money")["parse_money"]
        self.assertEqual(fn("주차 요금이 6,000원인가?"), 0)
        self.assertEqual(fn("식사비 6,000원 결제했어"), 6000)
        self.assertEqual(fn("점심으로 2만원 지출했어"), 20000)
        self.assertEqual(fn("가격이 5만원이면 살까?"), 0)

    def test_notion_rich_text_never_silently_truncates(self):
        fn = isolated("rich")["rich"]
        original = "대한민국" * 1600
        nodes = fn(original)["rich_text"]
        self.assertGreater(len(nodes), 1)
        self.assertEqual("".join(node["text"]["content"] for node in nodes), original)
        self.assertTrue(all(len(node["text"]["content"]) <= 2000 for node in nodes))

    def test_notion_extreme_payload_raises_instead_of_truncating(self):
        fn = isolated("rich")["rich"]
        with self.assertRaises(ValueError):
            fn("나" * 181000)

    def test_web_search_required_for_current_external_information(self):
        fn = isolated("requires_web_search")["requires_web_search"]
        self.assertTrue(fn("월정사 근처 식당과 주차요금 찾아줘"))
        self.assertFalse(fn("너에게 설명을 부탁해"))

    def test_web_search_only_counts_completed_calls(self):
        fn = isolated("response_searched_web")["response_searched_web"]
        resp = lambda status: SimpleNamespace(output=[
            SimpleNamespace(type="web_search_call", status=status)
        ])
        self.assertTrue(fn(resp("completed")))
        self.assertFalse(fn(resp("failed")))
        self.assertFalse(fn(SimpleNamespace(output=[])))

    def test_apply_result_preserves_user_event_time(self):
        event_calls = []
        ss = SimpleNamespace(journey={
            "state": "PLANNING", "started_at": None, "ended_at": None,
            "origin": None, "destination": None, "transport": None,
            "spend_krw": 0,
        })
        fake_st = SimpleNamespace(session_state=ss)
        scope = isolated("apply_result", overrides={
            "st": fake_st,
            "ALLOWED_STATES": {"IDLE","PLANNING","MOVING","STAYING","ENDED"},
            "now_iso": lambda: "2026-10-15T05:00:00+09:00",
            "add_event": lambda *a, **kw: event_calls.append((a, kw)),
        })
        result = {"state_updates":{"state":"MOVING","origin":"진조리",
                                   "destination":"월정사","transport":"자가용",
                                   "spend_delta":0},
                  "events":[{"type":"Movement","subtype":"start"},
                            {"type":"Exposure","subtype":"parking"}]}
        scope["apply_result"](result,
            user_occurred_at="2026-10-15T04:58:00+09:00",
            assistant_occurred_at="2026-10-15T04:58:03+09:00",
            event_location={"latitude":37.0,"longitude":128.0})
        self.assertEqual(ss.journey["transport"], "car")
        self.assertEqual(event_calls[0][1]["occurred_at"], "2026-10-15T04:58:00+09:00")
        self.assertEqual(event_calls[1][1]["occurred_at"], "2026-10-15T04:58:03+09:00")

    def test_rhythm_signals_are_situational(self):
        ss = SimpleNamespace(journey={"rhythm_signals":[]})
        fake_st = SimpleNamespace(session_state=ss)
        fn = isolated("track_rhythm_context", overrides={"st":fake_st})["track_rhythm_context"]
        fn("배고파. 화장실 어디?", "2026-10-15T05:00:00+09:00",
           {"latitude":37.0,"longitude":128.0})
        self.assertEqual({x["kind"] for x in ss.journey["rhythm_signals"]},
                         {"meal", "toilet"})
        self.assertTrue(all(x["source"] == "explicit"
                            for x in ss.journey["rhythm_signals"]))


if __name__ == "__main__":
    unittest.main()
