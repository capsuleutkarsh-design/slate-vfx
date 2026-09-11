import re
with open('d:/Soft/Slate/V0040/slate/core/infra/postgres_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()
    matches = re.findall(r'execute\(\s*f[\"''].*?\{.*?\}.*?[\"'']\)', text, re.IGNORECASE)
    print(f'Found {len(matches)} potential unparameterized execute calls.')
    if len(matches) > 0:
        print(matches[:3])
