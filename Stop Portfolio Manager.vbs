' Stops the background Portfolio Manager server (same as the Quit button in the app).
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }""", 0, True
sh.Popup "Portfolio Manager stopped.", 3, "Portfolio Manager", 64
