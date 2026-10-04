; Universal Search - Inno Setup 6 script (optional alternative to install.ps1).
;
; HONESTY NOTE, PHASE 049. This script is STILL not compiled: ISCC.exe is not
; installed on this machine, and installing it needs network access and the
; user's agreement. What phase 049 did was fix the four defects that made it
; uncompilable as written, verify each one by reading rather than by running,
; and record that the compile still has not happened. Nothing here may be read
; as "this builds" -- it is "this should now build, and nobody has watched".
;
; The tested, manifest-based installer of record remains install.ps1, and it is
; the one this repository executes in CI and in
; tests/test_release.py::test_install_reinstall_uninstall_roundtrip.
;
; Compile with Inno Setup 6 when available:
;
;     ISCC.exe packaging\installer.iss
;
; Note that `Spanish.isl` is NOT part of stock Inno Setup 6 -- verified, winget
; offers JRSoftware.InnoSetup 6.7.3, English only. The Spanish language entry
; below therefore needs the official translations package, and without it the
; compile fails on that one line.
;
; User data (index database, config.json, logs) lives in
; {localappdata}\Universal Search and is NEVER written under {app}, so
; uninstalling removes only application files - the same distinction the
; PowerShell installer makes through install-manifest.json.

#define MyAppName "Universal Search"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Universal Search contributors"
#define MyAppExeName "UniversalSearch.exe"

[Setup]
AppId={{7C1F5A2E-9B4D-4E6A-8C3F-2D5B9A0E1F47}
AppName={#MyAppName}
; Phase 049: without this, setup.exe showed a blank version in Add/Remove
; Programs while the installer filename said 2.0.0.
VersionInfoVersion={#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\UniversalSearch
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
; Phase 049, defect 4: an upgrade could overwrite a running GUI. install.ps1
; kills the process by name before copying; this script had no equivalent.
CloseApplications=yes
RestartApplications=no
; Phase 049. Inno Setup resolves relative paths against the directory holding
; this script, which is `packaging\`, not the repository root. `OutputDir=dist`
; therefore meant `packaging\dist\`, and the [Files] entry below meant
; `packaging\dist\UniversalSearch\*` -- a directory that does not exist. The
; `SetupIconFile` line sits beside them and *does* resolve, which is exactly
; why the mistake survived a read-through: one line in a block of three works.
SourceDir=..\
OutputDir=..\dist
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
; Phase 049: the only path that deletes user data, unchecked by default.
Name: "purgedata"; Description: "Also delete your index, settings and logs"; GroupDescription: "Removing:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\dist\UniversalSearch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Phase 049, defect 3: an upgrade left the previous build's files behind.
; `ignoreversion` skips files whose version is unchanged, which is right, but a
; module the new build *removed* matches nothing in [Files] and is never
; touched, so `_internal\` accumulated the union of every build ever made.
; install.ps1 copies over the top and does not remove either, so both scripts
; share this and here is where it is fixed for Inno.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

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

; Phase 049: install.ps1 preserves user data by default and deletes it only
; under an explicit `-PurgeData`, printing both outcomes. Inno Setup could not
; express that choice at all -- there was no [UninstallDelete] entry, so the
; behaviour was correct by accident rather than by decision.
[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Universal Search"; Tasks: "purgedata"

; Only {app} is removed by default. {localappdata}\Universal Search (index,
; config, logs) survives uninstalling unless the user ticks the task or deletes
; it afterwards. Note that install.ps1 and this uninstaller are NOT
; complementary: this script writes no install-manifest.json, so running one
; after the other leaves residue. install.ps1 remains the tested installer.
