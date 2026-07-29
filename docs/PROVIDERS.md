# 새 에이전트(Provider) 추가하기

Agentic OS는 각 AI CLI를 **어댑터**로 감싼다. 새 구독 CLI(예: Codex, Aider,
Cursor CLI 등)를 붙이려면 아래 계약을 구현한 provider 클래스를 하나 만들고
등록하면 된다. 프론트엔드·라우팅·큐·노트는 그대로 재사용된다.

## 1. Provider 클래스 (`app/providers.py`)

세 가지 메서드를 구현한다:

```python
class MyProvider:
    name = "myagent"                     # 내부 식별자(소문자, 고유)

    def build_command(self, prompt, session_id=None, model=None):
        """CLI를 헤드리스로 1회 실행하는 argv 리스트를 만든다.
        API 키가 아니라 구독 로그인 세션을 쓰도록 해야 한다(추가 과금 방지)."""
        cmd = ["myagent", "-p", prompt]
        if model:
            cmd += ["--model", model]
        if session_id:
            cmd += ["--resume", session_id]   # 지원 시. 없으면 생략
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        """CLI 출력에서 최종 텍스트와 (가능하면) 세션 id를 뽑는다."""
        return ParseResult(text=stdout or stderr, session_id=None)

    def detect_rate_limit(self, output, exit_code, now=None):
        """사용 제한이면 재개할 시각(datetime)을, 아니면 None을 반환.
        큐가 이 시각까지 기다렸다가 자동 재개한다."""
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)     # 기본 60분 뒤
```

- `ParseResult`, `_default_resume_at`는 `app/providers.py`에 이미 있다.
- 세션 재개(`--resume`)를 지원하지 않으면 `session_id`를 무시하면 된다
  (그 CLI는 매 호출 stateless로 동작).
- 사용 제한 정규식은 그 CLI가 실제로 내는 메시지(예: `429`,
  `rate limit`, `quota`)에 맞춘다. claude처럼 리셋 epoch를 파싱할 수
  있으면 `detect_rate_limit`에서 그 시각을 돌려주면 더 정확하다.

## 2. 등록 (`app/providers.py`)

`PROVIDERS` dict에 인스턴스를 더한다:

```python
PROVIDERS = {
    p.name: p
    for p in [ClaudeProvider(), CodexProvider(), AntigravityProvider(),
              GeminiProvider(), GrokProvider(), OpenClawProvider(),
              HermesProvider(), MyProvider()]
}
```

자동 라우팅은 등록된 모든 비로컬 provider를 자동으로 후보에 포함한다. 작업
난이도·성격을 세밀하게 지정하려면 `AGENT_PROFILES`에 `name`을 추가한다.
프로필이 없어도 기본 `complex` 후보로 동작한다. 로컬·무제한 CLI는 Hermes처럼
이름을 제외하거나 라우팅 프로필을 별도로 설계한다.

## 3. 셋업 감지 (`app/setup.py`)

`CLI_META`에 항목을 더하면 `/setup` 위저드와 `aos doctor`가 설치 여부를
감지하고 로그인 안내를 보여준다:

```python
"myagent": {
    "binary": "myagent",              # PATH에서 찾을 실행파일 이름
    "label": "My Agent",
    "vendor": "…",
    "desc": "한 줄 설명",
    "auth_cmd": "myagent login",      # 로그인 명령(없으면 None)
    "auth_hint": "로그인 방법 안내…",
    "install_hint": "설치 명령/링크",
},
```

## 4. (선택) 모델 목록

`app/models.py`의 `_DISCOVERERS`에 디스커버 함수를 등록한다. CLI에서 목록을
못 가져오면 `config.FALLBACK_PROVIDER_MODELS`에 선택지를 넣어 두면 UI 모델
칩이 바로 노출된다(항목이 1개뿐이면 칩이 숨겨짐).

현재 디스커버 경로:
- claude: 바이너리 별칭 스캔
- codex: `codex debug models`
- gemini: 별칭(auto/pro/flash) + 알려진 full id
- antigravity: `agy models`
- grok: `grok models`
- openclaw: `openclaw models list --json`
- hermes: `hermes status` (라벨만)

## 5. (선택) 사용량 실측

잔여 사용량을 [CodexBar](https://github.com/steipete/CodexBar)로 읽을 수
있으면 `config.CODEXBAR_PROVIDERS`에 `{"myagent": "codexbar-id"}`를 더한다.
없으면 사용량은 "정보 없음"으로 표시되고, 자동 라우팅에서는 기본 순위값
(`_UNKNOWN_REMAINING`)으로 취급된다.

## 6. 테스트

`tests/test_providers.py`의 기존 패턴을 따라 `build_command`/`parse_output`/
`detect_rate_limit`를 검증한다. 워커는 API 키 환경변수를 제거하고 CLI를
spawn하므로(`app/worker.py` `_clean_env`), 실제 호출 없이 argv·파싱만
단위 테스트하면 된다.

## 설계 원칙 (지킬 것)

- **API 키 미사용** — 구독 CLI의 헤드리스 로그인 세션만 쓴다(추가 과금 없음).
- **stateless 호출** — 각 실행은 독립적. 세션 재개는 CLI가 지원할 때만.
- **빌드 도구 없음** — 프론트엔드는 손대지 않아도 새 provider가 칩·라우팅에
  자동 반영된다(`/setup`에서 활성화 시).

## 부록: 미디어 생성 능력 스파이크 (2026-07-25, vision board Phase 0)

CLI 구독 로그인만으로 헤드리스 미디어 생성이 가능한지 실측한 결과.

| 능력 | agy (Antigravity) | grok | 비고 |
|---|---|---|---|
| 이미지 | ✅ `generate_image` (실측: 1024×1024 PNG 생성 확인) | ✅ `image_gen`, `image_edit` (실측: PNG 생성 확인) | 둘 다 CLI 경로 사용 가능 |
| 비디오 | ❌ 없음 | 🔶 `image_to_video`, `reference_to_video` (도구 존재 확인, 실생성은 미검증) | grok CLI 경로 |
| 오디오/음성 | ❌ 없음 | ❌ 없음 | API 폴백 필요 (`GEMINI_API_KEY` TTS) |

실측 시 주의점:
- `agy`는 `--dangerously-skip-permissions` 필요, 산출물을 자기 스크래치 디렉토리
  (`~/.gemini/antigravity-cli/scratch/`)에 저장할 수 있음 → 프롬프트로 절대 경로를
  마지막 줄에 출력하게 하고, 그 경로에서 artifacts 디렉토리로 복사한다.
- `grok`은 `--always-approve` 필요, 요청한 cwd에 저장함(동일하게 경로 출력을 계약으로).
- 모델 목록(`agy models`/`grok models`)에는 미디어 모델이 노출되지 않는다 —
  미디어는 에이전트의 내부 도구를 통해서만 접근 가능.
