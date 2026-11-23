; Cerulean's Sound Searcher Inno Setup Script
; Paths are relative to this script's folder. package.bat passes the version; build with sound.bat first.

#define MyAppName "Cerulean's Sound Searcher"
#ifndef MyAppVersion
  #define MyAppVersion "0.4.0"
#endif
#define MyAppPublisher "Cerulean Blue Software"
#define MyAppURL "https://github.com/CeruleanBlueSoftware/ceruleans-sound-searcher"
#define MyAppExeName "Cerulean's Sound Searcher.exe"
#define MyChangeLogURL "https://github.com/CeruleanBlueSoftware/ceruleans-sound-searcher/blob/main/CHANGELOG.md"

[Setup]
; Application identification
AppId={{8A8A0051-E7CE-4BC5-A109-0B04C21E2813}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases

; Installation settings: the program goes in a "program" folder inside the app's settings folder, so
; uninstalling removes the program but keeps the user's settings, index, notes and downloads.
DefaultDirName={userappdata}\Cerulean Blue Software\Cerulean's Sound Searcher\program
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest

; Installer output
OutputDir=output
OutputBaseFilename=setup ceruleans sound searcher
Compression=lzma2/max
SolidCompression=yes
InternalCompressLevel=max
WizardStyle=classic
SetupIconFile=ceruleanBlue.ico

; License and application settings
LicenseFile=LICENSE
CloseApplications=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "changelog"; Description: "View the Change Log after installation"; GroupDescription: "After installation:"; Flags: unchecked

[Files]
; Entire standalone build, with the read me, change log and licence that sound.bat puts in it
Source: "dist\sound_searcher.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Start menu shortcuts
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Read me"; Filename: "{app}\README.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Start the app after installation
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: shellexec nowait postinstall skipifsilent

[Code]
function ShellExecute(hwnd: HWND; lpOperation, lpFile, lpParameters, lpDirectory: string; nShowCmd: Integer): THandle;
  external 'ShellExecuteW@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('changelog') then
      ShellExecute(0, 'open', '{#MyChangeLogURL}', '', '', SW_SHOWNORMAL);
  end;
end;
