import subprocess, time

pid = int(open('/root/swap.pid').read().strip())
print(f"watching pid {pid}")
while True:
    r = subprocess.run(['kill', '-0', str(pid)], capture_output=True)
    if r.returncode != 0:
        break
    time.sleep(15)
print("=== swap process exited ===")
try:
    print(open('/root/swap2.log').read())
except Exception as e:
    print(f"(log read failed: {e})")
