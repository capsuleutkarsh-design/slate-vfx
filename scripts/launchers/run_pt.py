import sys
import pytest

with open("pt_out.txt", "w") as f:
    sys.stdout = f
    sys.stderr = f
    try:
        pytest.main(["tests/test_user_manager.py", "-v"])
    except Exception as e:
        print("ERROR:", e)
