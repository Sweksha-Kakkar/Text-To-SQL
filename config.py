"""Central settings. Secrets come from a .env file (TableTalk/.env or the folder above)."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
_ENV_CANDIDATES = [ROOT / ".env", ROOT.parent / ".env"]
ENV_FILE = next((p for p in _ENV_CANDIDATES if p.exists()), ROOT / ".env")
load_dotenv(ENV_FILE)

# --- Gemini (Google AI Studio) ---------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Models picked from the AI Studio catalogue that worked on the free tier
# (tested 25 Nov 2026). The first one is the default.
SQL_MODELS = [
    "gemini-3.5-flash",       # best quality/speed balance on the free tier
    "gemini-3.5-flash-lite",  # fastest, used as automatic fallback
    "gemini-3.8-flash",       # newest flash, often busy (503) on free tier
    "gemini-3.1-flash-lite",
    "gemma-4-31b-it",         # open-weights model, also on Hugging Face
]
DEFAULT_MODEL = SQL_MODELS[0]
FALLBACK_MODEL = "gemini-3.5-flash-lite"
THINKING_LEVELS = ["minimal", "low", "medium", "high"]
DEFAULT_THINKING = "low"
EMBED_MODEL = "gemini-embedding-2"
EMBED_DIM = 768  # smaller vectors than the 3072 default: faster, plenty for ~70 docs

# --- PostgreSQL --------------------------------------------------------------
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DB = os.getenv("PG_DB", "tabletalk")
PG_SUPERUSER = os.getenv("PG_SUPERUSER", "postgres")

# Two least-privilege roles, created by db/setup_db.py:
#   tabletalk_app    - owns the RAG index (schema "rag"), reads table metadata
#   tabletalk_reader - SELECT-only on the 10 store tables; runs the generated SQL
APP_USER = "tabletalk_app"
READER_USER = "tabletalk_reader"

# --- Safety limits -----------------------------------------------------------
MAX_ROWS = 1000             # rows returned to the UI
STATEMENT_TIMEOUT_MS = 8000  # kill slow generated queries
MAX_FIX_ATTEMPTS = 2        # self-correction retries after a SQL error
HISTORY_TURNS = 3           # previous Q&A pairs sent for follow-up questions

STORE_TABLES = [
    "customers", "categories", "suppliers", "products", "warehouses",
    "inventory", "employees", "orders", "order_items", "reviews",
]


def env_secret(name: str) -> str:
    """Return a secret from .env, generating and saving one if it is missing."""
    value = os.getenv(name)
    if value:
        return value
    value = secrets.token_hex(16)
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    ENV_FILE.write_text(text + f"{name}={value}\n", encoding="utf-8")
    os.environ[name] = value
    return value


def pg_params(role: str, dbname: str = PG_DB) -> dict:
    """Connection keyword arguments for psycopg.connect()."""
    if role == "super":
        user, password = PG_SUPERUSER, os.getenv("PG_SUPERUSER_PASSWORD", "")
    elif role == "app":
        user, password = APP_USER, env_secret("PG_APP_PASSWORD")
    elif role == "reader":
        user, password = READER_USER, env_secret("PG_READER_PASSWORD")
    else:
        raise ValueError(f"unknown role {role!r}")
    return dict(host=PG_HOST, port=PG_PORT, dbname=dbname, user=user, password=password,
                connect_timeout=5)
