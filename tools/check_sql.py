import re
from pathlib import Path

_target = (Path(__file__).resolve().parent.parent
           / 'slate' / 'core' / 'infra' / 'postgres_manager.py')
with open(_target, 'r', encoding='utf-8') as f:
    text = f.read()
    matches = re.findall(r'execute\(\s*f[\"''].*?\{.*?\}.*?[\"'']\)', text, re.IGNORECASE)
    print(f'Found {len(matches)} potential unparameterized execute calls.')
    if len(matches) > 0:
        print(matches[:3])
