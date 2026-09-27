' Portfolio Manager launcher (Windows)
' - First run: opens run.bat visibly so you can watch the one-time setup.
' - Afterwards: starts the server silently in the background (no console window)
'   and opens the app in its own window. If an older version is running, it is
'   restarted automatically so updates take effect.
Option Explicit

Const PORT = 8000
Dim sh, fso, root, py, url, apiUrl, want, cfg, i

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
py = root & "\.venv\Scripts\python.exe"
url = "http://localhost:" & PORT
apiUrl = "http://127.0.0.1:" & PORT & "/api/config"

' ---- one-time setup
If Not fso.FileExists(py) Then
    sh.Run """" & root & "\run.bat""", 1, False
    WScript.Quit
End If

' ---- which version should be running?
want = ""
If fso.FileExists(root & "\VERSION") Then
    want = fso.OpenTextFile(root & "\VERSION").ReadAll
    want = Trim(Replace(Replace(want, vbCr, ""), vbLf, ""))
End If

cfg = HttpGet(apiUrl)
If cfg <> "" And InStr(cfg, """version"":""" & want & """") = 0 Then
    StopServer                      ' an older build is running - replace it
    WScript.Sleep 1500
    cfg = ""
End If

' ---- start the server in the background if needed
If cfg = "" Then
    If Not fso.FolderExists(root & "\data") Then fso.CreateFolder root & "\data"
    ' make sure newly added packages are installed (quiet, hidden)
    sh.Run """" & py & """ -m pip install -q --disable-pip-version-check -r requirements.txt", 0, True
    sh.Run "cmd /c """"" & py & """ -m uvicorn app.main:app --host 127.0.0.1 --port " & PORT & _
           " > ""data\server.log"" 2>&1""", 0, False
    For i = 1 To 120
        WScript.Sleep 500
        cfg = HttpGet(apiUrl)
        If cfg <> "" Then Exit For
    Next
    If cfg = "" Then
        MsgBox "Portfolio Manager could not start." & vbCrLf & vbCrLf & _
               "Details are in:" & vbCrLf & root & "\data\server.log", vbExclamation, "Portfolio Manager"
        WScript.Quit 1
    End If
End If

OpenAppWindow url
WScript.Quit 0

' ======================================================================
Sub OpenAppWindow(u)
    Dim edge, chrome
    edge = AppPath("msedge.exe")
    chrome = AppPath("chrome.exe")
    If edge <> "" Then
        sh.Run """" & edge & """ --app=" & u & " --window-size=1440,920", 1, False
    ElseIf chrome <> "" Then
        sh.Run """" & chrome & """ --app=" & u & " --window-size=1440,920", 1, False
    Else
        sh.Run u, 1, False
    End If
End Sub

Function AppPath(exe)
    Dim hive, v
    AppPath = ""
    On Error Resume Next
    For Each hive In Array("HKCU", "HKLM")
        v = ""
        v = sh.RegRead(hive & "\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" & exe & "\")
        If Err.Number = 0 And v <> "" Then
            If fso.FileExists(Replace(v, """", "")) Then AppPath = Replace(v, """", "") : Exit Function
        End If
        Err.Clear
    Next
    On Error GoTo 0
End Function

Function HttpGet(u)
    Dim x
    HttpGet = ""
    On Error Resume Next
    Set x = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    x.setTimeouts 1000, 1000, 3000, 3000
    x.Open "GET", u, False
    x.Send
    If Err.Number = 0 Then
        If x.Status = 200 Then HttpGet = x.responseText
    End If
    Err.Clear
    On Error GoTo 0
End Function

Sub StopServer()
    sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""Get-NetTCPConnection -LocalPort " & PORT & _
           " -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }""", 0, True
End Sub
