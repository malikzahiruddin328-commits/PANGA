; Inno Setup script for Panga (native-packaging Phase 2 - see
; docs/native-packaging-scope.md). Compile with Inno Setup 6's ISCC after
; building the PyInstaller bundle first - see
; docs/native-packaging-phase2-build.md for the full sequence.
;
; Per-user install ({localappdata}\Programs\Panga, PrivilegesRequired=lowest):
; no admin/UAC prompt, and it keeps app binaries + data + config all under
; one directory a normal user account can write to - important because
; every src/ module's PROJECT_ROOT (see docs/native-packaging-scope.md,
; "Artifact layout") resolves to the exe's own directory, and data/ needs
; to be writable there without elevation.
;
; AppId is a fixed GUID, not the app name - Inno uses it (not AppName) to
; recognize "this is the same app, treat it as an upgrade" across versions.
; Keep this exact GUID in every future release. feature/update-mechanism
; needs this same GUID if it ever calls Inno Setup's own upgrade path
; instead of doing its own file replacement - coordinate before merge (see
; docs/native-packaging-scope.md's "out of scope" section).

#define MyAppId "{29D8F805-F3A8-455D-AC29-482DDED84C50}"
#define MyAppName "Panga"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Panga"
#define MyAppExeName "Panga.exe"
; Set by build tooling relative to this .iss file's own location
; (packaging/), i.e. run from the repo root's packaging/ directory or pass
; /D on the ISCC command line to override - see the build doc.
#ifndef BuildDistDir
  #define BuildDistDir "..\build_dist\Panga"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer_output
OutputBaseFilename=Panga-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; No code-signing cert yet (see docs/native-packaging-scope.md's "Code
; signing" note) - SmartScreen will warn on first run until Store
; distribution or a direct-download cert makes that a real line item.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The whole PyInstaller onedir output (Panga.exe + _internal\) - built
; separately, see docs/native-packaging-phase2-build.md.
Source: "{#BuildDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; Sibling directories PyInstaller doesn't produce (its own COLLECT step
; nests datas under _internal\, which the app's PROJECT_ROOT-relative path
; logic doesn't look in - see docs/native-packaging-scope.md). Copied here
; as real top-level {app}\config, {app}\.streamlit instead.
Source: "..\config\*"; DestDir: "{app}\config"; Flags: recursesubdirs createallsubdirs
Source: "..\.streamlit\*"; DestDir: "{app}\.streamlit"; Flags: recursesubdirs createallsubdirs
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion

; Shipped as-is, unmodified (native-packaging Phase 1 output) - the
; uninstaller runs this to remove the 3 Windows Task Scheduler entries; see
; [Code] below. Task removal is purely name-based (Unregister-ScheduledTask
; "Panga-*"), no python.exe/venv path assumption, so it works unmodified
; against a packaged install.
Source: "..\scripts\uninstall_scheduled_tasks.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
; The packaged-install counterpart of Phase 1's install_scheduled_tasks.ps1
; (that one targets venv\Scripts\python.exe, which doesn't exist in a
; packaged install - see packaging/install_scheduled_tasks_packaged.ps1's
; own header comment for why this is a separate file rather than an edit
; to the original).
Source: "install_scheduled_tasks_packaged.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Register background tasks"; Filename: "powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install_scheduled_tasks_packaged.ps1"""; \
    WorkingDir: "{app}"; \
    Comment: "Run once after setting up .env and Gmail credentials - see docs\native-packaging-task-scheduler.md in the source repo"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Runs the uninstall helper (data-retention prompt, backup offer, license
// device-release call - see packaging/panga_launcher.py's
// cmd_uninstall_helper and docs/native-packaging-scope.md's "Uninstall
// behavior") and removes the Task Scheduler entries, both BEFORE Inno
// deletes any files - usUninstall fires before file removal, usPostUninstall
// fires after (Inno Setup's own uninstall-step ordering). The helper needs
// data\, config\, and the OS keyring entry to still exist when it runs, so
// this can't be usPostUninstall.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--uninstall-helper', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
    Exec('powershell.exe',
      '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\uninstall_scheduled_tasks.ps1') + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
