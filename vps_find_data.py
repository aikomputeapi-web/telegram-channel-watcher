import os, glob

# Look for large data files that might contain credential data
patterns = [
    '/tmp/*.json', '/tmp/*.csv', '/tmp/*.txt', '/tmp/*.tsv', '/tmp/*.zip', '/tmp/*.gz',
    '/home/**/*.json', '/home/**/*.csv', '/home/**/*.txt', '/home/**/*.zip', '/home/**/*.gz',
]
for p in patterns:
    for f in glob.glob(p, recursive=True):
        try:
            sz = os.path.getsize(f)
            if sz > 1_000_000:  # Only files > 1MB
                print(f"{f}  ({sz/1024/1024:.1f}MB)")
        except:
            pass
