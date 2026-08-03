"""아주 가벼운 UI 국제화(i18n).

한국어 원문을 그대로 키로 쓰고 영어 번역만 매핑한다. 장점:
- 별도 키를 발명하지 않아도 되고, 번역이 없으면 원문(한국어)으로 폴백해
  화면이 깨지지 않는다.
- 기본 언어는 영어("영문 기본")이며, 쿠키(aos-lang) 또는 Accept-Language로
  한국어를 선택할 수 있다.

요청별 현재 언어는 contextvar로 들고 다녀(동시 요청 안전) 템플릿 필터
`t`와 JS 주입 카탈로그가 같은 값을 참조한다.
"""
from __future__ import annotations

from contextvars import ContextVar

LANGS = ("en", "ko")
DEFAULT_LANG = "en"

_current = ContextVar("aos_lang", default=DEFAULT_LANG)


def set_lang(lang):
    _current.set(lang if lang in LANGS else DEFAULT_LANG)


def get_lang():
    return _current.get()


def resolve_lang(cookie=None, accept_language=None):
    """쿠키 > Accept-Language > 기본(영어) 순으로 언어를 정한다."""
    if cookie in LANGS:
        return cookie
    al = (accept_language or "").lower()
    # ko, ko-KR 등이 en보다 앞에 오면 한국어로 본다(대략적 판정)
    if "ko" in al:
        ko_at = al.find("ko")
        en_at = al.find("en")
        if en_at == -1 or ko_at < en_at:
            return "ko"
    return DEFAULT_LANG


def t(text):
    """현재 언어로 번역. 영어면 카탈로그에서 찾고, 없으면 원문 그대로."""
    if get_lang() == "en":
        return EN.get(text, text)
    return text


# 한국어 원문 → 영어. 없는 항목은 원문으로 폴백된다.
EN = {
    # --- 공통 / 대시보드 ---
    "무엇을 도와드릴까요?": "What can I help with?",
    "작업을 설명해 주세요… 파일을 끌어다 놓을 수도 있어요":
        "Describe a task… you can also drag files in",
    "자동": "Auto",
    "모델": "Model",
    "협의": "Council",
    "메모리": "Memory",
    "노트 검색…": "Search notes…",
    # --- 홈 채팅 레일 (검색·정렬·이름 변경) ---
    "채팅 검색…": "Search chats…",
    "채팅 검색": "Search chats",
    "채팅 정렬": "Sort chats",
    "시간순": "Recent",
    "프로젝트별": "By project",
    "작업 위치 없음": "No workspace",
    "저장된 노트가 없습니다": "No saved notes yet",
    "검색 결과가 없습니다": "No results",
    "고정됨": "Pinned",
    "기타": "Other",
    "사용량": "Usage",
    "작업 큐": "Job queue",
    "아직 작업이 없습니다. 위에서 첫 작업을 보내 보세요.":
        "No jobs yet. Send your first one above.",
    "에이전트 설정": "Agent settings",
    "⚙︎ 에이전트 설정": "⚙︎ Agent settings",
    "대시보드": "Dashboard",
    "← 대시보드": "← Dashboard",
    # --- 작업 큐 테이블 ---
    "프롬프트": "Prompt",
    "에이전트": "Agent",
    "상태": "Status",
    "취소": "Cancel",
    "삭제": "Delete",
    "이 작업과 연결된 메모리 노트를 삭제할까요?":
        "Delete the memory note linked to this job?",
    # --- 사용량 패널 ---
    "로컬 · 무제한": "Local · unlimited",
    "정보 없음": "No data",
    "CodexBar 연동 필요": "Needs CodexBar",
    "갱신 대기": "Awaiting update",
    "방금": "just now",
    "곧": "soon",
    "남음": "left",
    "후 리셋": "until reset",
    "최근 24시간": "last 24h",
    "회 실행": "runs",
    "재개": "Resume",
    "노트 메뉴": "Note menu",
    # --- 작업 상세 ---
    "출력": "Output",
    "실행 과정": "Run steps",
    "변경된 파일": "Changed files",
    "적용된 지침": "Instructions applied",
    "🔌 MCP 서버": "🔌 MCP servers",
    "워크스페이스별 MCP 서버 관리": "Manage MCP servers per workspace",
    "서버": "servers",
    "워크스페이스별로 claude 에만 연결됩니다 — 그 실행 한 번에만 적용되는 --mcp-config 를 지원하는 유일한 에이전트입니다. codex·gemini 는 전역 설정 파일에만 쓸 수 있어 워크스페이스 단위로 관리할 수 없습니다.":
        "These connect to claude only, per workspace — it's the only agent that "
        "supports a --mcp-config scoped to a single run. codex and gemini can only "
        "write to a global config file, so they can't be managed per workspace.",
    "등록된 작업 위치가 없습니다. 먼저 컴포저의 ＋ 에서 폴더나 GitHub 리포를 추가하세요.":
        "No workspaces registered yet. Add a folder or GitHub repo from the composer's ＋ first.",
    "등록된 서버": "Registered servers",
    "서버 추가": "Add a server",
    "서버 이름": "Server name",
    "실행 명령": "Command",
    "인자 (공백으로 구분)": "Arguments (space-separated)",
    "환경변수 (한 줄에 KEY=VALUE 하나씩)": "Environment variables (one KEY=VALUE per line)",
    "Claude에 설치된 MCP 서버": "MCP servers installed in Claude",
    "선택하세요": "Select one",
    "이 작업 위치에 새로 연결할 수 있는 설치된 MCP 서버가 없습니다. 터미널에서 claude mcp add 로 먼저 설치하세요.":
        "No installed MCP servers available to connect for this workspace. "
        "Install one first with claude mcp add in your terminal.",
    "직접 입력 (고급)": "Manual entry (advanced)",
    "URL": "URL",
    "명령": "Command",
    "이 에이전트가 스스로 읽는 파일은 중복으로 붙이지 않습니다":
        "Files this agent already reads on its own aren't duplicated here",
    "변경 되돌리기": "Revert changes",
    "되돌렸습니다": "Reverted",
    "이 작업이 만든, 아직 커밋되지 않은 변경을 실행 전 상태로 되돌릴까요? 그 사이 다른 작업이 같은 파일을 더 건드렸다면 그것도 함께 되돌아갑니다.":
        "Revert this job's uncommitted changes to how they were before it ran? "
        "If another job touched the same files since, those changes revert too.",
    "작업 위치": "Workspace",
    "자동 라우팅": "Auto-routed",
    # --- 노트 / 대화 ---
    "대화": "Conversation",
    "노트": "Note",
    "나": "You",
    "이 세션 이어서 진행": "Continue this session",
    "이어서 무엇을 할까요?": "What next?",
    "이 노트를 참고해 새 작업": "New task using this note",
    "이 노트를 컨텍스트로 무엇을 할까요?": "What should I do with this note as context?",
    "노트 내용이 프롬프트 앞에 첨부됩니다": "The note is prepended to your prompt",
    "기본값": "default",
    # --- 도구 / 컴포저 ---
    "파일 첨부…": "Attach files…",
    "메모리 첨부": "Attach memory",
    "타임아웃": "Timeout",
    "분": "min",
    "첨부·도구": "Attach / tools",
    "전송": "Send",
    "사이드바 열기/닫기": "Toggle sidebar",
    "사이드바 닫기": "Close sidebar",
    "라이트/다크 테마 전환": "Toggle light/dark theme",
    "테마 전환": "Toggle theme",
    "언어 전환 (한국어/English)": "Switch language (English/한국어)",
    # --- 작업 위치 ---
    "기본(연동 안 함)": "Default (none)",
    "작업이 실행될 폴더/리포": "Folder/repo the job runs in",
    "폴더 탐색 또는 GitHub 리포에서 작업 위치 추가":
        "Add a workspace from a folder or GitHub repo",
    "연동 해제": "Remove",
    "📁 폴더 선택": "📁 Folder",
    "⑂ GitHub 리포": "⑂ GitHub repo",
    "닫기": "Close",
    "연결 끊김 · 재연결 중…": "Disconnected · reconnecting…",

    # --- 작업 상세 에러 배너 ---
    "CLI를 찾을 수 없습니다.": "CLI not found.",
    "에이전트의 CLI가 설치되어 PATH에 있는지 확인하세요.":
        "agent's CLI is installed and on your PATH.",
    "셋업에서 확인 →": "Check in setup →",
    "시간 초과.": "Timed out.",
    "작업이 제한 시간 안에 끝나지 않았습니다. 컴포저의 ＋ 메뉴에서 타임아웃을 늘려 다시 시도해 보세요.":
        "The job didn't finish in time. Increase the timeout from the composer's ＋ menu and retry.",
    "인증이 필요합니다.": "Authentication required.",
    "터미널에서": "In your terminal, finish the login for",
    "CLI 로그인을 완료한 뒤 다시 시도하세요.": "CLI, then retry.",
    "로그인 안내 →": "Login help →",
    "취소된 작업입니다.": "This job was cancelled.",
    "사용 제한.": "Rate limited.",
    "제한이 풀리면 자동으로 재개됩니다.": "It resumes automatically once the limit clears.",
    "작업이 실패했습니다.": "The job failed.",

    # --- JS: 에이전트/모델 팝업 ---
    "토론": "Debate",
    "여러 에이전트가 제안·비평하고 하나가 종합합니다 (사용량 다중 소모)":
        "Several agents propose & critique; one synthesizes (uses multiple quotas)",
    # --- JS: 노트 컨텍스트 메뉴 ---
    "고정": "Pin",
    "고정 해제": "Unpin",
    "이름 변경": "Rename",
    "새 이름": "New name",
    "새 그룹…": "New group…",
    "그룹 이름": "Group name",
    "그룹에서 제거": "Remove from group",
    "그룹으로 이동": "Move to group",
    "보관": "Archive",
    "보관 해제": "Unarchive",
    "이 노트를 삭제할까요? 되돌릴 수 없습니다.": "Delete this note? This can't be undone.",
    # --- JS: 작업 위치 모달 ---
    "이 폴더에서 작업이 실행됩니다": "Jobs will run in this folder",
    "요청한 iCloud 폴더가 이 Mac에 없습니다. Finder에서 먼저 내려받은 뒤 다시 선택하세요.":
        "That iCloud folder isn't on this Mac. Download it in Finder first, then pick it again.",
    "추가하지 못했습니다.": "Couldn't add it.",
    "리포 검색…": "Search repos…",
    "리포 목록을 불러오지 못했습니다.": "Couldn't load the repo list.",
    "클론 중…": "Cloning…",
    "클론해서 추가": "Clone & add",
    "바로가기": "Shortcuts",
    "상위 폴더": "Parent folder",
    "하위 폴더가 없습니다": "No subfolders",
    "이 폴더 선택": "Select this folder",
    "GitHub 확인 중…": "Checking GitHub…",
    "gh(GitHub CLI)가 설치되어 있지 않습니다.": "The gh (GitHub CLI) isn't installed.",
    "GitHub 로그인이 필요합니다.<br>터미널에서 <code>gh auth login</code> 후 다시 시도하세요.":
        "GitHub login required.<br>Run <code>gh auth login</code> in your terminal, then retry.",
    "로그인:": "Signed in:",
    "리포 목록 불러오는 중…": "Loading repos…",
    "브랜치": "Branch",
    "불러오는 중…": "Loading…",
    "브랜치 조회 실패": "Failed to load branches",
    "선택한 브랜치를 클론해 추가합니다": "Clones the selected branch and adds it",
    "검색 결과 없음": "No matches",

    # --- 셋업 위저드 (setup.js) ---
    "Agentic OS에 오신 것을 환영합니다": "Welcome to Agentic OS",
    "구독 중인 AI CLI들을 하나의 대시보드로 — 실측 남은 사용량으로 자동 배분하고, 결과는 노트로 쌓입니다.":
        "All your AI CLIs in one dashboard — routed by real remaining quota, with results saved as notes.",
    "자동 배분": "Auto-routing",
    "남은 사용량이 가장 많은 에이전트로 작업을 배분합니다":
        "Sends work to whichever agent has the most quota left",
    "사용 제한에 걸리면 큐에 두었다가 자동으로 재개합니다":
        "Queues work through rate limits and resumes automatically",
    "노트 메모리": "Note memory",
    "모든 결과가 마크다운 노트로 저장되고 이어서 작업할 수 있습니다":
        "Every result is saved as a Markdown note you can continue from",
    "협의 모드": "Council mode",
    "여러 에이전트가 제안·비평하고 하나가 종합합니다":
        "Several agents propose & critique; one synthesizes",
    "사용하는 에이전트를 선택하세요": "Choose the agents you use",
    "설치가 감지된 CLI는 자동으로 선택됩니다. 나중에 ⚙︎ 에이전트 설정에서 바꿀 수 있어요.":
        "Detected CLIs are pre-selected. You can change this later under ⚙︎ Agent settings.",
    "재확인": "Re-check",
    "✓ 설치됨": "✓ Installed",
    "✗ 없음": "✗ Not found",
    "CLI가 없으면 이 에이전트의 작업은 실패합니다 — 설치:":
        "Without the CLI, this agent's jobs will fail — install:",
    "각 CLI에 로그인하세요": "Log in to each CLI",
    "Agentic OS가 대신 로그인할 수는 없습니다. 각 CLI의 OAuth 로그인을 터미널에서 마친 뒤 계속 진행하세요. (로그인 없이 진행해도 되지만 해당 에이전트 작업은 실패합니다)":
        "Agentic OS can't log in for you. Finish each CLI's OAuth login in your terminal, then continue. (You can proceed without it, but that agent's jobs will fail.)",
    "로그인 불필요": "No login needed",
    "보조 도구 (선택)": "Optional tools",
    "노트 저장 위치:": "Notes saved to:",
    "— <code>aos.env</code>의 <code>AOS_VAULT_PATH</code>로 Obsidian 볼트를 지정할 수 있어요":
        " — set <code>AOS_VAULT_PATH</code> in <code>aos.env</code> to use an Obsidian vault",
    "준비가 끝났습니다": "You're all set",
    "선택한 에이전트만 대시보드·사용량·자동 라우팅에 나타납니다.":
        "Only the agents you picked appear in the dashboard, usage, and auto-routing.",
    "협의 모드 사용 가능": "Council mode available",
    "개 에이전트": "agents",
    "협의 모드는 에이전트": "Council mode needs at least",
    "개 이상이 필요해 비활성화됩니다": "agents, so it's disabled",
    "확인 중…": "Checking…",
    "시작하기": "Get started",
    "마침": "Finish",
    "다음": "Next",
    "이전": "Back",
    "건너뛰기": "Skip",
    "저장에 실패했습니다": "Failed to save",
    "서버에 연결할 수 없습니다": "Couldn't reach the server",
    "환영": "Welcome",
    "로그인": "Login",
    "셋업 진행 단계": "Setup progress",
    "차": "#",
    "자동 추천": "Auto pick",
    "복사": "Copy",
    "복사됨": "Copied",

    # --- CLI 메타데이터 (app/setup.py, /api/setup/status로 전달) ---
    "로컬": "Local",
    "코딩·분석·글쓰기에 강한 범용 에이전트":
        "Versatile agent, strong at coding, analysis, and writing",
    "ChatGPT/Codex 구독 CLI — 코딩·자동화에 강함":
        "ChatGPT/Codex subscription CLI — strong at coding and automation",
    "Gemini 기반 에이전트 — Google 계정으로 로그인":
        "Gemini-based agent — sign in with a Google account",
    "공식 Gemini 터미널 CLI — OAuth 또는 Vertex AI":
        "Official Gemini terminal CLI — OAuth or Vertex AI",
    "xAI Grok — SuperGrok 구독 CLI": "xAI Grok — SuperGrok subscription CLI",
    "로컬 Gateway 에이전트 — 여러 클라우드 모델 프로필 연결":
        "Local Gateway agent — connects multiple cloud model profiles",
    "로컬 실행 모델 — 무제한·개인 데이터에 적합":
        "Local model — unlimited, good for private data",
    "터미널에서 claude auth login 으로 Anthropic/Claude 구독 계정 OAuth를 완료하세요. 로그인 후 재확인을 누르세요.":
        "Run claude auth login in your terminal for Anthropic/Claude subscription OAuth. Then hit Re-check.",
    "터미널에서 codex login 으로 ChatGPT 계정 OAuth 로그인을 완료하세요. (API 키가 아니라 구독 세션을 씁니다. 상태 확인: codex login status)":
        "Run codex login for ChatGPT OAuth (subscription session, not an API key). Check with: codex login status.",
    "agy 첫 실행 시 Google 계정 OAuth 로그인이 진행되고 시스템 키체인에 저장됩니다.":
        "On first run, agy walks you through Google OAuth and stores it in the system keychain.",
    "권장(업무/Workspace·Vertex): 1) gcloud auth application-default login  2) ~/.gemini/.env 에 GOOGLE_CLOUD_PROJECT·GOOGLE_CLOUD_LOCATION (예: global)·GOOGLE_GENAI_USE_VERTEXAI=true  3) ~/.gemini/settings.json 의 security.auth.selectedType 을 vertex-ai 로. 또는 터미널에서 gemini 실행 후 Sign in with Google (oauth-personal) — Workspace는 GOOGLE_CLOUD_PROJECT 필요. API 키 모드는 워커가 키를 제거해 동작하지 않습니다. (Antigravity와 별도 CLI)":
        "Recommended (Workspace/Vertex): 1) gcloud auth application-default login  2) set GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (e.g. global), GOOGLE_GENAI_USE_VERTEXAI=true in ~/.gemini/.env  3) set security.auth.selectedType to vertex-ai in ~/.gemini/settings.json. Or run gemini and Sign in with Google (oauth-personal); Workspace needs GOOGLE_CLOUD_PROJECT. API key mode will not work — the worker strips keys. (Separate from Antigravity.)",
    "grok CLI 자체 로그인 절차(브라우저 인증)를 완료하세요.":
        "Complete the grok CLI's own login (browser auth).",
    "터미널에서 openclaw models auth login 으로 모델 프로바이더(OAuth/키)를 연결하세요. 프로필이 없으면 작업이 실패합니다. Gateway가 필요하면 openclaw gateway 를 실행해 두세요.":
        "Run openclaw models auth login to connect a model provider. Without a profile, jobs fail. Start openclaw gateway if needed.",
    "로컬 실행 — 로그인 불필요, 사용량 무제한입니다.":
        "Runs locally — no login needed, unlimited usage.",
    "✓ 로그인됨": "✓ Signed in",
    "✗ 로그인 필요": "✗ Sign-in required",
    "? 상태 미확인": "? Status unknown",
    "상태:": "Status:",
    "로그인 전에는 이 에이전트 작업이 실패합니다. 아래 명령을 터미널에서 실행한 뒤 재확인하세요.":
        "Jobs for this agent will fail until you sign in. Run the command below in a terminal, then Re-check.",
    "Agentic OS가 대신 로그인할 수는 없습니다. 각 CLI의 OAuth 로그인을 터미널에서 마친 뒤 재확인을 누르세요. 로그인 없이 진행해도 되지만 해당 에이전트 작업은 실패합니다.":
        "Agentic OS cannot sign in for you. Finish each CLI's OAuth in a terminal, then Re-check. You can continue without signing in, but those agents' jobs will fail.",
    "사용량 실측 표시 (claude·codex·gemini·grok 등 잔여 사용량 기반 자동 라우팅)":
        "Real usage display (auto-routing by remaining quota for claude/codex/gemini/grok etc.)",
    "GitHub 리포를 작업 위치로 연동": "Use a GitHub repo as a workspace",
    "노트(메모리) 전문 검색": "Full-text search over notes (memory)",

    # --- 비전 보드 ---
    "비전 보드": "Vision board",
    "프로젝트": "Projects",
    "아직 프로젝트가 없습니다. 위에서 첫 목표를 보내 보세요.":
        "No projects yet. Send your first goal above.",
    "완료": "Done",
    "계획 중": "Planning",
    "계획 실패": "Plan failed",
    "승인 대기": "Awaiting approval",
    "실행 중": "Running",
    "일시정지": "Paused",
    "실패": "Failed",
    "취소됨": "Cancelled",
    "대기": "Pending",
    "대기열": "Queued",
    "메인 에이전트가 계획을 세우는 중입니다…": "The main agent is drafting a plan…",
    "계획이 준비되었습니다.": "The plan is ready.",
    "그래프를 검토한 뒤 승인하면 자동으로 실행됩니다.":
        "Review the graph and approve to run automatically.",
    "✓ 승인하고 실행": "✓ Approve & run",
    "재계획": "Replan",
    "계획 재시도": "Retry planning",
    "계획 수립에 실패했습니다": "Failed to draft a plan",
    "일시정지됨": "Paused",
    "실패한 태스크를 클릭해 재시도하거나, 재계획하세요.":
        "Click the failed task to retry, or replan.",
    "⏸ 일시정지": "⏸ Pause",
    "▶ 이어서 실행": "▶ Resume",
    "일시정지하는 중 — 실행 중인 태스크가 끝나면 멈춥니다.":
        "Pausing — will stop once running tasks finish.",
    "노드를 클릭해 사이드바에서 태스크를 편집한 뒤 이어서 실행하세요.":
        "Click a node to edit the task in the sidebar, then resume.",
    "프로젝트가 완료되었습니다.": "Project completed.",
    "취소된 프로젝트입니다.": "This project was cancelled.",
    "재시도": "Retry",
    "실행 로그 →": "Run log →",
    "프로젝트와 모든 태스크·산출물을 삭제할까요?":
        "Delete this project with all tasks and artifacts?",
    "취소된 항목 모두 비우기": "Clear all cancelled",
    "이 프로젝트를 삭제할까요? 되돌릴 수 없습니다.":
        "Delete this project? This can't be undone.",
    "취소된 프로젝트를 모두 삭제할까요? 되돌릴 수 없습니다.":
        "Delete all cancelled projects? This can't be undone.",
    "삭제하지 못했습니다": "Couldn't delete it",
    "취소된 프로젝트 {n}개를 삭제했습니다": "Deleted {n} cancelled project(s)",
    # --- 워크플로 다이어그램 편집기 ---
    "노드를 끌어 배치하고, 포트를 이어 연결하세요.":
        "Drag nodes to arrange them, and drag port to port to connect.",
    "실행 중에는 그래프를 편집할 수 없습니다.":
        "The graph can't be edited while the project is running.",
    "태스크": "Task",
    "자동 정렬": "Tidy up",
    "화면 맞춤": "Fit to screen",
    "확대": "Zoom in",
    "축소": "Zoom out",
    "제목": "Title",
    "설명": "Description",
    "종류": "Type",
    "태스크 제목": "Task title",
    "구체적 작업 지시 (비우면 제목을 씁니다)":
        "Specific instructions (defaults to the title if left empty)",
    "추가": "Add",
    "편집": "Edit",
    "저장": "Save",
    "선행 태스크": "Depends on",
    # 사이드바 태스크 인스펙터(static/chat-rail.js)
    "추가 지시": "Extra instructions",
    "고급": "Advanced",
    # 홈 대시보드 재구성 — 좌: 비전보드, 우: 작업 편집
    "작업 편집": "Edit task",
    "비전 보드로 만들 목표를 적어 주세요…": "Describe a goal to turn into a vision board…",
    "비전 보드를 만들지 못했습니다.": "Could not create the vision board.",
    "아직 진행 중입니다 — 끝나면 여기서 이어서 지시할 수 있어요.":
        "Still running — you can send a follow-up here once it finishes.",
    "기본": "Default",
    "다시 실행": "Run again",
    "시도": "Attempt",
    "시도 이력": "Attempt history",
    "다시 실행하지 못했습니다.": "Could not run it again.",
    "연결 저장": "Save connections",
    "저장 중…": "Saving…",
    "연결을 저장했습니다": "Connections saved",
    "연결을 삭제했습니다": "Connection deleted",
    "이 태스크를 삭제할까요? 연결도 함께 끊어집니다.":
        "Delete this task? Its connections will be removed too.",
    "이 연결을 끊을까요?": "Remove this connection?",
    "서버에 연결하지 못했습니다": "Couldn't reach the server",
    # 편집 API가 400으로 돌려주는 검증 메시지 (캔버스가 그대로 띄운다).
    # 태스크 수·순환 목록처럼 값이 끼는 메시지는 원문(한국어)으로 폴백된다.
    "이미 실행된 태스크는 편집할 수 없습니다": "Tasks that already ran can't be edited",
    "계획 검토 또는 일시정지 상태에서만 그래프를 편집할 수 있습니다":
        "The graph can only be edited while awaiting approval or paused",
    "프로젝트를 찾을 수 없습니다": "Project not found",
    "태스크를 찾을 수 없습니다": "Task not found",
    "제목과 설명은 비울 수 없습니다": "Title and description can't be empty",
    "제목은 비울 수 없습니다": "Title can't be empty",
    "자기 자신에 의존할 수 없습니다": "A task can't depend on itself",
    "이미 연결되어 있습니다": "Already connected",
    "순환 의존은 만들 수 없습니다": "Can't create a circular dependency",
    "연결": "Connect",
    "연결 삭제": "Delete connection",
    "연결할 태스크를 탭하세요": "Tap a task to connect it",
    "연결 모드를 취소했습니다": "Connect mode cancelled",

    # --- 태스크 오류 조치 (모델 교체 · 지시 추가) ---
    "완료로 기록됐지만 에이전트가 결과를 내놓지 않았습니다":
        "Marked done, but the agent produced no result",
    "완료로 기록됐지만 결과가 오류 안내문입니다":
        "Marked done, but the result is an error notice",
    "⟳ 조치하고 다시 실행": "⟳ Fix up and re-run",
    "모델 교체": "Switch model",
    "지시 추가": "Add instructions",
    "예: 권한이 필요한 명령은 쓰지 말고 파일 편집만으로 끝내라":
        "e.g. Don't run commands that need permission — finish with file edits only",
    "이 태스크에 의존하는 후속 태스크도 다시 실행":
        "Re-run downstream tasks that depend on this one",
    "이 설정으로 다시 실행": "Re-run with these settings",
    "완료 또는 실패한 태스크만 다시 실행할 수 있습니다":
        "Only completed or failed tasks can be re-run",

    # --- 채널: 사이드바 네비게이션 / 채널 상세 페이지 ---
    "채널": "Channels",
    "만들기": "Create",
    "이 채널에 새 주제로 말을 걸어보세요…": "Start a new topic in this channel…",
    "답장…": "Reply…",
    "전송하지 못했습니다": "Couldn't send",
    "실행 중…": "Working…",
    "이 채널과 모든 대화를 삭제할까요? 되돌릴 수 없습니다.":
        "Delete this channel and all its conversations? This can't be undone.",
    "쓰레드로 답장하기": "Reply in thread",
    "쓰레드": "Thread",
    "개 답장": " replies",
    # --- 홈 Orca 셸: 우측 채팅·세션 레일 / 중앙 탭 / 작업 큐 상태바 ---
    # (레일·탭바 공통 문구는 프로젝트 상세 화면도 함께 쓴다)
    "채팅": "Chat",
    "채팅 열기": "Open chat",
    "채팅 열기/닫기": "Toggle chat",
    "다시 시도": "Retry",
    "작업이 삭제되었거나 서버가 아직 이 화면을 모릅니다(재시작 필요).":
        "The job was deleted, or the server hasn't picked up this view yet (restart needed).",
    "보낼 수 없습니다.": "Couldn't send.",
    "이 세션을 이어받아 같은 에이전트로 진행합니다":
        "Continues this session with the same agent",
    "이 대화를 컨텍스트로 붙여 같은 에이전트로 진행합니다":
        "Continues with the same agent, attaching this conversation as context",
    "세션": "Sessions",
    "접기/펼치기": "Collapse/expand",
    "패널 너비 조절": "Resize panel",
    "작업 탭": "Job tabs",
    "이전 탭": "Previous tab",
    "다음 탭": "Next tab",
    "작업": "Job",
    "작업 없음": "No jobs",
    "실행": "Running",
    "불러올 수 없습니다.": "Couldn't load.",
    "오른쪽에서 작업을 보내면 진행과정이 여기 탭으로 열립니다.":
        "Send a job from the right and its progress opens here as a tab.",
    "아직 작업이 없습니다. 아래에서 첫 작업을 보내 보세요.":
        "No jobs yet. Send your first one below.",
}
