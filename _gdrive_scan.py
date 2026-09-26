#!/usr/bin/env python3
"""Search remotes via rclone for cred-search DB backups (hashed folder names noted).
Targets: gdrive4 root files (some listed as hash names = encrypted-teamdrives? print sizes)
Print name+size for every file matching .db or .gz or >100MB in gdrive4 root.
"""
import subprocess, sys

RCLONE = r'rclone'
remote = 'gdrive4:tih1pjc2au59t65h3uqca75ivo'
out = subprocess.run([RCLONE, 'lsf', remote, '--max-depth', '1', '--format', 'sp', '--files-only'],
                     capture_output=True, text=True)
print(out.stdout[:4000])
print('STDERR:', out.stderr[:500])
