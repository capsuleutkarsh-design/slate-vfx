import keyring

val = keyring.get_password('UTVFX', 'db_host')
print(f"KEYRING DB_HOST: {val}")
