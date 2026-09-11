import os
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from ut_vfx.core.infra.global_config import GlobalConfig

# Setup OAuth2 bearer
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/users/login")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

def get_secret_key() -> str:
    gc = GlobalConfig()
    secret = gc.get("jwt_secret")
    if not secret:
        # Generate a random 32-byte hex string
        import secrets
        secret = secrets.token_hex(32)
        gc.set("jwt_secret", secret)
    return secret

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception

def get_current_user_roles(token: str = Depends(oauth2_scheme)) -> list:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        roles = payload.get("roles", ["Artist"])
        return roles
    except JWTError:
        raise credentials_exception
