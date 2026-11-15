; Inno Setup script for the Windows installer. Run through packaging\build_windows.ps1.
; Installs for the current user only (no admin rights needed), starts Grabbit at login,
; and removes everything, including the extension folder, when uninstalled.

#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif

[Setup]
AppId={{6F2C4A8E-3B1D-4E7A-9C55-0D8E2B7F41A3}
AppName=Grabbit
AppVersion={#AppVersion}
AppVerName=Grabbit {#AppVersion}
AppPublisher=Cubehole
AppPublisherURL=https://cubehole.com
AppSupportURL=https://github.com/naved1197/grabbit/issues
AppUpdatesURL=https://github.com/naved1197/grabbit/releases/latest
AppCopyright=Copyright (c) 2026 Cubehole
DefaultDirName={localappdata}\Programs\Grabbit
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Grabbit-Setup-{#AppVersion}
SetupIconFile=icons\Grabbit.ico
UninstallDisplayIcon={app}\Grabbit.exe
UninstallDisplayName=Grabbit
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Close a running Grabbit before updating its files.
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Grabbit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Grabbit"; Filename: "{app}\Grabbit.exe"
Name: "{userdesktop}\Grabbit"; Filename: "{app}\Grabbit.exe"; Tasks: desktopicon

[Registry]
; Start Grabbit (in the background) when the user logs in. The tray menu can turn this off.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Grabbit"; \
  ValueData: """{app}\Grabbit.exe"" --background"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Grabbit.exe"; Description: "Open Grabbit and set up the browser extension"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Grabbit.exe"; Flags: runhidden; RunOnceId: "StopGrabbit"

[UninstallDelete]
; The extension folder, staged downloads, yt-dlp updates and logs.
Type: filesandordirs; Name: "{localappdata}\Grabbit"
