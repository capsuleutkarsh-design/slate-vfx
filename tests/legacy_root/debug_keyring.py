import keyring

val = keyring.get_password('Slate', 'db_host')
print(f"KEYRING DB_HOST: {val}")
