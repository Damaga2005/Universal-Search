; Universal Search - Inno Setup 6 script (optional alternative to install.ps1).
;
; HONESTY NOTE: Inno Setup (ISCC.exe) is NOT installed on the build machine,
; so this script is provided as source and has NOT been compiled/verified
; here. The tested, manifest-based installer of record is install.ps1
; (tests/test_release.py::test_install_reinstall_uninstall_roundtrip).
; Compile with Inno Setup 6 when available:
;
;     ISCC.exe packaging\installer.iss
;
; User data (index database, config.json, logs) lives in
; {localappdata}\Universal Search and is NEVER written under {app}, so
; uninstalling removes only application files - the same distinction the
; PowerShell installer makes through install-manifest.json.

#define MyAppName "Universal Search"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Universal Search contributors"
#define MyAppExeName "UniversalSearch.exe"

[Setup]
AppId={{7C1F5A2E-9B4D-4E6A-8C3F-2D5B9A0E1F47}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\UniversalSearch
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=UniversalSearch-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=universal_search.ico
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "backgroundindexer"; Description: "Start the background indexer with Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "dist\UniversalSearch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; The startup entry is registered through the application's own tested
; command so the recorded path always points at the installed executable.
Filename: "{app}\universal-search.exe"; Parameters: "indexer autostart on"; Description: "Start the background indexer with Windows"; Flags: postinstall runasoriginaluser; Tasks: backgroundindexer
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Best effort: stop the worker and remove its autostart entry before the
; binaries disappear (same order the PowerShell uninstaller uses).
Filename: "{app}\universal-search.exe"; Parameters: "indexer stop"; RunOnceId: "StopIndexer"; Flags: runhidden
Filename: "{app}\universal-search.exe"; Parameters: "indexer autostart off"; RunOnceId: "AutostartOff"; Flags: runhidden

; Only {app} is removed. {localappdata}\Universal Search (index, config,
; logs) survives uninstalling unless the user deletes it manually.
