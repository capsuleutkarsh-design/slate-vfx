print("Python works!")
try:
    import keyring
    print("keyring imported")
except ImportError:
    print("keyring not installed")
except Exception as e:
    print(f"keyring import error: {e}")

try:
    pwd = keyring.get_password("UTVFX", "db_host")
    print(f"keyring read: {pwd}")
except Exception as e:
    print(f"keyring get_password error: {e}")
