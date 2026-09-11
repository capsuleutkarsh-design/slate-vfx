import sys
import subprocess
with open("pip_out.txt", "w") as f:
    result = subprocess.run([
        r"%USERPROFILE%\Documents\Studio_soft_2\env\Scripts\python.exe", 
        "-m", "pip", "install", "fastapi", "uvicorn", "websockets"
    ], capture_output=True, text=True)
    f.write(result.stdout)
    f.write("\nSTDERR:\n")
    f.write(result.stderr)
print("Done")
