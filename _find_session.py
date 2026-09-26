# 252 bytes = exactly what a v1 StringSession with NO auth key should decode... no.
# Actually a full v1 session = 1(ver) + 1(dc) + 4(ip) + 2(port) + 256(key) = 264 bytes -> b64 len 352.
# Our raw decoded = 252 bytes -> auth key truncated. Not a standard Telethon string.
# CONCLUSION: this string is corrupted or was produced by modified code.
#
# Recovery path: the watcher PYTHON PROCESS ON OLD PROD had a live .session in memory,
# but box is down. The LOCAL PC has watcher-session file? README says session file fallback.
import glob, os
for pat in [
    'C:/Users/Administrator/coding/telegram-channel-watcher/watcher-session*',
    'C:/Users/Administrator/coding/telegram-channel-watcher/*.session',
    'C:/Users/Administrator/coding/**/watcher-session*',
]:
    for f in glob.glob(pat, recursive=True):
        print(f, os.path.getsize(f))
print('scan done')
