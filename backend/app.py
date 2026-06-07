"""FastAPI entrypoint."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import DATA_DIR
from database import Base, engine
from rate_limit import limiter
from routers import (
    approvals, auth_router, contractors, contracts_router,
    feedback, opendata, procurement, transparency,
)

# Import models so SQLAlchemy registers them before create_all.
import models  # noqa: F401

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Corruption-Resistant Procurement System",
    description="AI + hash-chained audit MVP for transparent public procurement.",
    version="0.2.0",
)

# Rate limiting.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(procurement.router)
app.include_router(approvals.router)
app.include_router(contracts_router.router)
app.include_router(feedback.router)
app.include_router(contractors.router)
app.include_router(transparency.router)
app.include_router(opendata.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------- Uploaded files (served read-only) ----------
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---------- Static frontend ----------
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def root():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/{page}.html")
    def page(page: str):
        target = FRONTEND_DIR / f"{page}.html"
        if target.exists():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIR / "index.html")
