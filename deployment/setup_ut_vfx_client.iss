; Script to create installer for UT_VFX Production tool.
; VERSION 2.0: ZERO-CONFIG DEPLOYMENT
; Features: Auto-Updater, Cleanup Old Configs, Bundled Dependencies.

#define MyAppName "Slate"
#define MyAppVersion "BETA 2.0.22"
#define MyAppPublisher "UT Studio"
#define MyAppURL "https://github.com/capsuleutkarsh-design/slate-vfx"
#define MyAppExeName "UT_VFX_Studio.exe"
#define MyAppIconFileName "app_icon_128.ico"

; Build-path overrides (can be passed from ISCC CLI via /DName=Value)
#ifndef SourceDistDir
#define SourceDistDir "..\dist\UTVFX"
#endif
#ifndef InstallerOutputDir
#define InstallerOutputDir "..\installers"
#endif
#ifndef SetupIconPath
#define SetupIconPath "..\ut_vfx\icons\app_icon_128.ico"
#endif

[Setup]
AppId={{A4B3C2D1-E5F6-4321-8765-CLIENT000000}
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
OutputBaseFilename=setup_UT_VFX_Studio_v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

; Aesthetics
SetupIconFile={#SetupIconPath}
WizardImageFile=..\ut_vfx\icons\app_banner.bmp
WizardSmallImageFile=..\ut_vfx\icons\app_banner_small.bmp

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "client"; Description: "Client Workstation (Default)"
Name: "custom"; Description: "Custom Installation"; Flags: iscustom

[Components]
Name: "main_soft"; Description: "UT_VFX Main Software (Client)"; Types: client custom

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; --- MAIN APPLICATION COMPONENTS ---
; We install the entire shared library folder EXCEPT the server and ops components
Source: "{#SourceDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "UT_Server.exe,ut_server\*,UT_Studio_Ops.exe"
Source: "..\OpenRV\*"; DestDir: "{app}\OpenRV"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Components: main_soft
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Components: main_soft

[Registry]
; --- AUTO-STARTUP CONFIGURATION ---
; Auto-Startup configurations mapped to components
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "UTVFX"; ValueData: """{app}\{#MyAppExeName}"" --startup"; Flags: uninsdeletevalue; Components: main_soft

[Run]
; Launch the apps after installation
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Components: main_soft

[InstallDelete]
; Force cleanup of the entire directory before installing
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{commonpf}\{#MyAppName}"
Type: filesandordirs; Name: "{commonpf32}\{#MyAppName}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  CleanupDone: Boolean;
  ServerPathPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  ServerPathPage := CreateInputDirPage(wpSelectDir,
    'Select UT_Central Shared Folder', 'Where is the UT_Central shared folder located on your network?',
    'Select the network folder where the shared databases and caches will be stored, then click Next.'#13#10#13#10'For best compatibility with older VFX tools, mapping your server to a Drive Letter (like Z:\) is recommended.',
    False, 'New Folder');
  ServerPathPage.Add('Server Root Path (e.g., Z:\UT_Central or \\Server\Shared\UT_Central):');
  ServerPathPage.Values[0] := 'X:\Extra\UT_Central';
end;

procedure ForceKillSlateProcesses();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "UT_VFX_Studio.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "UTVFX.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "UTVFX_Debug.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "gatekeeper_main.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
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
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'UTVFX');
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'UTVFX_Debug');
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'UT_VFX Production');

  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\UTVFX');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\UT Studio\UT_VFX Production');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\UT_VFX Production_is1');

  RegDeleteKeyIncludingSubkeys(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\UT_VFX Production_is1');
  RegDeleteKeyIncludingSubkeys(HKLM, 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\UT_VFX Production_is1');
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
