
import pyblish.api
import re

class ValidateNamingConvention(pyblish.api.InstancePlugin):
    """Validate that shots follow the studio naming convention: seq_shot_task_version"""
    
    order = pyblish.api.ValidatorOrder
    label = "Naming Convention"
    families = ["shot"]

    def process(self, instance):
        name = instance.data["name"]
        # Allow 3 or 4 parts
        pattern = r"^[A-Za-z0-9\-]+_[A-Za-z0-9\-]+_[A-Za-z0-9\-]+(_[A-Za-z0-9\-]+)?$"
        
        if not re.match(pattern, name):
            raise Exception(f"Shot '{name}' does not match pattern: seq_shot_task_version")
        else:
            self.log.info(f"Shot '{name}' is correctly named.")
