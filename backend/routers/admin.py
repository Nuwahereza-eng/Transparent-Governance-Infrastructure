"""Admin-only utility routes — currently exposes a 'reset demo' endpoint that
wipes and reseeds the database, intended for hackathon judges who want to
replay the demo without restarting the server."""
import os
import shutil
import importlib

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from config import DATA_DIR
from database import Base, SessionLocal, engine
from models import Role, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/reset-demo")
def reset_demo(user: User = Depends(get_current_user)):
    """Wipe every table and re-run seed.py. Admin only."""
    if user.role != Role.admin:
        raise HTTPException(403, "Admin only")
    # Drop and recreate all tables.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # Also clear uploaded evidence files so the demo starts clean.
    uploads = DATA_DIR / "uploads"
    if uploads.exists():
        for child in uploads.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except Exception:
                pass
    # Reseed.
    import seed as _seed
    importlib.reload(_seed)
    _seed.seed()
    return {"status": "ok", "message": "Database wiped and reseeded with demo data."}
