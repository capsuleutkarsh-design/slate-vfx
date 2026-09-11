import os
import re

pg_dir = r"C:\Program Files\PostgreSQL\18\data"
conf_path = os.path.join(pg_dir, "postgresql.conf")
hba_path = os.path.join(pg_dir, "pg_hba.conf")

# 1. Update postgresql.conf
with open(conf_path, "r", encoding="utf-8") as f:
    conf_content = f.read()

# Replace listen_addresses if it exists, or add it
if "listen_addresses =" in conf_content:
    conf_content = re.sub(r"^[#\s]*listen_addresses\s*=\s*['\"].*?['\"]", "listen_addresses = '*'", conf_content, flags=re.MULTILINE)
else:
    conf_content += "\nlisten_addresses = '*'\n"

# Verify we got it, some variants might not have quotes
if "listen_addresses = '*'" not in conf_content:
    conf_content = re.sub(r"^[#\s]*listen_addresses\s*=.*", "listen_addresses = '*'", conf_content, flags=re.MULTILINE)

with open(conf_path, "w", encoding="utf-8") as f:
    f.write(conf_content)
    
print("postgresql.conf updated")

# 2. Update pg_hba.conf
with open(hba_path, "r", encoding="utf-8") as f:
    hba_content = f.read()

hba_rule = "host    all             all             0.0.0.0/0               md5"
if "0.0.0.0/0" not in hba_content:
    hba_content += f"\n# Added by UT_VFX Configurator\n{hba_rule}\n"
    with open(hba_path, "w", encoding="utf-8") as f:
        f.write(hba_content)
    print("pg_hba.conf updated")
else:
    print("pg_hba.conf already allows 0.0.0.0/0")
