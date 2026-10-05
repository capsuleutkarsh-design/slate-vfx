from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from slate.core.infra.database_manager import DatabaseManager
from slate.api.core.security import create_access_token, get_current_user

router = APIRouter(prefix="/api/users", tags=["Users"])

def get_db():
    return DatabaseManager()

@router.post("/login")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: DatabaseManager = Depends(get_db)):
    """Check the user name and password exactly as the sign-in window does, then issue a JWT."""
    from slate.core.domain.user_manager import UserManager
    user = UserManager(db=db).authenticate(form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect user name or password")
    access_token = create_access_token(data={"sub": user["username"], "roles": user["roles"]})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/")
def get_user_id(username: str, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Get User ID by username or display name."""
    user_id = db.user_repo.get_user_id(username)
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user_id}

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
