from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from typing import Dict, Any, List
from ut_vfx.core.infra.database_manager import DatabaseManager
from ut_vfx.api.core.security import create_access_token, get_current_user

router = APIRouter(prefix="/api/users", tags=["Users"])

def get_db():
    return DatabaseManager()

@router.post("/login")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: DatabaseManager = Depends(get_db)):
    """Authenticate user and issue JWT."""
    # Since password is not strongly enforced in legacy DB, just verify username exists
    user_id = db.user_repo.get_user_id(form_data.username)
    if not user_id:
        raise HTTPException(status_code=401, detail="Incorrect username")
    user_roles = db.user_repo.get_user_roles(form_data.username)
    
    access_token = create_access_token(data={"sub": form_data.username, "roles": user_roles})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/")
def get_user_id(username: str, db: DatabaseManager = Depends(get_db)):
    """Get User ID by username or display name."""
    user_id = db.user_repo.get_user_id(username)
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user_id}

@router.post("/sync")
def sync_users(users_dict: Dict[str, Any], db: DatabaseManager = Depends(get_db)):
    """Sync a dictionary of users into the database."""
    success = db.user_repo.sync_users(users_dict)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to sync users")
    return {"status": "success"}

@router.get("/{username}/profile_pic")
def get_profile_pic(username: str, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Get the profile picture path for a user."""
    path = db.user_repo.get_user_profile_pic(username)
    if not path:
        raise HTTPException(status_code=404, detail="Profile picture not found")
    return {"profile_pic_path": path}

@router.put("/{username}/profile_pic")
def update_profile_pic(username: str, path: str, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Update the profile picture path for a user."""
    success = db.user_repo.update_user_profile_pic(username, path)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update profile picture")
    return {"status": "success"}
