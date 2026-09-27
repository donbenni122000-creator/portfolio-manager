' Creates "Portfolio Manager" shortcuts on the Desktop and in the Start menu.
' Safe to run again (it just refreshes the shortcuts).
Option Explicit
Dim sh, fso, root, loc, lnk, made, logf

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
made = ""

For Each loc In Array(sh.SpecialFolders("Desktop"), sh.SpecialFolders("Programs"))
    If loc <> "" And fso.FolderExists(loc) Then
        Set lnk = sh.CreateShortcut(loc & "\Portfolio Manager.lnk")
        lnk.TargetPath = sh.ExpandEnvironmentStrings("%WINDIR%\System32\wscript.exe")
        lnk.Arguments = """" & root & "\Portfolio Manager.vbs"""
        lnk.WorkingDirectory = root
        lnk.IconLocation = root & "\app\static\icon.ico,0"
        lnk.Description = "Portfolio Manager - client portfolios, stock research and paper trading"
        lnk.Save
        made = made & loc & "\Portfolio Manager.lnk" & vbCrLf
    End If
Next

Set logf = fso.CreateTextFile(root & "\install.log", True)
logf.WriteLine "Installed " & Now
logf.Write made
logf.Close

sh.Popup "Portfolio Manager shortcuts created:" & vbCrLf & vbCrLf & made, 6, "Portfolio Manager", 64
