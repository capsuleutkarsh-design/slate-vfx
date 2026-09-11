from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any, Optional
from ut_vfx.core.infra.database_manager import DatabaseManager
from pydantic import BaseModel
from ut_vfx.api.core.security import get_current_user

router = APIRouter(prefix="/api/attendance", tags=["Attendance"])

def get_db():
    return DatabaseManager()

class AttendanceLogRequest(BaseModel):
    username: str
    notes: Optional[str] = ""

@router.post("/check-in")
def log_check_in(req: AttendanceLogRequest, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Log a user check-in."""
    success = db.attendance_repo.log_check_in(req.username, req.notes)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to log check-in")
    return {"status": "success", "message": f"Checked in {req.username}"}

@router.post("/check-out")
def log_check_out(req: AttendanceLogRequest, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Log a user check-out."""
    success = db.attendance_repo.log_check_out(req.username, req.notes)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to log check-out. User may not be checked in.")
    return {"status": "success", "message": f"Checked out {req.username}"}

@router.get("/")
def get_attendance(limit: int = 100, db: DatabaseManager = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Get recent attendance records."""
    records = db.attendance_repo.get_attendance(limit=limit)
    return {"records": records}
