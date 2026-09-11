import importlib.metadata
import sys

def check(package_name):
    try:
        version = importlib.metadata.version(package_name)
        print(f"{package_name}: {version}")
    except importlib.metadata.PackageNotFoundError:
        print(f"{package_name}: NOT_FOUND")
    except Exception as e:
        print(f"{package_name}: ERROR {e}")

if __name__ == "__main__":
    print("--- checking versions ---")
    check("opentimelineio")
    check("PyOpenColorIO")
    check("opencolorio")
    
    # Also print site-packages location
    import site
    print(f"Site Packages: {site.getsitepackages()}")
