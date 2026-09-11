from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any, Tuple
from ut_vfx.core.infra.database_manager import DatabaseManager
from pydantic import BaseModel
from ut_vfx.api.core.security import get_current_user

router = APIRouter(prefix="/api/shots", tags=["Shots"])

def get_db():
    return DatabaseManager()

class ShotUpdateRequest(BaseModel):
    shot_name: str
    data_json: str
    current_version: int

class ShotBatchItem(BaseModel):
    shot_name: str
    status: str
    priority: int
    data_json: str

@router.get("/{project_code}")
def get_shots(project_code: str, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Get all shots for a specific project."""
    shots = db.tracking_repo.get_tracking_shots(project_code)
    return {"project_code": project_code, "shots": shots}

@router.put("/{project_code}")
def update_shot(project_code: str, req: ShotUpdateRequest, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Safely update a shot using optimistic locking (versioning)."""
    success = db.tracking_repo.update_tracking_shot_safe(
        project_code, 
        req.shot_name, 
        req.data_json, 
        req.current_version
    )
    if not success:
        raise HTTPException(
            status_code=409, 
            detail="Update failed. The shot may have been modified by someone else. Please refresh and try again."
        )
    return {"status": "success", "message": f"Updated shot {req.shot_name}"}

@router.post("/{project_code}/batch")
def save_shots_batch(project_code: str, shots_data: List[Tuple[str, str, int, str]], db: DatabaseManager = Depends(get_db)):
    """Save a batch of shots to a project."""
    success = db.tracking_repo.save_tracking_shots(project_code, shots_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save shots batch")
    return {"status": "success", "message": f"Saved {len(shots_data)} shots"}
