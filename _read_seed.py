# Diagnose: OLD password still logs in (302), NEW password fails (200 = form re-render).
# seed_admin likely UPDATES the hash... but old still works => maybe seed_admin did nothing
# because password length cap? or it verifies existing and skips? Read the source.
import re
src = open(r'C:\Users\Administrator\coding\telegram-channel-watcher\credentials_db.py').read()
m = re.search(r'def seed_admin.*?(?=\ndef )', src, re.S)
print(m.group(0) if m else 'not found')
