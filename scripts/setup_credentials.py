"""
UT_VFX - Credential Setup Utility

This script helps configure database credentials securely on client machines.
Run this after installation to set up database access.

Usage:
    python scripts/setup_credentials.py

Options:
    --method [env|keyring|file]  Choose storage method
    --non-interactive            Use defaults (for scripted installations)
"""

import sys
import os
import argparse
from pathlib import Path
import getpass
import json

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def setup_environment_variable(host, port, dbname, user, password):
    """Setup credentials via environment variables (requires manual setup)."""
    print("\n" + "="*60)
    print("ENVIRONMENT VARIABLE METHOD")
    print("="*60)
    print("\nTo configure credentials via environment variables:")
    print("\n1. Open System Properties > Environment Variables")
    print("2. Add the following SYSTEM variables:")
    print(f"\n   DB_HOST = {host}")
    print(f"   DB_PORT = {port}")
    print(f"   DB_NAME = {dbname}")
    print(f"   DB_USER = {user}")
    print(f"   DB_PASSWORD = {password}")
    print("\n3. Restart the application")
    print("\nNote: This method is most secure and recommended for servers.")
    print("="*60)
    
    return False  # Indicates manual setup required

def setup_keyring(host, port, dbname, user, password):
    """Setup credentials via Windows Credential Manager."""
    try:
        import keyring
        
        print("\n" + "="*60)
        print("WINDOWS CREDENTIAL MANAGER METHOD")
        print("="*60)
        
        service_name = "UTVFX"
        
        # Store credentials
        keyring.set_password(service_name, "db_host", host)
        keyring.set_password(service_name, "db_port", str(port))
        keyring.set_password(service_name, "db_name", dbname)
        keyring.set_password(service_name, "db_user", user)
        keyring.set_password(service_name, "db_password", password)
        
        print(f"\n✅ Credentials stored in Windows Credential Manager")
        print(f"   Service: {service_name}")
        print(f"   User: {user}")
        print(f"   Host: {host}:{port}")
        print(f"\nTo view: Control Panel > Credential Manager > Windows Credentials")
        print("="*60)
        
        return True
        
    except ImportError:
        print("\n❌ ERROR: 'keyring' library not installed.")
        print("   Run: pip install keyring")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: Failed to store credentials in keyring: {e}")
        return False

def setup_encrypted_file(host, port, dbname, user, password):
    """Setup credentials via encrypted config file (fallback)."""
    print("\n" + "="*60)
    print("ENCRYPTED FILE METHOD (Fallback)")
    print("="*60)
    print("\n⚠️  WARNING: This method is less secure than keyring or environment variables.")
    
    confirm = input("\nContinue with file storage? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        return False
    
    try:
        from cryptography.fernet import Fernet
        
        # Generate or load encryption key
        local_appdata = Path(os.getenv('LOCALAPPDATA')) / "UTVFX"
        local_appdata.mkdir(parents=True, exist_ok=True)
        
        key_file = local_appdata / ".encryption_key"
        if key_file.exists():
            with open(key_file, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            # Set file permissions (Windows)
            try:
                import stat
                os.chmod(key_file, stat.S_IREAD | stat.S_IWRITE)
            except:
                pass
        
        cipher = Fernet(key)
        
        # Encrypt credentials
        credentials = {
            "db_host": host,
            "db_port": port,
            "db_name": dbname,
            "db_user": user,
            "db_password": password
        }
        
        encrypted_data = cipher.encrypt(json.dumps(credentials).encode())
        
        creds_file = local_appdata / ".db_credentials"
        with open(creds_file, 'wb') as f:
            f.write(encrypted_data)
        
        # Set file permissions
        try:
            import stat
            os.chmod(creds_file, stat.S_IREAD | stat.S_IWRITE)
        except:
            pass
        
        print(f"\n✅ Credentials stored in encrypted file")
        print(f"   Location: {creds_file}")
        print(f"   Encryption key: {key_file}")
        print("\n⚠️  Keep these files secure!")
        print("="*60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: Failed to create encrypted file: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Configure UT_VFX database credentials")
    parser.add_argument("--method", choices=['env', 'keyring', 'file'], 
                       help="Storage method (default: auto-detect best)")
    parser.add_argument("--non-interactive", action='store_true',
                       help="Use defaults from client_config.json (no prompts)")
    args = parser.parse_args()
    
    print("\n" + "="*60)
    print("UT_VFX - DATABASE CREDENTIALS SETUP")
    print("="*60)
    
    # Load defaults from client_config.json if exists
    config_file = project_root / "client_config.json"
    defaults = {}
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                defaults = {
                    'host': config.get('db_host', '192.168.0.45'),
                    'port': config.get('db_port', 5432),
                    'dbname': config.get('db_name', 'ut_vfx'),
                    'user': config.get('db_user', 'postgres')
                }
        except Exception as e:
            print(f"Warning: Could not read client_config.json: {e}")
    
    if not defaults:
        defaults = {
            'host': '192.168.0.45',
            'port': 5432,
            'dbname': 'ut_vfx',
            'user': 'postgres'
        }
    
    # Interactive mode
    if not args.non_interactive:
        print("\nEnter database connection details:")
        print("(Press Enter to use default values shown in brackets)\n")
        
        host = input(f"Database Host [{defaults['host']}]: ").strip() or defaults['host']
        port = input(f"Database Port [{defaults['port']}]: ").strip() or str(defaults['port'])
        try:
            port = int(port)
        except ValueError:
            print(f"Invalid port, using default: {defaults['port']}")
            port = defaults['port']
        
        dbname = input(f"Database Name [{defaults['dbname']}]: ").strip() or defaults['dbname']
        user = input(f"Database User [{defaults['user']}]: ").strip() or defaults['user']
        password = getpass.getpass("Database Password: ").strip()
        
        if not password:
            print("\n❌ ERROR: Password cannot be empty")
            return 1
    else:
        # Non-interactive mode
        print("\nUsing defaults from client_config.json...")
        host = defaults['host']
        port = defaults['port']
        dbname = defaults['dbname']
        user = defaults['user']
        password = getpass.getpass("Database Password: ").strip()
        
        if not password:
            print("\n❌ ERROR: Password required even in non-interactive mode")
            return 1
    
    # Test connection (optional but recommended)
    print("\nTesting database connection...")
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password, connect_timeout=5
        )
        conn.close()
        print("✅ Connection successful!")
    except ImportError:
        print("⚠️  psycopg2 not installed, skipping connection test")
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        retry = input("\nContinue anyway? [y/N]: ").strip().lower()
        if retry != 'y':
            return 1
    
    # Choose storage method
    if args.method:
        method = args.method
    else:
        print("\n" + "="*60)
        print("Choose credential storage method:")
        print("="*60)
        print("\n1. Windows Credential Manager (Recommended)")
        print("   - Most secure for client PCs")
        print("   - Easy management via Control Panel")
        print("   - Requires 'keyring' library")
        print("\n2. Environment Variables")
        print("   - Good for servers/CI-CD")
        print("   - Requires manual setup")
        print("   - System-wide configuration")
        print("\n3. Encrypted File (Fallback)")
        print("   - Works without dependencies")
        print("   - Less secure than other methods")
        print("   - Not recommended for production")
        
        choice = input("\nSelect method [1/2/3]: ").strip()
        method_map = {'1': 'keyring', '2': 'env', '3': 'file'}
        method = method_map.get(choice, 'keyring')
    
    # Setup credentials
    success = False
    if method == 'keyring':
        success = setup_keyring(host, port, dbname, user, password)
    elif method == 'env':
        success = setup_environment_variable(host, port, dbname, user, password)
    elif method == 'file':
        success = setup_encrypted_file(host, port, dbname, user, password)
    
    if success:
        print("\n" + "="*60)
        print("✅ SETUP COMPLETE!")
        print("="*60)
        print("\nYou can now launch UT_VFX.")
        print("Credentials will be loaded automatically from secure storage.")
        return 0
    else:
        print("\n" + "="*60)
        print("⚠️  SETUP INCOMPLETE")
        print("="*60)
        print("\nPlease complete the manual setup steps shown above.")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nSetup cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
