"""
Logging configuration for UT_VFX Production tool.
"""
import logging
import logging.handlers
from pathlib import Path
import sys
from datetime import datetime


def setup_logging():
    """Set up application logging with rotation."""
    # Create logs directory in user's AppData
    import os
    if sys.platform == "win32":
        log_dir = Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "UTVFX" / "logs"
    else:
        log_dir = Path.home() / ".ut_vfx" / "logs"
    
    log_dir.mkdir(parents=True, exist_ok=True)
    log_filename = log_dir / f"ut_vfx_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_filename,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    logging.info(f"Logging initialized. Log file: {log_filename}")
    return log_filename