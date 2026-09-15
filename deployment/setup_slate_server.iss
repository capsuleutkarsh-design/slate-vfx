; Script to create installer for the Slate server.
; VERSION 2.0: ZERO-CONFIG DEPLOYMENT
; Features: Auto-Updater, Cleanup Old Configs, Bundled Dependencies.

#define MyAppName "Slate Server"
#define MyAppVersion "BETA 2.0.27"
#define MyAppPublisher "UT Studio"
#define MyAppURL "https://github.com/capsuleutkarsh-design/slate-vfx"
#define MyAppExeName "Slate_Server.exe"
#define MyAppIconFileName "server_icon.ico"

; Build-path overrides (can be passed from ISCC CLI via /DName=Value)
#ifndef SourceDistDir
#define SourceDistDir "..\dist"
#endif
#ifndef InstallerOutputDir
#define InstallerOutputDir "..\installers"
#endif
#ifndef SetupIconPath
#define SetupIconPath "..\slate\icons\server_icon.ico"
#endif

[Setup]
AppId={{A4B3C2D1-E5F6-4321-8765-SERVER000000}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; --- INSTALL LOCATION ---
; Under the per-user Programs folder, so no administrator rights are needed
; and a network-drive install is never attempted. Not {localappdata}\Slate:
; that folder is where the software keeps its settings and caches, and a
; program folder that is also the data folder is wiped by every upgrade.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes

; Helper Options
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
OutputDir={#InstallerOutputDir}
OutputBaseFilename=setup_{#MyAppName}_v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

; Aesthetics
SetupIconFile={#SetupIconPath}
WizardImageFile=..\slate\icons\app_banner.bmp
WizardSmallImageFile=..\slate\icons\app_banner_small.bmp

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "server"; Description: "Server Node (Default)"
Name: "custom"; Description: "Custom Installation"; Flags: iscustom

[Components]
Name: "central_server"; Description: "Slate Server (master node and database)"; Types: server custom

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; --- MAIN APPLICATION COMPONENTS ---
; We install the Slate_Server executable
Source: "{#SourceDistDir}\Slate_Server.exe"; DestDir: "{app}"; Flags: ignoreversion
; The sidecar that applies an update after the server has exited. The engine
; looks for it beside the running executable, and without it an update on the
; server machine downloads, verifies, and then fails to install.
Source: "{#SourceDistDir}\Slate\SlateUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Components: central_server
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Components: central_server

[Registry]
; --- AUTO-STARTUP CONFIGURATION ---
; Auto-Startup configurations mapped to components
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Slate Server"; ValueData: """{app}\{#MyAppExeName}"" --startup"; Flags: uninsdeletevalue; Components: central_server

[Run]
; Launch the apps after installation
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Slate Server"; Flags: nowait postinstall skipifsilent; Components: central_server

[InstallDelete]
; Clean up previous server executable and logs, but KEEP Database
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\SlateUpdater.exe"
Type: files; Name: "{app}\pg_server.log"

[UninstallDelete]
; Same as install, preserve Database
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\SlateUpdater.exe"
Type: files; Name: "{app}\pg_server.log"

[Code]
#include "inc_slate_data.iss"

var
  CleanupDone: Boolean;
  ServerPathPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  ServerPathPage := CreateInputDirPage(wpSelectDir,
    'Select Slate_Central Shared Folder', 'Where is the Slate_Central shared folder located on your network?',
    'Select the network folder where the shared databases and caches will be stored, then click Next.'#13#10#13#10'For best compatibility with older VFX tools, mapping your server to a Drive Letter (like Z:\) is recommended.',
    False, 'New Folder');
  ServerPathPage.Add('Server Root Path (e.g., Z:\Slate_Central or \\Server\Shared\Slate_Central):');
  // Deliberately blank. A pre-filled drive letter is one studio's
  // answer to a question every studio answers differently, and it
  // reads as a setting rather than as a guess.
  ServerPathPage.Values[0] := '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ServerPathPage.ID then
    Result := StudioFolderAccepted(Trim(ServerPathPage.Values[0]));
end;

procedure ForceKillSlateProcesses();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "Slate_Server.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "postgres.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // The pooler is a separate process holding a separate port, and one left over
  // from an older install keeps serving its stale configuration to every client.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "pgbouncer.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CleanupLegacyRegistry();
begin
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Slate');
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Slate_Debug');
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Slate Production');

  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Slate');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\UT Studio\Slate Production');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Slate Production_is1');

  RegDeleteKeyIncludingSubkeys(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Slate Production_is1');
  RegDeleteKeyIncludingSubkeys(HKLM, 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Slate Production_is1');
end;

procedure RunPreInstallCleanup();
begin
  if CleanupDone then
    exit;
  CleanupDone := True;

  Log('Starting aggressive pre-install cleanup...');
  ForceKillSlateProcesses();

  TryDeleteDirIfExists(ExpandConstant('{localappdata}\{#MyAppName}'));
  TryDeleteDirIfExists(ExpandConstant('{pf}\{#MyAppName}'));
  TryDeleteDirIfExists(ExpandConstant('{pf32}\{#MyAppName}'));

  CleanupLegacyRegistry();
  Log('Pre-install cleanup completed.');
end;

// Cleanup old configs/processes to ensure fresh install and write config
procedure CurStepChanged(CurStep: TSetupStep);
var
  ServerRoot: String;
  ConfigContent: String;
begin
  if CurStep = ssInstall then
    RunPreInstallCleanup();
    
  if CurStep = ssPostInstall then
  begin
    ServerRoot := ServerPathPage.Values[0];
    StringChangeEx(ServerRoot, '\', '\\', True);
    ConfigContent := '{' + #13#10 + '    "SERVER_ROOT": "' + ServerRoot + '"' + #13#10 + '}';
    SaveStringToFile(ExpandConstant('{app}\client_config.json'), ConfigContent, False);
  end;
end;

function JsonStringValue(const FileName: String; const Key: String): String;
var
  Raw: AnsiString;
  Text: String;
  P: Integer;
begin
  // Enough JSON to read one string setting out of the server's config. There is
  // no parser here and there does not need to be: the alternative is deleting a
  // data directory the file was never consulted about.
  Result := '';
  if not FileExists(FileName) then exit;
  if not LoadStringFromFile(FileName, Raw) then exit;

  Text := String(Raw);
  P := Pos('"' + Key + '"', Text);
  if P = 0 then exit;
  Text := Copy(Text, P + Length(Key) + 2, Length(Text));

  P := Pos(':', Text);
  if P = 0 then exit;
  Text := Copy(Text, P + 1, Length(Text));

  P := Pos('"', Text);
  if P = 0 then exit;
  Text := Copy(Text, P + 1, Length(Text));

  P := Pos('"', Text);
  if P = 0 then exit;
  Result := Trim(Copy(Text, 1, P - 1));

  // JSON escapes its separators; Windows wants them back.
  StringChangeEx(Result, '\\', '\', True);
  StringChangeEx(Result, '/', '\', True);
end;

function IsInsideCentralFolder(const Path: String): Boolean;
var
  Central: String;
begin
  Central := RemoveBackslashUnlessRoot(ExpandConstant('{localappdata}\Slate_Central'));
  Result := (Length(Path) >= Length(Central)) and
            (CompareText(Copy(Path, 1, Length(Central)), Central) = 0);
end;

procedure PurgeServerData();
var
  ConfiguredPath: String;
begin
  Log('Removing the Slate Server database, backups and settings...');

  // Nothing under the data directory can be deleted while PostgreSQL has it
  // open, and a half-deleted cluster is worse than an untouched one - the next
  // start finds enough of it to try, and fails in a way nobody can read.
  ForceKillSlateProcesses();
  Sleep(1500);

  ConfiguredPath := JsonStringValue(
    ExpandConstant('{localappdata}\Slate_Central\slate_server_config.json'), 'db_path');

  TryDeleteDirIfExists(ExpandConstant('{localappdata}\Slate_Central'));
  PurgeDirectory(ExpandConstant('{app}'));
  PurgeDirectory(ExpandConstant('{localappdata}\{#MyAppName}'));

  // The data directory can be anywhere - a second drive, a share - and the one
  // the server was actually told to use is the one that matters. It is asked
  // about separately because it is the only thing here that might be the
  // studio's own storage rather than this machine's.
  if (ConfiguredPath <> '') and DirExists(ConfiguredPath)
     and (not IsInsideCentralFolder(ConfiguredPath)) then
  begin
    if UninstallSilent or HasCmdLineSwitch('PURGEDATA') then
      TryDeleteDirIfExists(ConfiguredPath)
    else if MsgBox('The server was also configured to keep its database here:'
                   + #13#10#13#10 + ConfiguredPath + #13#10#13#10
                   + 'This folder is outside the Slate Server folders, so it may '
                   + 'be shared studio storage rather than this machine''s own. '
                   + 'Delete it as well?' + #13#10#13#10
                   + 'If you are not certain, choose No and look at it first.',
                   mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      TryDeleteDirIfExists(ConfiguredPath)
    else
      Log('Left the configured data directory in place: ' + ConfiguredPath);
  end;

  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Slate Server');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    Log('Starting uninstall cleanup...');
    ForceKillSlateProcesses();
    CleanupLegacyRegistry();
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    if ShouldRemoveData('Slate Server',
      'This deletes THE STUDIO DATABASE and everything around it:' + #13#10 +
      '  -  every user, attendance record, leave request, ticket,' + #13#10 +
      '     asset, licence and shot the studio has entered' + #13#10 +
      '  -  every database backup taken on this machine' + #13#10 +
      '  -  the server settings, the connection pool and the logs' + #13#10#13#10 +
      'This is the studio''s data, not just this program''s settings, and ' +
      'there is no way back from it.' + #13#10#13#10 +
      'Say Yes only if you are starting over deliberately - a database left ' +
      'behind by a bad install is picked straight back up by the next one.') then
    begin
      PurgeServerData();
      ReportPurgeFailures();
    end
    else
      Log('Keeping the database, the backups and the server settings.');
  end;
end;
