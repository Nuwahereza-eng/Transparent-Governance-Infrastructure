"""Authentication routes."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from audit import chain
from auth import create_access_token, get_current_user, hash_password, verify_password
from database import get_db
from models import User
from rate_limit import limiter
from schemas import TokenOut, UserOut, UserRegister

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut)
@limiter.limit("10/minute")
def register(request: Request, payload: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        organization=payload.organization,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    chain.append(
        db, actor=user, action="user.register", entity_type="user", entity_id=user.id,
        payload={"email": user.email, "role": user.role.value},
    )
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
@limiter.limit("20/minute")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    chain.append(
        db, actor=user, action="user.login", entity_type="user", entity_id=user.id,
        payload={"email": user.email},
    )
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
