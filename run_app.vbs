' Launches run_app.bat minimized instead of fully hidden, so there's still
' a window in the taskbar you can close if you want to stop the app -
' otherwise there'd be no easy way to shut it down without Task Manager.
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & folder & "\run_app.bat""", 7, False
