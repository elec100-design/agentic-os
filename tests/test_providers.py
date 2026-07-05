from datetime import datetime, timedelta, timezone

from app.providers import (
    CONTINUE_PROMPT,
    PROVIDERS,
    ClaudeProvider,
    GeminiProvider,
    GrokProvider,
    HermesProvider,
    route_auto,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


# --- Claude ---

def test_claude_build_new():
    cmd = ClaudeProvider().build_command("안녕")
    assert cmd == ["claude", "-p", "--output-format", "json", "안녕"]


def test_claude_build_resume():
    cmd = ClaudeProvider().build_command("안녕", session_id="abc-123")
    assert "--resume" in cmd and "abc-123" in cmd
    assert cmd[-1] == CONTINUE_PROMPT


def test_claude_parse_json_output():
    stdout = '{"result": "답변입니다", "session_id": "sess-9"}'
    r = ClaudeProvider().parse_output(stdout, "", 0)
    assert r.text == "답변입니다"
    assert r.session_id == "sess-9"


def test_claude_parse_non_json_falls_back():
    r = ClaudeProvider().parse_output("plain text", "", 0)
    assert r.text == "plain text"
    assert r.session_id is None


def test_claude_parse_non_dict_json_falls_back():
    r = ClaudeProvider().parse_output('["a"]', "", 0)
    assert r.text == '["a"]'
    assert r.session_id is None
    r2 = ClaudeProvider().parse_output("null", "", 0)
    assert r2.text == "null"
    assert r2.session_id is None


def test_claude_rate_limit_with_epoch():
    out = "Claude AI usage limit reached|1751719800"
    ra = ClaudeProvider().detect_rate_limit(out, 1, now=NOW)
    assert ra == datetime.fromtimestamp(1751719800, tz=timezone.utc)


def test_claude_rate_limit_without_epoch_uses_default():
    ra = ClaudeProvider().detect_rate_limit("usage limit reached", 1, now=NOW)
    assert ra == NOW + timedelta(minutes=60)


def test_claude_no_limit_on_success_exit():
    assert ClaudeProvider().detect_rate_limit("usage limit reached", 0, now=NOW) is None


def test_claude_no_limit_on_normal_error():
    assert ClaudeProvider().detect_rate_limit("some other error", 1, now=NOW) is None


# --- Gemini ---

def test_gemini_build_and_resume():
    p = GeminiProvider()
    assert p.build_command("hi") == ["gemini", "-p", "hi"]
    resumed = p.build_command("hi", session_id="latest")
    assert "--resume" in resumed and "latest" in resumed


def test_gemini_rate_limit():
    p = GeminiProvider()
    assert p.detect_rate_limit("Error 429: RESOURCE_EXHAUSTED", 1, now=NOW) == NOW + timedelta(minutes=60)
    assert p.detect_rate_limit("fine", 1, now=NOW) is None


def test_gemini_parse_records_session():
    assert GeminiProvider().parse_output("out", "", 0).session_id == "latest"


# --- Grok ---

def test_grok_build_and_resume():
    p = GrokProvider()
    assert p.build_command("hi") == ["grok", "-p", "hi"]
    resumed = p.build_command("hi", session_id="latest")
    assert resumed[:2] == ["grok", "-c"]
    assert CONTINUE_PROMPT in resumed


def test_grok_rate_limit():
    p = GrokProvider()
    assert p.detect_rate_limit("Too Many Requests", 1, now=NOW) is not None


# --- Hermes ---

def test_hermes_build_and_never_limits():
    p = HermesProvider()
    assert p.build_command("hi") == ["hermes", "-z", "hi"]
    assert p.detect_rate_limit("rate limit", 1, now=NOW) is None


# --- Registry & routing ---

def test_registry_has_all_four():
    assert set(PROVIDERS) == {"claude", "gemini", "grok", "hermes"}


def test_route_auto():
    assert route_auto("최신 뉴스 검색해줘") == "grok"
    assert route_auto("이 PDF 문서 요약해줘") == "gemini"
    assert route_auto("로컬 파일 정리해줘") == "hermes"
    assert route_auto("버그 수정해줘") == "claude"
