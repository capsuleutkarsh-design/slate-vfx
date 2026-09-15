; Script to create installer for Slate Operations.
; VERSION 2.0: ZERO-CONFIG DEPLOYMENT
; Features: HRMS Attendance, Leaves, Onboarding, IT Inventory, DCC Licenses, Ticketing, Deployment, Users & Roles.

#define MyAppName "Slate Operations"
#define MyAppVersion "BETA 2.0.27"
#define MyAppPublisher "UT Studio"
#define MyAppURL "https://github.com/capsuleutkarsh-design/slate-vfx"
#define MyAppExeName "Slate_Ops.exe"
#define MyAppIconFileName "app_icon_128.ico"

; Build-path overrides (can be passed from ISCC CLI via /DName=Value)
#ifndef SourceDistDir
#define SourceDistDir "..\dist\Slate"
#endif
#ifndef InstallerOutputDir
#define InstallerOutputDir "..\installers"
#endif
#ifndef SetupIconPath
#define SetupIconPath "..\slate\icons\app_icon_128.ico"
#endif

[Setup]
AppId={{B5C4D3E2-F6A7-4432-9876-OPS00000000}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; --- INSTALL LOCATION ---
; Under the per-user Programs folder, beside Slate Studio, and away from
; {localappdata}\Slate where the shared settings live.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes

; Helper Options
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
OutputDir={#InstallerOutputDir}
OutputBaseFilename=setup_Slate_Ops_v{#MyAppVersion}
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
Name: "ops"; Description: "Studio Operations Workstation (Default)"
Name: "custom"; Description: "Custom Installation"; Flags: iscustom

[Components]
Name: "main_ops"; Description: "Slate Operations Software"; Types: ops custom

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Install the shared runtime folder EXCEPT server and VFX executables
Source: "{#SourceDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "Slate_Server.exe,slate_server\*,Slate_Studio.exe,OpenRV\*,OpenRV"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Components: main_ops
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Components: main_ops

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "UTStudioOps"; ValueData: """{app}\{#MyAppExeName}"" --startup"; Flags: uninsdeletevalue; Components: main_ops

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Components: main_ops

[InstallDelete]
Type: filesandordirs; Name: "{app}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
#include "inc_slate_data.iss"

var
  CleanupDone: Boolean;
  ServerPathPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  ServerPathPage := CreateInputDirPage(wpSelectDir,
    'Select Slate_Central Shared Folder', 'Where is the Slate_Central shared folder located on your network?',
    'Select the network folder where the shared databases and caches will be stored, then click Next.'#13#10#13#10'For best compatibility with studio tools, mapping your server to a Drive Letter (like Z:\) is recommended.',
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
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "Slate_Ops.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM "Slate.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ForceKillSlateProcesses();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, LocalConfigPath, ServerRootVal: String;
  JsonContent: String;
begin
  if CurStep = ssInstall then
  begin
    // Where this used to install. That folder held only program files, so
    // whatever an older build left there can go whole.
    TryDeleteDirIfExists(ExpandConstant('{localappdata}\{#MyAppName}'));
  end;

  if CurStep = ssPostInstall then
  begin
    ServerRootVal := ServerPathPage.Values[0];
    StringChangeEx(ServerRootVal, '\', '/', True);

    ConfigPath := ExpandConstant('{app}\client_config.json');
    LocalConfigPath := ExpandConstant('{localappdata}\Slate\client_config.json');

    JsonContent := '{' + #13#10 +
      '  "SERVER_ROOT": "' + ServerRootVal + '"' + #13#10 +
      '}';

    SaveStringToFile(ConfigPath, JsonContent, False);
    ForceDirectories(ExpandConstant('{localappdata}\Slate'));
    SaveStringToFile(LocalConfigPath, JsonContent, False);
  end;
end;

procedure PurgeOpsData();
begin
  Log('Removing Slate Operations settings and data from this workstation...');

  PurgeDirectory(ExpandConstant('{app}'));
  // The previous program folder, if an older build is still there.
  TryDeleteDirIfExists(ExpandConstant('{localappdata}\{#MyAppName}'));

  // Setup writes the server path to the shared Slate folder as well as its own,
  // so a purge that skipped it would leave behind the very file that sends the
  // next install back to the wrong server.
  TryDeleteFileIfExists(ExpandConstant('{localappdata}\Slate\client_config.json'));
  TryDeleteFileIfExists(ExpandConstant('{localappdata}\Slate\config.json'));

  // Regenerated on demand, and stale copies of them are a large part of why a
  // reinstall can look like it changed nothing.
  TryDeleteDirIfExists(ExpandConstant('{localappdata}\Slate\Cache'));
  TryDeleteDirIfExists(ExpandConstant('{localappdata}\Slate\Logs'));
  TryDeleteDirIfExists(ExpandConstant('{localappdata}\Slate\logs'));
  TryDeleteDirIfExists(ExpandConstant('{localappdata}\Slate\Temp'));
  TryDeleteDirIfExists(ExpandConstant('{localappdata}\Slate\telemetry'));
  TryDeleteDirIfExists(ExpandConstant('{%USERPROFILE}\RuntimeData\Slate'));

  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'UTStudioOps');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\UTStudio\Slate');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\UT_Software\Slate');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    ForceKillSlateProcesses();

  // After Setup has removed what it installed, so what is left here is only
  // what the software wrote for itself.
  if CurUninstallStep = usPostUninstall then
  begin
    if ShouldRemoveData('Slate Operations',
      'This deletes, on this computer only:' + #13#10 +
      '  -  the server path and login settings' + #13#10 +
      '  -  cached data, temporary files and local logs' + #13#10#13#10 +
      'The shared Slate settings under AppData\Local\Slate go with them, so a ' +
      'Slate client on this machine will ask for the server path again.' + #13#10#13#10 +
      'The studio database on the server is NOT touched. No attendance, leave, ' +
      'ticket or inventory record is removed.') then
    begin
      PurgeOpsData();
      ReportPurgeFailures();
    end
    else
      Log('Keeping the Slate Operations settings and data on this workstation.');
  end;
end;
