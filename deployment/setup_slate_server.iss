; Script to create installer for the Slate server.
; VERSION 2.0: ZERO-CONFIG DEPLOYMENT
; Features: Auto-Updater, Cleanup Old Configs, Bundled Dependencies.

#define MyAppName "Slate Server"
#define MyAppVersion "BETA 2.0.25"
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

; --- INSTALL LOCATION: User AppData (Fixes Network Drive Visibility) ---
DefaultDirName={localappdata}\{#MyAppName}
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
Type: files; Name: "{app}\pg_server.log"

[UninstallDelete]
; Same as install, preserve Database
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\pg_server.log"

[Code]
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
  ServerPathPage.Values[0] := 'X:\Extra\Slate_Central';
end;

procedure ForceKillSlateProcesses();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "Slate_Server.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "postgres.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure TryDeleteFileIfExists(const FilePath: String);
begin
  if FileExists(FilePath) then
  begin
    if DeleteFile(FilePath) then
      Log('Deleted file: ' + FilePath)
    else
      Log('Failed to delete file: ' + FilePath);
  end;
end;

procedure TryDeleteDirIfExists(const DirPath: String);
begin
  if DirExists(DirPath) then
  begin
    if DelTree(DirPath, True, True, True) then
      Log('Deleted directory: ' + DirPath)
    else
      Log('Failed to delete directory: ' + DirPath);
  end;
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    Log('Starting uninstall cleanup...');
    ForceKillSlateProcesses();
    CleanupLegacyRegistry();
  end;
end;
