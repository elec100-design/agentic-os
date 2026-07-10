import asyncio
import json

from app import config, models


def test_parse_grok_models_extracts_default_and_ids():
    text = """You are logged in with grok.com.

Default model: grok-4.5

Available models:
  * grok-4.5 (default)
  - grok-composer-2.5-fast
"""
    entries = models.parse_grok_models(text)
    assert entries[0]["model"] is None and entries[0]["default"] is True
    ids = [e["model"] for e in entries if e["model"]]
    assert ids == ["grok-4.5", "grok-composer-2.5-fast"]


def test_parse_grok_models_empty():
    assert models.parse_grok_models("") == []
    assert models.parse_grok_models("no models here") == []


def test_parse_agy_models_uses_display_names():
    text = """Gemini 3.5 Flash (Medium)
Gemini 3.1 Pro (High)
Claude Sonnet 4.6 (Thinking)
"""
    entries = models.parse_agy_models(text)
    assert entries[0]["model"] is None
    assert entries[1] == {
        "label": "Gemini 3.5 Flash (Medium)",
        "model": "Gemini 3.5 Flash (Medium)",
        "default": False,
    }
    assert any(e["model"] == "Claude Sonnet 4.6 (Thinking)" for e in entries)


def test_parse_agy_models_empty():
    assert models.parse_agy_models("") == []
    assert models.parse_agy_models("Usage: agy models") == []


def test_claude_models_from_aliases_use_family_ids():
    entries = models.claude_models_from_aliases({
        "opus": "claude-opus-4-8",
        "sonnet": "claude-sonnet-5",
        "haiku": "claude-haiku-4-5-20251001",
        "fable": "claude-fable-5",
    })
    by_model = {e["model"]: e for e in entries}
    assert by_model[None]["default"] is True
    assert by_model["opus"]["model"] == "opus"
    assert "claude-opus-4-8" in by_model["opus"]["label"]
    assert by_model["fable"]["model"] == "fable"


def test_parse_claude_alias_map_from_minified_blob():
    blob = (
        b'aliases:{opus:{default:"claude-opus-4-8",per_},"claude-sonnet-5",'
        b'haiku:"claude-haiku-4-5",fable:{default:"claude-fable-5"}}'
    )
    m = models.parse_claude_alias_map(blob)
    assert m.get("opus") == "claude-opus-4-8"
    assert m.get("haiku") == "claude-haiku-4-5"
    assert m.get("fable") == "claude-fable-5"


def test_parse_hermes_status_label():
    text = "Model:        tencent/hy3:free\nProvider:     Nous Portal\n"
    entries = models.parse_hermes_status(text)
    assert len(entries) == 1
    assert entries[0]["model"] is None
    assert "tencent/hy3:free" in entries[0]["label"]


def test_get_provider_models_falls_back_when_cache_empty(tmp_env):
    out = models.get_provider_models()
    assert "claude" in out
    assert any(e.get("model") == "opus" for e in out["claude"])
    assert out["hermes"][0]["model"] is None


def test_get_provider_models_reads_cache(tmp_env):
    models.write_cache({
        "claude": [
            {"label": "기본값", "model": None, "default": True},
            {"label": "Opus", "model": "opus"},
        ],
        "grok": [
            {"label": "기본값", "model": None, "default": True},
            {"label": "Grok 4.5", "model": "grok-4.5"},
        ],
    })
    out = models.get_provider_models()
    assert any(e.get("model") == "grok-4.5" for e in out["grok"])
    # antigravity 캐시 없으면 폴백
    assert out["antigravity"][0]["model"] is None


def test_is_valid_model(tmp_env):
    assert models.is_valid_model("claude", "") is True
    assert models.is_valid_model("claude", "opus") is True
    assert models.is_valid_model("claude", "not-a-real-model") is False


def test_write_and_read_cache_roundtrip(tmp_env):
    providers = {"claude": [{"label": "기본값", "model": None, "default": True}]}
    models.write_cache(providers)
    cache = models.read_cache()
    assert cache["source"] == "cache"
    assert cache["updatedAt"]
    assert cache["providers"]["claude"][0]["default"] is True
    path = config.MODELS_CACHE_PATH
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "providers" in raw


def test_refresh_loop_writes_cache(tmp_env):
    stop = asyncio.Event()

    async def fake_fetch():
        stop.set()
        return {
            "claude": [{"label": "기본값", "model": None, "default": True}],
            "grok": [
                {"label": "기본값", "model": None, "default": True},
                {"label": "G", "model": "grok-x"},
            ],
        }

    async def run():
        await models.refresh_loop(stop, interval=0.01, fetcher=fake_fetch)

    asyncio.run(run())
    out = models.get_provider_models()
    assert any(e.get("model") == "grok-x" for e in out["grok"])
