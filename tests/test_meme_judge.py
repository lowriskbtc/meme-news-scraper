import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked in tests"))

import meme_judge as mj  # noqa: E402


# ---------------- deterministic suppression ----------------

@pytest.mark.parametrize("headline", [
    "Colombia declares economic emergency after deadly earthquake",
    "Two killed as train derailed outside city",
    "Mass shooting at mall leaves community in shock",
    "Hiker hospitalized after fall",
    "Plane crash near airport under investigation",
    "Man assaulted two officers at festival",
])
def test_real_harm_is_always_suppressed(headline):
    assert mj.suppressed(headline)


@pytest.mark.parametrize("headline", [
    "Town declares war on aggressive turkey",
    "Photographer shooting wildlife documentary finds raccoon heist",
    "Drought of common sense as council votes on goose parking",
    "Emu escapes farm, spotted riding escalator at shopping mall",
    "Stock prices crash after CEO's cat walks across keyboard",
])
def test_idioms_are_not_suppressed(headline):
    """Regression: bare disaster words once killed exactly the stories this feed wants."""
    assert mj.suppressed(headline) is None


# ---------------- model calls (mocked) ----------------

def fake_post(response_text, calls):
    def post(url, json=None, timeout=None):
        calls.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"response": response_text})
    return post


def test_judge_one_parses_and_clamps(monkeypatch):
    calls = []
    monkeypatch.setattr(mj.requests, "post", fake_post('{"score": 14, "why": "goose on the loose"}', calls))
    assert mj.judge_one("Goose elected class president") == (10.0, "goose on the loose")
    assert calls[0]["format"] == "json" and calls[0]["options"]["temperature"] == 0.0


def test_judge_one_suppresses_without_calling_the_model(monkeypatch):
    calls = []
    monkeypatch.setattr(mj.requests, "post", fake_post('{"score": 9}', calls))
    score, why = mj.judge_one("Village celebrates after deadly earthquake")
    assert score == 0.0 and why.startswith("suppressed") and calls == []


@pytest.mark.parametrize("raw", ["not json", '{"score": "high"}'])
def test_judge_one_returns_none_on_bad_model_output(monkeypatch, raw):
    monkeypatch.setattr(mj.requests, "post", fake_post(raw, []))
    assert mj.judge_one("Cat becomes mayor") is None


def test_judge_disabled(monkeypatch):
    monkeypatch.setattr(mj, "JUDGE_ENABLED", False)
    assert mj.judge_one("anything") is None and mj.judge_items([{"id": "1"}]) == 0


def test_judge_items_uses_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(mj, "CACHE_FILE", tmp_path / "data" / "judgments.json")
    calls = []
    monkeypatch.setattr(mj.requests, "post", fake_post('{"score": 7, "why": "odd"}', calls))
    items = [{"id": "a", "title": "Llama attends graduation"}]
    assert mj.judge_items(items) == 1 and items[0]["judge_score"] == 7.0
    again = [{"id": "a", "title": "Llama attends graduation"}]
    assert mj.judge_items(again) == 0 and again[0]["judge_score"] == 7.0
    assert len(calls) == 1
    assert json.loads((tmp_path / "data" / "judgments.json").read_text())["judgments"]["a"]["score"] == 7.0


def test_available_matches_model_by_bare_name(monkeypatch):
    resp = SimpleNamespace(status_code=200, json=lambda: {"models": [{"name": "llama3.2:3b-instruct"}]})
    monkeypatch.setattr(mj.requests, "get", lambda *a, **k: resp)
    monkeypatch.setattr(mj, "JUDGE_MODEL", "llama3.2:3b")
    assert mj.available() is True
    monkeypatch.setattr(mj.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert mj.available() is False
