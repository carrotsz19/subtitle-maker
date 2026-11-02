; Subtitle-Maker-Setup.exe: the file people download from a release and double-click.
;
; It has the program's files inside it, so it downloads no scripts (a .bat that fetched and ran
; install.ps1 looked like a malware dropper to antivirus). It shows no wizard of its own: it
; unpacks the files to a temporary folder and runs install.ps1 from there in the Veyra installer
; window, which copies them into %USERPROFILE%\Subtitle Maker and installs Python and its packages.
; Uninstalling stays the app's own (Settings > Apps > Subtitle Maker, made by windows-app.ps1).
;
; The Release workflow builds it, with the files of the commit it releases in build\app:
;     ISCC /DVersion=0.2.3 /DSha=<40-character commit> installer\SubtitleMaker.iss

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Sha
  #define Sha "0000000000000000000000000000000000000000"
#endif

[Setup]
AppId=Veyra.SubtitleMaker.Setup
AppName=Subtitle Maker
AppVersion={#Version}
AppVerName=Subtitle Maker {#Version}
AppPublisher=Veyra
AppPublisherURL=https://github.com/carrotsz19/subtitle-maker
AppSupportURL=https://github.com/carrotsz19/subtitle-maker/issues
AppCopyright=Veyra
VersionInfoVersion={#Version}
VersionInfoCompany=Veyra
VersionInfoDescription=Subtitle Maker Setup
VersionInfoProductName=Subtitle Maker
VersionInfoProductVersion={#Version}
; Everything happens in the Veyra window, for this user only, with no admin prompt
CreateAppDir=no
Uninstallable=no
CreateUninstallRegKey=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ShowLanguageDialog=no
SetupIconFile=..\ui\icon.ico
OutputDir=..\dist
OutputBaseFilename=Subtitle-Maker-Setup
Compression=lzma2/max
SolidCompression=yes

[Files]
; Unpacked by the code below (dontcopy), not by a wizard
Source: "..\build\app\*"; DestDir: "{tmp}\app"; Flags: dontcopy recursesubdirs createallsubdirs

[Code]
function SetEnvironmentVariable(Name: String; Value: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
var
  App: String;
  Code: Integer;
begin
  // No wizard: the Veyra window (install.ps1) is the installer. Returning False ends this
  // program once that window has closed; the temporary folder goes with it.
  Result := False;
  ExtractTemporaryFiles('{tmp}\app\*');
  App := ExpandConstant('{tmp}\app');
  // install.ps1 reads these; powershell.exe gets them from this process
  SetEnvironmentVariable('SUBTITLE_MAKER_SOURCE', App);
  SetEnvironmentVariable('SUBTITLE_MAKER_SHA', '{#Sha}');
  SetEnvironmentVariable('SUBTITLE_MAKER_GUI', '1');
  if not Exec(ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -ExecutionPolicy Bypass -File "' + App + '\install.ps1"',
      App, SW_HIDE, ewWaitUntilTerminated, Code) then
    MsgBox('Subtitle Maker''s installer couldn''t start (' + SysErrorMessage(Code) + ').', mbError, MB_OK);
end;
