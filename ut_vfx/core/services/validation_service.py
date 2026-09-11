import pyblish.api
import pyblish.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class ValidationService:
    """
    Bridge between UT_VFX and Pyblish.
    Handles plugin registration and check execution.
    """
    
    def __init__(self):
        self.plugins_registered = False
        self._register_plugins()
        
    def _register_plugins(self):
        if self.plugins_registered:
            return
            
        # Register standard plugins (optional)
        # pyblish.api.register_all()
        
        # Register our custom plugins
        # Point to the 'plugins' directory relative to this file's parent (ut_vfx)
        # Assuming layout: ut_vfx/core/services/validation_service.py -> ../../plugins
        
        # Adjust path based on actual file location
        current_dir = Path(__file__).parent
        # Go up to ut_vfx root
        plugin_path = current_dir.parent.parent / "plugins"
        
        if plugin_path.exists():
            pyblish.api.register_plugin_path(str(plugin_path))
            logger.info(f"Registered Pyblish plugins from: {plugin_path}")
        else:
            logger.warning(f"Plugin path not found: {plugin_path}")
            
        self.plugins_registered = True
        
    def run_validation(self, shot_data):
        """
        Run validation on a Shot object.
        Returns (success, report).
        """
        # Context is the "World" for Pyblish
        context = pyblish.api.Context()
        context.data["name"] = "Shot Validation"
        
        # Create an Instance (The thing being checked)
        # We pass the shot object as data
        instance = context.create_instance(shot_data.shot_name, family="shot")
        instance.data["shot_object"] = shot_data
        
        # Run checks
        # We assume we are checking in-memory data, so we use 'Validation' order mostly
        logger.info(f"Running validation for {shot_data.shot_name}")
        pyblish.util.publish(context)
        
        results = []
        success = True
        
        for result in context.data["results"]:
            # Check if it failed
            if not result["success"]:
                success = False
            
            # Format report
            record = {
                "plugin": result["plugin"].__name__,
                "success": result["success"],
                "error": str(result["error"]) if result["error"] else None,
                "records": result["records"] # Log messages
            }
            results.append(record)
            
        return success, results
