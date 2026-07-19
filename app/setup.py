"""첫 실행 셋업 — CLI 설치 감지와 provider별 안내 메타데이터.

감지는 `shutil.which`로 binary 존재만 확인한다(CLI를 실행하지 않음 —
로그인 프롬프트·사용량 소모·행(hang) 방지, /setup의 재확인 버튼이 수시로
호출해도 안전). 로그인 여부는 CLI가 상태 명령을 제공하지 않아 확인할 수
없으므로, 각 CLI의 로그인 절차를 안내만 한다.
"""
from __future__ import annotations

import shutil

# UI 노출 순서 = providers.PROVIDERS 정식 순서와 동일
CLI_META = {
    "claude": {
        "binary": "claude",
        "label": "Claude Code",
        "vendor": "Anthropic",
        "desc": "코딩·분석·글쓰기에 강한 범용 에이전트",
        "auth_cmd": "claude",
        "auth_hint": ("터미널에서 claude 를 한 번 실행하면 브라우저 로그인"
                      "(구독 계정 OAuth)이 열립니다. 로그인 후 재확인을 누르세요."),
        "install_hint": "npm install -g @anthropic-ai/claude-code",
    },
    "antigravity": {
        "binary": "agy",
        "label": "Antigravity",
        "vendor": "Google",
        "desc": "Gemini 기반 에이전트 — Google 계정으로 로그인",
        "auth_cmd": "agy",
        "auth_hint": ("agy 첫 실행 시 Google 계정 OAuth 로그인이 진행되고 "
                      "시스템 키체인에 저장됩니다."),
        "install_hint": "https://antigravity.google 설치 안내 참고",
    },
    "grok": {
        "binary": "grok",
        "label": "Grok",
        "vendor": "xAI",
        "desc": "xAI Grok — SuperGrok 구독 CLI",
        "auth_cmd": "grok",
        "auth_hint": "grok CLI 자체 로그인 절차(브라우저 인증)를 완료하세요.",
        "install_hint": "npm install -g @vibe-kit/grok-cli",
    },
    "hermes": {
        "binary": "hermes",
        "label": "Hermes",
        "vendor": "로컬",
        "desc": "로컬 실행 모델 — 무제한·개인 데이터에 적합",
        "auth_cmd": None,
        "auth_hint": "로컬 실행 — 로그인 불필요, 사용량 무제한입니다.",
        "install_hint": "hermes CLI 설치 안내 참고",
    },
}

OPTIONAL_TOOLS = {
    "codexbar": "사용량 실측 표시 (claude·grok 잔여 사용량 기반 자동 라우팅)",
    "gh": "GitHub 리포를 작업 위치로 연동",
    "rg": "노트(메모리) 전문 검색",
}


def detect(which=shutil.which):
    """provider·보조 도구별 설치 상태. which는 테스트에서 주입 가능."""
    providers = {}
    for name, meta in CLI_META.items():
        path = which(meta["binary"])
        providers[name] = {
            "installed": bool(path),
            "path": path,
            "label": meta["label"],
            "vendor": meta["vendor"],
            "desc": meta["desc"],
            "authCmd": meta["auth_cmd"],
            "authHint": meta["auth_hint"],
            "installHint": meta["install_hint"],
        }
    tools = {}
    for name, desc in OPTIONAL_TOOLS.items():
        tools[name] = {"installed": bool(which(name)), "desc": desc}
    return {"providers": providers, "tools": tools}
