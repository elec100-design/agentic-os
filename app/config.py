import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file():
    """저장소 루트의 aos.env (KEY=VALUE, # 주석) 를 환경에 로드한다.
    실제 환경변수(launchd plist·export)가 이미 있으면 그것이 우선한다.
    경로는 AOS_ENV_FILE 로 바꿀 수 있다. 이 파일로 개인 설정(볼트 경로·
    tailnet origin 등)을 영구 저장해 두면 재설치·수동실행에도 유지된다."""
    path = Path(os.environ.get("AOS_ENV_FILE") or (BASE_DIR / "aos.env"))
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


_load_env_file()

DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "aos.db"
UPLOAD_DIR = DATA_DIR / "uploads"
USAGE_CACHE_PATH = DATA_DIR / "usage_cache.json"
MODELS_CACHE_PATH = DATA_DIR / "models_cache.json"
NOTE_STATE_PATH = DATA_DIR / "note_state.json"
WORKSPACES_PATH = DATA_DIR / "workspaces.json"   # 등록된 작업 위치 목록
SETTINGS_PATH = DATA_DIR / "settings.json"       # 셋업(활성 에이전트) 설정
WORKSPACES_DIR = DATA_DIR / "workspaces"         # GitHub 리포 클론 대상
GIT_CLONE_TIMEOUT_SEC = 180
BROWSE_ROOT = Path.home()                        # 폴더 탐색 팝업의 최상위(홈 이하만)
ICLOUD_DRIVE = (
    Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
)

# 탐색 UI에 표시할 폴더 이름 (macOS 내부 경로 → Finder식 라벨)
FOLDER_LABELS = {
    "com~apple~CloudDocs": "iCloud Drive",
}


def resolve_path(path):
    """expanduser + resolve. 실패 시 입력 Path 그대로."""
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except (OSError, ValueError):
        return p


def _path_variants(path):
    """macOS firmlink(/Users ↔ /System/Volumes/Data/Users) 양쪽 표현을 모두 허용."""
    resolved = resolve_path(path)
    variants = [resolved]
    text = str(resolved)
    if text.startswith("/Users/"):
        alt = Path("/System/Volumes/Data" + text)
        if alt.exists():
            variants.append(resolve_path(alt))
    elif text.startswith("/System/Volumes/Data/Users/"):
        alt = Path("/Users" + text[len("/System/Volumes/Data") :])
        if alt.exists():
            variants.append(resolve_path(alt))
    out = []
    seen = set()
    for v in variants:
        key = str(v)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


_CLOUD_KINDS = frozenset({"icloud", "cloud"})


def _cloud_storage_label(name):
    """CloudStorage 폴더명 → Finder식 라벨."""
    if name.startswith("iCloudDrive-"):
        suffix = name.removeprefix("iCloudDrive-")
        return f"iCloud Drive ({suffix})" if suffix else "iCloud Drive"
    if name.startswith("GoogleDrive-"):
        suffix = name.removeprefix("GoogleDrive-")
        return f"Google Drive ({suffix})" if suffix else "Google Drive"
    if name.startswith("OneDrive-"):
        suffix = name.removeprefix("OneDrive-")
        return f"OneDrive ({suffix})" if suffix else "OneDrive"
    if name == "Dropbox":
        return "Dropbox"
    return name


def cloud_roots():
    """탐색·등록에 허용할 클라우드 루트(iCloud·CloudStorage 마운트)."""
    seen = set()
    roots = []
    for shortcut in browse_shortcuts():
        if shortcut["kind"] not in _CLOUD_KINDS:
            continue
        for variant in _path_variants(shortcut["path"]):
            key = str(variant)
            if key in seen:
                continue
            seen.add(key)
            if variant.is_dir():
                roots.append(variant)
    return roots


def icloud_roots():
    """cloud_roots 별칭 (하위 호환)."""
    return cloud_roots()


def browse_roots():
    """폴더 탐색에서 접근 가능한 최상위 루트(홈 + 클라우드)."""
    seen = set()
    roots = []
    for variant in _path_variants(BROWSE_ROOT):
        key = str(variant)
        if key in seen:
            continue
        seen.add(key)
        roots.append(variant)
    for root in cloud_roots():
        key = str(root)
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def canonical_path(path):
    """경로 비교·저장용 표준 형태. macOS firmlink면 /Users/... 쪽을 우선."""
    variants = _path_variants(path)
    for variant in variants:
        if str(variant).startswith("/Users/"):
            return variant
    return variants[0]


def paths_equivalent(left, right):
    """macOS firmlink로 /Users ↔ /System/Volumes/Data 표현이 달라도 동일 경로로 본다."""
    left_keys = {str(p) for p in _path_variants(left)}
    return any(str(p) in left_keys for p in _path_variants(right))


def is_browse_allowed(path):
    """탐색 허용 경로: 홈 이하 또는 iCloud Drive(및 CloudStorage) 이하."""
    try:
        cur_variants = _path_variants(path)
    except (TypeError, ValueError):
        return False
    for cur in cur_variants:
        for root in browse_roots():
            for root_variant in _path_variants(root):
                if cur == root_variant or root_variant in cur.parents:
                    return True
    return False


def is_browse_root(path):
    """홈·iCloud 등 탐색 최상위 루트에 해당하는지."""
    cur_keys = {str(p) for p in _path_variants(path)}
    for root in browse_roots():
        for root_variant in _path_variants(root):
            if str(root_variant) in cur_keys:
                return True
    return False


def browse_parent(path):
    """허용 범위 안에서 한 단계 위. 탐색 루트면 None."""
    if is_browse_root(path):
        return None
    parent = canonical_path(path).parent
    return parent if is_browse_allowed(parent) else None


def browse_shortcuts():
    """폴더 탐색 루트에서 보여줄 바로가기 (iCloud·자주 쓰는 위치)."""
    home = Path.home()
    shortcuts = []
    icloud = ICLOUD_DRIVE
    if icloud.is_dir():
        shortcuts.append({
            "name": "iCloud Drive",
            "path": str(resolve_path(icloud)),
            "kind": "icloud",
        })
    # macOS 12+ CloudStorage 마운트 (일부 계정은 여기만 노출)
    cloud_storage = home / "Library/CloudStorage"
    seen = {s["path"] for s in shortcuts}
    if cloud_storage.is_dir():
        try:
            for entry in sorted(cloud_storage.iterdir(),
                                 key=lambda p: p.name.lower()):
                if not entry.is_dir():
                    continue
                resolved = str(canonical_path(entry))
                if any(paths_equivalent(resolved, existing) for existing in seen):
                    continue
                seen.add(resolved)
                kind = "icloud" if entry.name.startswith("iCloud") else "cloud"
                shortcuts.append({
                    "name": _cloud_storage_label(entry.name),
                    "path": resolved,
                    "kind": kind,
                })
        except OSError:
            pass
    shortcuts.append({
        "name": "홈",
        "path": str(resolve_path(home)),
        "kind": "home",
    })
    for name in ("Documents", "Desktop", "Downloads"):
        p = home / name
        if p.is_dir():
            shortcuts.append({
                "name": name,
                "path": str(resolve_path(p)),
                "kind": "home",
            })
    return shortcuts


def is_under_cloud_root(path):
    """경로가 iCloud·CloudStorage 루트 이하인지."""
    cur = canonical_path(path)
    for root in cloud_roots():
        for root_variant in _path_variants(root):
            if cur == root_variant or root_variant in cur.parents:
                return True
    return False


def should_skip_cloud_alias(entry, cur):
    """iCloud 루트의 Desktop/Documents 등 로컬 홈 미러 심볼릭 링크는 목록에서 제외."""
    if not is_under_cloud_root(cur):
        return False
    try:
        if not entry.is_symlink():
            return False
        target = entry.resolve()
        home = resolve_path(BROWSE_ROOT)
        if (home in target.parents or target == home) and not is_under_cloud_root(target):
            return True
    except OSError:
        pass
    return False


def list_browse_children(cur):
    """탐색 허용 폴더의 하위 디렉터리 목록."""
    rows = []
    try:
        entries = sorted(cur.iterdir(), key=lambda p: p.name.lower())
    except (FileNotFoundError, PermissionError):
        raise
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if should_skip_cloud_alias(entry, cur):
            continue
        rows.append({
            "name": entry.name,
            "label": folder_label(entry.name),
            "path": str(resolve_path(entry)),
        })
    return rows


def resolve_browse_path(path):
    """탐색 요청 경로를 허용 범위 안의 실제 디렉터리로 정규화."""
    root = resolve_path(BROWSE_ROOT)
    requested = (path or "").strip()
    if not requested:
        return root, None, None

    cur = resolve_path(requested)
    if not is_browse_allowed(cur):
        return root, None, None

    if cur.is_dir():
        return cur, None, None

    notice = (
        "폴더를 찾을 수 없거나 iCloud에서 아직 이 Mac으로 다운로드되지 않았습니다. "
        "Finder에서 해당 폴더를 연 뒤 다시 시도해 주세요."
    )
    candidate = canonical_path(cur)
    while not candidate.is_dir():
        parent = browse_parent(candidate)
        if not parent:
            return root, notice, str(canonical_path(cur))
        candidate = parent
    return candidate, notice, str(canonical_path(cur))


def show_browse_shortcuts(path):
    """홈·클라우드 루트에서 바로가기 패널을 보여준다."""
    for variant in _path_variants(path):
        if paths_equivalent(variant, BROWSE_ROOT):
            return True
    for root in cloud_roots():
        for variant in _path_variants(path):
            if paths_equivalent(variant, root):
                return True
    return False


def folder_label(name):
    return FOLDER_LABELS.get(name, name)

# CodexBar(`codexbar usage`)로 실측하는 프로바이더. 우리 이름 -> CodexBar id.
# hermes는 로컬 실행이라 사용 제한이 없어 제외.
# antigravity(agy)는 CodexBar 미지원이라 실측 불가 → 사용량 '정보 없음'으로 표시.
CODEXBAR_PROVIDERS = {"claude": "claude", "grok": "grok"}
USAGE_REFRESH_SEC = 180          # 백그라운드로 사용량 갱신하는 주기
CODEXBAR_TIMEOUT_SEC = 60        # 프로바이더 1건 조회 타임아웃

# 모델 목록은 app.models 가 CLI에서 동적으로 수집해 MODELS_CACHE_PATH 에 캐시한다.
# 아래는 조회 실패·최초 기동 시 폴백. claude는 패밀리 별칭을 써서 CLI가 항상 최신으로 해석.
MODELS_REFRESH_SEC = 6 * 3600       # 모델 목록 백그라운드 갱신 주기
MODELS_DISCOVER_TIMEOUT_SEC = 15    # 프로바이더 1건 CLI 조회 타임아웃

FALLBACK_PROVIDER_MODELS = {
    "claude": [
        {"label": "기본값", "model": None, "default": True},
        {"label": "Fable (최신)", "model": "fable"},
        {"label": "Opus (최신)", "model": "opus"},
        {"label": "Sonnet (최신)", "model": "sonnet"},
        {"label": "Haiku (최신)", "model": "haiku"},
    ],
    "antigravity": [
        {"label": "기본값", "model": None, "default": True},
    ],
    "grok": [
        {"label": "기본값", "model": None, "default": True},
    ],
    "hermes": [
        {"label": "기본값", "model": None, "default": True},
    ],
}

# 노트 저장 위치 — 환경변수로 개인 볼트 경로를 지정한다.
#   AOS_VAULT_PATH : Obsidian 볼트(또는 임의 폴더) 루트. 설정 시 노트는 그 아래
#                    "Agentic OS/" 하위 폴더에 저장되고, 컨텍스트 노트는 볼트 전체에서 읽힌다.
#   AOS_NOTES_DIR  : 노트 저장 폴더를 직접 지정(볼트 하위 규칙을 무시).
# 둘 다 없으면 저장소 안 data/notes/ 에 저장 → Obsidian 없이도 바로 동작한다.
_vault_env = os.environ.get("AOS_VAULT_PATH", "").strip()
_notes_env = os.environ.get("AOS_NOTES_DIR", "").strip()
if _notes_env:
    MEMORY_DIR = Path(_notes_env).expanduser()
elif _vault_env:
    MEMORY_DIR = Path(_vault_env).expanduser() / "Agentic OS"
else:
    MEMORY_DIR = DATA_DIR / "notes"
# 컨텍스트 노트를 읽을 수 있는 최상위 범위. 볼트를 지정했으면 볼트 전체, 아니면 노트 폴더.
VAULT_PATH = Path(_vault_env).expanduser() if _vault_env else MEMORY_DIR

PORT = int(os.environ.get("AOS_PORT", "8899"))
HOST = os.environ.get("AOS_HOST", "127.0.0.1")
MAX_UPLOAD_MB = int(os.environ.get("AOS_MAX_UPLOAD_MB", "25"))
JOB_TIMEOUT_SEC = 30 * 60
DEFAULT_RESUME_DELAY_MIN = 60
MAX_ATTEMPTS = 10
WORKER_POLL_SEC = 5
# 워커 전역 동시 실행 상한. 같은 provider(CLI)는 항상 직렬(사용량 레이스 방지),
# 서로 다른 provider끼리만 병렬로 돈다. 협의(council) 잡은 배타적으로 실행된다.
MAX_CONCURRENT_JOBS = int(os.environ.get("AOS_MAX_CONCURRENT_JOBS", "4"))
# SSE 출력 스트림 폴백 폴링 주기(초). 워커가 같은 프로세스면 stream_hub 신호로
# 즉시 깨어나므로 이 값은 워커가 별도 프로세스로 분리됐을 때의 상한 지연이다.
STREAM_POLL_SEC = 1.0

# 협의(Council) 모드 — 여러 에이전트가 제안·비평하고 한 에이전트가 종합한다.
#   AOS_COUNCIL_MEMBERS    : 참여 에이전트 목록(콤마 구분, 기본 4개 전체)
#   AOS_COUNCIL_AGGREGATOR : 종합자 고정(기본 빈 값 = 잔여 사용량 기반 자동)
#   AOS_COUNCIL_ROUNDS     : 2=제안+상호비평(기본), 1=제안만
#   AOS_COUNCIL_STAGE_TIMEOUT_SEC : 단계별 CLI 1회 실행 타임아웃(기본 600초)
COUNCIL_MEMBERS = [
    p.strip()
    for p in os.environ.get(
        "AOS_COUNCIL_MEMBERS", "claude,antigravity,grok,hermes"
    ).split(",")
    if p.strip()
]
COUNCIL_AGGREGATOR = os.environ.get("AOS_COUNCIL_AGGREGATOR", "").strip()
COUNCIL_ROUNDS = int(os.environ.get("AOS_COUNCIL_ROUNDS", "2"))
COUNCIL_STAGE_TIMEOUT_SEC = int(
    os.environ.get("AOS_COUNCIL_STAGE_TIMEOUT_SEC", "600"))
COUNCIL_MIN_MEMBERS = 2          # 이 인원 미만이면 협의 불가
COUNCIL_ANSWER_MAX_CHARS = 8000  # 비평·종합 프롬프트에 넣는 답변당 최대 길이

# 비전 보드(오케스트레이션) — 메인 에이전트가 목표를 태스크로 분해하고,
# 오케스트레이터 루프가 의존성이 풀린 태스크를 잡으로 디스패치한다.
ARTIFACTS_DIR = DATA_DIR / "artifacts"   # 미디어 산출물 저장 위치
ORCH_POLL_SEC = float(os.environ.get("AOS_ORCH_POLL_SEC", "3"))
ORCH_MAX_TASKS = int(os.environ.get("AOS_ORCH_MAX_TASKS", "10"))
ORCH_MAX_INFLIGHT = int(os.environ.get("AOS_ORCH_MAX_INFLIGHT", "3"))
ORCH_UPSTREAM_CLIP_CHARS = 6000  # 하류 프롬프트에 넣는 상류 출력당 최대 길이
MEDIA_TIMEOUT_SEC = int(os.environ.get("AOS_MEDIA_TIMEOUT_SEC", "300"))
# API 폴백용 미디어 모델 (모델명이 자주 바뀌므로 env로 교체 가능하게)
MEDIA_TTS_MODEL = os.environ.get("AOS_MEDIA_TTS_MODEL",
                                 "gemini-2.5-flash-preview-tts")
