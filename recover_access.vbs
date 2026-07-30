' Launches the data-recovery tool with no console window - the tkinter
' dialog it opens is the visible UI. Only needed if this Windows account's
' credential store has lost the encryption key (see docs/encryption-at-rest.md) -
' not part of normal day-to-day use, so there's no desktop shortcut for this
' one, unlike run_app.vbs.
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & folder & "\venv\Scripts\pythonw.exe"" """ & folder & "\scripts\recover_access.py""", 0, False
