@echo off
rem Probe each gdrive remote for tg-watcher-backups folder
for %%R in (gdrive1 gdrive2 gdrive3 gdrive4 gdrive5 gdrive6 gdrive7 gdrive8 gdrive9 gdrive10 gdrive11 gdrive12 gdrive13 gdrive14 gdrive15) do (
  echo === %%R ===
  rclone lsf "%%R:tg-watcher-backups" --max-depth 1 2>nul
)
