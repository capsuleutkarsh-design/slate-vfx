; Script to create installer for Slate Operations.
; VERSION 2.0: ZERO-CONFIG DEPLOYMENT
; Features: HRMS Attendance, Leaves, Onboarding, IT Inventory, DCC Licenses, Ticketing, Deployment, Users & Roles.

#define MyAppName "Slate Operations"
#define MyAppVersion "BETA 2.0.25"
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

; --- INSTALL LOCATION: User AppData ---
DefaultDirName={localappdata}\{#MyAppName}
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
  ServerPathPage.Values[0] := 'X:\Extra\Slate_Central';
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
