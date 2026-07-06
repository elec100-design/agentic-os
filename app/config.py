from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "aos.db"
UPLOAD_DIR = DATA_DIR / "uploads"
USAGE_CACHE_PATH = DATA_DIR / "usage_cache.json"
NOTE_STATE_PATH = DATA_DIR / "note_state.json"
WORKSPACES_PATH = DATA_DIR / "workspaces.json"   # 등록된 작업 위치 목록
WORKSPACES_DIR = DATA_DIR / "workspaces"         # GitHub 리포 클론 대상
GIT_CLONE_TIMEOUT_SEC = 180
BROWSE_ROOT = Path.home()                        # 폴더 탐색 팝업의 최상위(홈 이하만)

# CodexBar(`codexbar usage`)로 실측하는 프로바이더. 우리 이름 -> CodexBar id.
# hermes는 로컬 실행이라 사용 제한이 없어 제외.
CODEXBAR_PROVIDERS = {"claude": "claude", "gemini": "gemini", "grok": "grok"}
USAGE_REFRESH_SEC = 180          # 백그라운드로 사용량 갱신하는 주기
CODEXBAR_TIMEOUT_SEC = 60        # 프로바이더 1건 조회 타임아웃

# 프로바이더별 선택 가능한 모델. 첫 항목이 기본값(--model 플래그 없이 실행).
# label은 UI 표시, model은 CLI --model 인자(None이면 플래그 생략).
PROVIDER_MODELS = {
    "claude": [
        {"label": "Fable 5", "model": None, "default": True},
        {"label": "Opus 4.8", "model": "claude-opus-4-8"},
        {"label": "Sonnet 5", "model": "claude-sonnet-5"},
        {"label": "Haiku 4.5", "model": "claude-haiku-4-5-20251001"},
    ],
    "gemini": [
        {"label": "기본값", "model": None, "default": True},
        {"label": "2.5 Pro", "model": "gemini-2.5-pro"},
        {"label": "2.5 Flash", "model": "gemini-2.5-flash"},
    ],
    "grok": [
        {"label": "기본값", "model": None, "default": True},
        {"label": "Grok 4", "model": "grok-4"},
        {"label": "Grok Code Fast", "model": "grok-code-fast-1"},
    ],
    "hermes": [
        {"label": "기본값", "model": None, "default": True},
    ],
}

VAULT_PATH = Path(
    "/Users/macmini/Library/Mobile Documents/com~apple~CloudDocs/LLM WIKI/Blogging"
)
MEMORY_DIR = VAULT_PATH / "Agentic OS"

PORT = 8899
JOB_TIMEOUT_SEC = 30 * 60
DEFAULT_RESUME_DELAY_MIN = 60
MAX_ATTEMPTS = 10
WORKER_POLL_SEC = 5
