"""Application configuration."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Allow overriding the data directory (used in production where a persistent
# disk is mounted at e.g. /var/data). Falls back to ./data for local dev.
_data_override = os.getenv("GOVTRUST_DATA_DIR")
DATA_DIR = Path(_data_override).resolve() if _data_override else BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")

# Auth
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# Audit chain genesis hash
GENESIS_HASH = "0" * 64
