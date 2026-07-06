from app import codexbar

# `codexbar usage --provider all --format json`에서 실제로 캡처한 형태
CLAUDE_ITEM = {
    "provider": "claude",
    "source": "web",
    "usage": {
        "primary": {"usedPercent": 81, "resetsAt": "2026-07-05T20:29:59Z",
                    "windowMinutes": 300, "resetDescription": "Jul 6 at 5:29AM"},
        "secondary": {"usedPercent": 80, "resetsAt": "2026-07-05T19:59:59Z",
                      "windowMinutes": 10080},
        "tertiary": None,
        "extraRateWindows": [
            {"id": "claude-routines", "title": "Daily Routines",
             "window": {"usedPercent": 0, "windowMinutes": 10080}},
            {"id": "claude-weekly-scoped-fable", "title": "Fable only",
             "window": {"usedPercent": 100, "resetsAt": "2026-07-05T19:59:59Z",
                        "windowMinutes": 10080}},
        ],
    },
}
GROK_ITEM = {
    "provider": "grok", "source": "grok-web",
    "usage": {"primary": {"usedPercent": 24, "resetsAt": "2026-07-08T21:48:38Z"}},
}
GROK_ERR = {
    "provider": "grok", "source": "auto",
    "error": {"message": "auth not supported.", "kind": "provider"},
}


def test_normalize_claude_uses_account_windows_not_model_scoped():
    out = codexbar.normalize([CLAUDE_ITEM])
    c = out["claude"]
    # 계정 창 max(81, 80) = 81 → Fable-only 100% 는 가용성에 반영 안 됨
    assert c["used"] == 81
    assert c["remaining"] == 19
    assert c["available"] is True
    assert c["resetsAt"] == "2026-07-05T20:29:59Z"
    # 표시용 windows 에는 extra 도 포함
    titles = [w["title"] for w in c["windows"]]
    assert "Fable only" in titles


def test_normalize_grok():
    out = codexbar.normalize([GROK_ITEM])
    assert out["grok"]["used"] == 24
    assert out["grok"]["remaining"] == 76
    assert out["grok"]["available"] is True


def test_normalize_error_provider():
    out = codexbar.normalize([GROK_ERR])
    g = out["grok"]
    assert g["used"] is None
    assert g["available"] is None
    assert "not supported" in g["error"]


def test_normalize_ignores_unknown_providers():
    out = codexbar.normalize([{"provider": "cursor", "usage": {}}])
    assert out == {}


def test_normalize_exhausted_when_account_window_full():
    item = {"provider": "grok", "source": "grok-web",
            "usage": {"primary": {"usedPercent": 100, "resetsAt": "2026-07-08T00:00:00Z"}}}
    out = codexbar.normalize([item])
    assert out["grok"]["available"] is False
    assert out["grok"]["remaining"] == 0


def test_cache_roundtrip(tmp_env):
    codexbar.write_cache({"claude": {"used": 50}})
    cache = codexbar.read_cache()
    assert cache["providers"]["claude"]["used"] == 50
    assert cache["updatedAt"] is not None


def test_read_cache_missing_returns_empty(tmp_env):
    assert codexbar.read_cache() == {"updatedAt": None, "providers": {}}


async def test_refresh_loop_writes_cache(tmp_env):
    import asyncio

    async def fake_fetch():
        return {"claude": {"used": 42, "available": True}}

    stop = asyncio.Event()
    task = asyncio.create_task(
        codexbar.refresh_loop(stop, interval=999, fetcher=fake_fetch)
    )
    for _ in range(50):
        await asyncio.sleep(0.02)
        if codexbar.read_cache()["providers"]:
            break
    stop.set()
    await task
    assert codexbar.read_cache()["providers"]["claude"]["used"] == 42
