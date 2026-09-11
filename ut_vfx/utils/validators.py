"""
Validation utilities for UT_VFX Production tool.
Enhanced with Studio Naming Conventions (No spaces, strict chars).
"""
import re
from pathlib import Path
import pandas as pd
from .security import security_validator, SecurityError


def validate_project_name(name: str) -> tuple[bool, str]:
    """
    Validate project name for safety AND studio standards.
    Studio Rules: No spaces, No brackets, Snake_Case or CamelCase only.
    """
    if not name or not name.strip():
        return False, "Project name cannot be empty"
    
    name = name.strip()
    
    # 1. Security Check (Basic OS safety)
    is_valid, sanitized_name, error_msg = security_validator.sanitize_filename(name)
    if not is_valid:
        return False, f"Security check failed: {error_msg}"
    
    # 2. Studio Pipeline Standards (The 'VFX Expert' Check)
    # Rule: No Spaces
    if " " in name:
        return False, "Studio Policy: Project names cannot contain spaces. Use underscores (_)."
        
    # Rule: No Brackets (Breaks command line tools)
    if "(" in name or ")" in name or "[" in name or "]" in name:
        return False, "Studio Policy: Brackets () [] are forbidden in project names."
        
    # Rule: Only AlphaNumeric + Underscore + Dash
    # Regex: Start with letter/number, contain only word chars or dashes
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', name):
        return False, "Studio Policy: Use only Letters, Numbers, Underscores (_), or Dashes (-)."
    
    # Check for reserved names on Windows
    reserved_names = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }
    if sanitized_name.upper() in reserved_names:
        return False, f"Project name '{sanitized_name}' is a reserved name on Windows"
    
    # Check length
    if len(sanitized_name) > 60:
        return False, "Project name is too long (max 60 characters for pipeline compatibility)"
    
    return True, "Valid project name"


def validate_excel_file(file_path: str) -> tuple[bool, str, pd.DataFrame]:
    """Validate Excel file structure and content with enhanced security."""
    try:
        if not file_path or not Path(file_path).exists():
            return False, "Excel file path is invalid or does not exist.", None
        
        # Use security validator for file validation
        file_path_obj = Path(file_path)
        validation_result = security_validator.validate_excel_file(file_path_obj)
        if not validation_result[0]:
            return False, validation_result[1], None
        
        # Check file size to avoid loading huge files
        file_size = file_path_obj.stat().st_size / (1024 * 1024)  # MB
        if file_size > 100:  # 100MB limit
            return False, "Excel file is too large (max 100MB).", None
        
        # Try to load the Excel file
        df = pd.read_excel(file_path)
        
        # Check if it has required columns
        # Also allow Uppercase variants
        cols_upper = [c.upper() for c in df.columns]
        
        has_reel = 'REEL' in cols_upper
        has_shot = 'SHOT' in cols_upper or 'SHOT NO' in cols_upper
        
        if not (has_reel and has_shot):
            return False, f"Missing columns. Found: {list(df.columns)}. Need: 'Reel' and 'Shot'", df
        
        # Check if there's any data
        if df.empty:
            return False, "Excel file is empty.", df
        
        return True, "Valid Excel file", df
    
    except SecurityError as e:
        return False, f"Security validation error: {str(e)}", None
    except pd.errors.EmptyDataError:
        return False, "Excel file is empty or has no data.", None
    except pd.errors.ParserError:
        return False, "Excel file format is invalid.", None
    except Exception as e:
        return False, f"Error reading Excel file: {str(e)}", None