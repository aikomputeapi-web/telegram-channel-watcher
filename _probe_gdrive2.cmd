@echo off
rem Broad rclone search: look for any credentials/db backups on the first few remotes
for %%R in (gdrive1 gdrive2 gdrive3 gdrive4 gdrive5) do (
  echo === %%R root ===
  rclone lsf "%%R:" --max-depth 1 2>nul
)
