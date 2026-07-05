from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "aos.db"

VAULT_PATH = Path(
    "/Users/macmini/Library/Mobile Documents/com~apple~CloudDocs/LLM WIKI/Blogging"
)
MEMORY_DIR = VAULT_PATH / "Agentic OS"

PORT = 8899
JOB_TIMEOUT_SEC = 30 * 60
DEFAULT_RESUME_DELAY_MIN = 60
MAX_ATTEMPTS = 10
WORKER_POLL_SEC = 5
