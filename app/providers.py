from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import config

CONTINUE_PROMPT = (
    "이전 작업이 사용 제한으로 중단되었습니다. "
    "중단 지점부터 이어서 작업을 끝까지 완료해주세요."
)


@dataclass
class ParseResult:
    text: str
    session_id: str | None = None


def _default_resume_at(now=None):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=config.DEFAULT_RESUME_DELAY_MIN)


class ClaudeProvider:
    name = "claude"
    _limit_re = re.compile(r"usage limit reached|rate.?limit", re.I)
    _epoch_re = re.compile(r"limit reached\|(\d{9,})")

    def build_command(self, prompt, session_id=None):
        if session_id:
            return [
                "claude", "-p", "--output-format", "json",
                "--resume", session_id, CONTINUE_PROMPT,
            ]
        return ["claude", "-p", "--output-format", "json", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        try:
            data = json.loads(stdout)
            return ParseResult(
                text=data.get("result", stdout), session_id=data.get("session_id")
            )
        except (json.JSONDecodeError, TypeError):
            return ParseResult(text=stdout or stderr)

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        m = self._epoch_re.search(output)
        if m:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        return _default_resume_at(now)


class GeminiProvider:
    name = "gemini"
    _limit_re = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|quota", re.I)

    def build_command(self, prompt, session_id=None):
        if session_id:
            return ["gemini", "-p", CONTINUE_PROMPT, "--resume", session_id]
        return ["gemini", "-p", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr, session_id="latest")

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class GrokProvider:
    name = "grok"
    _limit_re = re.compile(r"rate.?limit|\b429\b|too many requests", re.I)

    def build_command(self, prompt, session_id=None):
        if session_id:
            return ["grok", "-c", "-p", CONTINUE_PROMPT]
        return ["grok", "-p", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr, session_id="latest")

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class HermesProvider:
    name = "hermes"

    def build_command(self, prompt, session_id=None):
        return ["hermes", "-z", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr)

    def detect_rate_limit(self, output, exit_code, now=None):
        return None  # 로컬 실행 — 사용 제한 없음


PROVIDERS = {
    p.name: p
    for p in [ClaudeProvider(), GeminiProvider(), GrokProvider(), HermesProvider()]
}

_GROK_KW = ["검색", "최신", "뉴스", "트렌드", "search", "news", "latest", "trend"]
_GEMINI_KW = ["문서", "요약", "pdf", "번역", "summar", "document", "translate"]
_HERMES_KW = ["로컬", "파일 정리", "개인", "local", "private"]


def route_auto(prompt):
    low = prompt.lower()
    for kw in _GROK_KW:
        if kw in low:
            return "grok"
    for kw in _GEMINI_KW:
        if kw in low:
            return "gemini"
    for kw in _HERMES_KW:
        if kw in low:
            return "hermes"
    return "claude"
