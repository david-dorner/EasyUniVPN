#define MyAppName "EasyUniVPN"
; Version comes from the VERSION file at the repository root - build.ps1 passes
; it as /DMyAppVersion on every real build (local and CI). The placeholder
; below is only ever used when compiling this script directly with bare ISCC,
; and is deliberately not a real version so such a build is recognizable.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "EasyUniVPN"
#define MyAppURL "https://github.com/david-dorner/EasyUniVPN"
; Config-format version this app understands - keep in sync with
; CONFIG_VERSION in cli\src\common\app_config.py. Used to warn when
; downgrading to a version that may not read the currently saved settings.
#define SupportedConfigVersion 1
#define MyAppExeName "EasyUniVPNCli.exe"
#define MyAppLauncherExeName "EasyUniVPNLauncher.exe"
#define MyAppTrayExeName "EasyUniVPN.exe"

[Setup]
AppId={{F3A9C721-5B8E-4D02-A61F-9E3C82B47D50}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Always show the directory page and reset task checkboxes to defaults on every
; run - prevents the "why did the page disappear?" confusion on re-installs.
DisableDirPage=no
UsePreviousTasks=no
OutputDir=..\dist
OutputBaseFilename=EasyUniVPNSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; Kill running tray/launcher/VPN before overwriting - without this, Windows locks
; EasyUniVPN.exe while it is running and Setup silently skips the file copy,
; leaving the old binary in place even after a successful "install".
CloseApplications=yes
CloseApplicationsFilter=EasyUniVPN.exe,EasyUniVPNLauncher.exe,openconnect-saml.exe,openconnect.exe
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppLauncherExeName}
SetupIconFile=..\assets\app-icon.ico

[Tasks]
Name: "startmenu"; Description: "Create a Start Menu shortcut"; GroupDescription: "Shortcuts:"
Name: "startup";   Description: "Start EasyUniVPN with Windows"; GroupDescription: "Startup:"

[Files]
; Rust launcher - small native exe, handles UAC elevation and spawns the tray
Source: "..\launcher\target\release\{#MyAppLauncherExeName}"; DestDir: "{app}"; Flags: ignoreversion
; C# WinForms tray - requires .NET Framework 4.8 (pre-installed on Windows 10/11)
Source: "..\tray\bin\publish\{#MyAppTrayExeName}";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\tray\bin\publish\{#MyAppTrayExeName}.config"; DestDir: "{app}"; Flags: ignoreversion
; Python CLI bundle - handles setup wizard, bootstrap, reset, and autostart
Source: "..\cli\build\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; App icon + four VPN state PNGs used by the system tray
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: recursesubdirs createallsubdirs ignoreversion
; Pre-built openconnect VPN client (statically linked, no extra runtime needed)
Source: "..\runtime\openconnect\*"; DestDir: "{app}\runtime\openconnect"; Flags: recursesubdirs createallsubdirs ignoreversion
; Bootstrap assets: patched headless authenticator + pinned pip requirements
Source: ".\assets\headless.py"; DestDir: "{app}\installer\assets"; Flags: ignoreversion
Source: ".\requirements.lock.txt"; DestDir: "{app}\installer"; Flags: ignoreversion
; License texts - GPL-3.0 for EasyUniVPN itself plus third-party attributions
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu: launch tray (normal use) and open setup wizard (config / first-run)
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppLauncherExeName}";                        Tasks: startmenu
Name: "{group}\Setup {#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Parameters: "setup";           Tasks: startmenu

[Run]
; Offer to launch after install - shellexec lets Windows handle UAC normally,
; exactly as if the user double-clicked the launcher from Explorer.
Filename: "{app}\{#MyAppLauncherExeName}"; Description: "Launch EasyUniVPN"; Flags: postinstall shellexec nowait skipifsilent

[UninstallRun]
; Kill all EasyUniVPN processes and the VPN tunnel before touching any files.
; Order matters: stop the tray/launcher first (they own the VPN process tree),
; then explicitly kill the VPN binaries in case they outlived their parent.
; /T kills each process's children too - openconnect-saml.exe runs python.exe
; underneath it, which would otherwise survive and keep runtime files locked.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM EasyUniVPN.exe";          Flags: runhidden waituntilterminated; RunOnceId: "KillTray"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM EasyUniVPNLauncher.exe";  Flags: runhidden waituntilterminated; RunOnceId: "KillLauncher"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM openconnect-saml.exe";    Flags: runhidden waituntilterminated; RunOnceId: "KillOcSaml"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM openconnect.exe";         Flags: runhidden waituntilterminated; RunOnceId: "KillOpenconnect"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM EasyUniVPNCli.exe";       Flags: runhidden waituntilterminated; RunOnceId: "KillCli"
; --no-prompt: reset runs on a hidden console that still counts as a TTY, so
; without it the "Set up EasyUniVPN now?" prompt would block the uninstaller
; forever on a window nobody can see.
Filename: "{app}\{#MyAppExeName}"; Parameters: "reset --no-prompt"; Flags: runhidden waituntilterminated; RunOnceId: "ResetEasyUniVPN"
; reset only disables the task; fully delete it on uninstall so nothing is left.
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""EasyUniVPN"" /F"; Flags: runhidden waituntilterminated; RunOnceId: "DeleteAutostartTask"

[UninstallDelete]
; runtime\python is downloaded post-install by bootstrap
; and isn't tracked by [Files] - force the whole tree gone so no trace is left.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}"
; Tray log lives in APPDATA - not covered by the 'reset' command.
Type: filesandordirs; Name: "{userappdata}\EasyUniVPN\logs"

[Code]
type
  // 52-byte opaque buffer - large enough for Win32 MSG on 32-bit (28 B) and 64-bit (48 B).
  // We never read individual fields; we let PeekMessage write into it and pass the
  // same buffer on to TranslateMessage/DispatchMessage.
  TMsgBuf = array[0..12] of DWORD;

const
  PM_REMOVE = 1;

// Minimal message pump for the bootstrap polling loop.
// Application.ProcessMessages is not exposed in Inno Setup Pascal, so we import
// PeekMessage/TranslateMessage/DispatchMessage directly from user32.dll.
function PeekMessage(var Buf: TMsgBuf; Wnd: HWND; MsgMin, MsgMax, RemoveMsg: UINT): BOOL;
  external 'PeekMessageW@user32.dll stdcall';
function TranslateMessage(var Buf: TMsgBuf): BOOL;
  external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(var Buf: TMsgBuf): LongInt;
  external 'DispatchMessageW@user32.dll stdcall';

procedure ProcessMessages();
var
  Buf: TMsgBuf;
begin
  while PeekMessage(Buf, 0, 0, 0, PM_REMOVE) do begin
    TranslateMessage(Buf);
    DispatchMessage(Buf);
  end;
end;

var
  // Scrolling tail of the live log file shown below the progress bar during
  // bootstrap so long steps ("Installing dependencies...") have visible activity.
  BootstrapLogMemo: TNewMemo;
  // Set in InitializeSetup() from the uninstall registry key - '' on a fresh install.
  PreviousVersion: String;
  // Set to True by CancelButtonClick when the user cancels during bootstrap so the
  // polling loop can exit cleanly and let Inno Setup roll back.
  BootstrapCancelled: Boolean;

function UninstallRegistryKey(): String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1';
end;

// Reads the next '.'-separated component of a version string, advancing Index.
// Non-numeric trailing text (e.g. the "-dev" of a placeholder build) counts as 0.
function NextVersionPart(const S: String; var Index: Integer): Integer;
var
  Part: String;
begin
  Part := '';
  while (Index <= Length(S)) and (S[Index] <> '.') do begin
    Part := Part + S[Index];
    Index := Index + 1;
  end;
  if (Index <= Length(S)) and (S[Index] = '.') then
    Index := Index + 1;
  Result := StrToIntDef(Part, 0);
end;

// Numeric x.y.z comparison: 1 when A > B, -1 when A < B, 0 when equal.
function CompareVersions(const A, B: String): Integer;
var
  IA, IB, PA, PB, Step: Integer;
begin
  Result := 0;
  IA := 1;
  IB := 1;
  for Step := 1 to 4 do begin
    PA := NextVersionPart(A, IA);
    PB := NextVersionPart(B, IB);
    if PA > PB then begin Result := 1; exit; end;
    if PA < PB then begin Result := -1; exit; end;
  end;
end;

// The config_version recorded in the user's saved settings, mirroring
// CONFIG_VERSION in cli\src\common\app_config.py. Returns 0 when no setup
// exists; a config.json without the field predates versioning and counts as 1.
function InstalledConfigVersion(): Integer;
var
  Path, S, Num: String;
  Data: AnsiString;
  P: Integer;
begin
  Result := 0;
  Path := ExpandConstant('{userappdata}\EasyUniVPN\config.json');
  if not FileExists(Path) then
    exit;
  Data := '';
  if not LoadStringFromFile(Path, Data) then
    exit;
  S := String(Data);
  P := Pos('"config_version"', S);
  if P = 0 then begin
    Result := 1;
    exit;
  end;
  P := P + Length('"config_version"');
  while (P <= Length(S)) and ((S[P] = ' ') or (S[P] = ':')) do
    P := P + 1;
  Num := '';
  while (P <= Length(S)) and (S[P] >= '0') and (S[P] <= '9') do begin
    Num := Num + S[P];
    P := P + 1;
  end;
  Result := StrToIntDef(Num, 1);
end;

// Launches the existing installation's uninstaller and waits for it.
procedure RunExistingUninstaller();
var
  UninstallCmd: String;
  ResultCode: Integer;
begin
  if RegQueryStringValue(HKLM, UninstallRegistryKey(), 'UninstallString', UninstallCmd) then
    Exec(RemoveQuotes(UninstallCmd), '', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode)
  else
    MsgBox('Could not locate the uninstaller for the existing installation.', mbError, MB_OK);
end;

// When an installation already exists, offer Continue (update/repair/downgrade),
// Uninstall, or Cancel - and warn when downgrading below the version that wrote
// the currently saved settings format.
function InitializeSetup(): Boolean;
var
  Cmp, CfgVer, Choice: Integer;
  Instruction, Detail: String;
begin
  Result := True;
  if not RegQueryStringValue(HKLM, UninstallRegistryKey(), 'DisplayVersion', PreviousVersion) then
    PreviousVersion := '';
  if (PreviousVersion = '') or WizardSilent then
    exit;

  Cmp := CompareVersions('{#MyAppVersion}', PreviousVersion);
  if Cmp > 0 then begin
    Instruction := 'EasyUniVPN ' + PreviousVersion + ' is already installed.';
    Detail := 'Continue to update it to version {#MyAppVersion}. ' +
              'Your saved credentials, VPN profile, and settings are kept.';
  end else if Cmp = 0 then begin
    Instruction := 'EasyUniVPN {#MyAppVersion} is already installed.';
    Detail := 'Continue to repair this installation. Anything missing or damaged is ' +
              'restored; your saved credentials and settings are kept.';
  end else begin
    Instruction := 'A newer EasyUniVPN (' + PreviousVersion + ') is already installed.';
    Detail := 'Continue to downgrade it to version {#MyAppVersion}. ' +
              'Your saved credentials and settings are kept.';
    CfgVer := InstalledConfigVersion();
    if CfgVer > {#SupportedConfigVersion} then
      Detail := Detail + #13#10 + #13#10 +
        'Warning: your settings were saved in a newer format (version ' + IntToStr(CfgVer) +
        ') than this release understands (version {#SupportedConfigVersion}). ' +
        'After downgrading you may have to run setup again.';
  end;

  Choice := TaskDialogMsgBox(Instruction,
      Detail + #13#10 + #13#10 + 'Or choose Uninstall to remove EasyUniVPN from this computer.',
      mbConfirmation, MB_YESNOCANCEL, ['&Continue', '&Uninstall'], 0);
  if Choice = IDNO then begin
    RunExistingUninstaller();
    Result := False;
  end else if Choice <> IDYES then
    Result := False;
end;

procedure InitializeWizard();
begin
  if PreviousVersion <> '' then begin
    case CompareVersions('{#MyAppVersion}', PreviousVersion) of
      0:
        WizardForm.WelcomeLabel2.Caption :=
          'This will repair the existing EasyUniVPN ' + PreviousVersion + ' installation, ' +
          're-downloading anything missing without touching your saved credentials or settings.' + #13#10 + #13#10
          + WizardForm.WelcomeLabel2.Caption;
      1:
        WizardForm.WelcomeLabel2.Caption :=
          'This will update EasyUniVPN ' + PreviousVersion + ' to {#MyAppVersion}. ' +
          'Your saved credentials, VPN profile, and settings are kept as-is.' + #13#10 + #13#10
          + WizardForm.WelcomeLabel2.Caption;
      -1:
        WizardForm.WelcomeLabel2.Caption :=
          'This will downgrade EasyUniVPN ' + PreviousVersion + ' to {#MyAppVersion}. ' +
          'Your saved credentials, VPN profile, and settings are kept as-is.' + #13#10 + #13#10
          + WizardForm.WelcomeLabel2.Caption;
    end;
  end;

  BootstrapLogMemo := TNewMemo.Create(WizardForm);
  BootstrapLogMemo.Parent := WizardForm.InstallingPage;
  BootstrapLogMemo.Left := WizardForm.StatusLabel.Left;
  BootstrapLogMemo.Top := WizardForm.ProgressGauge.Top + WizardForm.ProgressGauge.Height + 16;
  BootstrapLogMemo.Width := WizardForm.ProgressGauge.Width;
  BootstrapLogMemo.Height := WizardForm.InstallingPage.ClientHeight - BootstrapLogMemo.Top - 8;
  BootstrapLogMemo.ScrollBars := ssVertical;
  BootstrapLogMemo.ReadOnly := True;
  BootstrapLogMemo.Font.Name := 'Consolas';
  BootstrapLogMemo.Font.Size := 8;
  BootstrapLogMemo.Visible := False;

  // Raise the installer to the front when it opens - it can appear behind
  // other windows when launched from a browser download or the taskbar.
  WizardForm.BringToFront;
end;

// Keeps only the last MaxLines lines of Text so the memo shows a scrolling tail.
function LastLines(Text: String; MaxLines: Integer): String;
var
  AllLines: TStringList;
  StartIndex: Integer;
  Kept: TStringList;
  I: Integer;
begin
  AllLines := TStringList.Create;
  Kept := TStringList.Create;
  try
    AllLines.Text := Text;
    StartIndex := AllLines.Count - MaxLines;
    if StartIndex < 0 then
      StartIndex := 0;
    for I := StartIndex to AllLines.Count - 1 do
      Kept.Add(AllLines[I]);
    Result := Kept.Text;
  finally
    AllLines.Free;
    Kept.Free;
  end;
end;

procedure RefreshBootstrapLog(LogFile: String);
var
  LogText: AnsiString;
begin
  if not FileExists(LogFile) then
    Exit;
  LogText := '';
  if not LoadStringFromFile(LogFile, LogText) then
    Exit;
  BootstrapLogMemo.Text := LastLines(String(LogText), 200);
  // WM_VSCROLL / SB_BOTTOM - auto-scroll to the newest output line.
  SendMessage(BootstrapLogMemo.Handle, $0115, 7, 0);
end;

// Parses installer.runtime's "pct|label" progress lines and updates the wizard UI.
procedure RefreshBootstrapProgress(ProgressFile: String);
var
  Line: AnsiString;
  SepPos, Pct: Integer;
  Message: String;
begin
  if not FileExists(ProgressFile) then
    Exit;
  Line := '';
  if not LoadStringFromFile(ProgressFile, Line) then
    Exit;
  SepPos := Pos('|', String(Line));
  if SepPos = 0 then
    Exit;
  Pct := StrToIntDef(Copy(String(Line), 1, SepPos - 1), -1);
  if Pct < 0 then
    Exit;
  Message := Copy(String(Line), SepPos + 1, MaxInt);
  WizardForm.StatusLabel.Caption := Message;
  WizardForm.StatusLabel.Update;
  WizardForm.ProgressGauge.Style := npbstNormal;
  WizardForm.ProgressGauge.Max := 100;
  WizardForm.ProgressGauge.Position := Pct;
end;

// Launches bootstrap with ewNoWait and polls sentinel files until it writes a
// status file. Application.ProcessMessages is called every 100 ms so the wizard
// window stays responsive (can be moved/closed) for the full download duration.
// Sleep(N) alone blocks the message pump - Windows marks the window "Not Responding"
// after ~5 s without a pumped message.
procedure RunBootstrapResponsively();
var
  ResultCode: Integer;
  SentinelFile, ProgressFile, LogFile: String;
  Status: AnsiString;
  Ticks: Integer;
begin
  SentinelFile := ExpandConstant('{app}\runtime\_bootstrap_status.txt');
  ProgressFile := ExpandConstant('{app}\runtime\_bootstrap_progress.txt');
  LogFile      := ExpandConstant('{app}\runtime\_bootstrap_log.txt');
  if FileExists(SentinelFile) then DeleteFile(SentinelFile);
  if FileExists(ProgressFile) then DeleteFile(ProgressFile);
  if FileExists(LogFile)      then DeleteFile(LogFile);

  WizardForm.StatusLabel.Caption := 'Downloading Python runtime and dependencies...';
  WizardForm.StatusLabel.Update;
  WizardForm.ProgressGauge.Style := npbstMarquee;
  BootstrapLogMemo.Visible := True;
  BootstrapLogMemo.Text := '';

  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), 'bootstrap', '', SW_HIDE, ewNoWait, ResultCode) then begin
    MsgBox('Could not start the runtime download step.', mbError, MB_OK);
    Exit;
  end;

  // Poll every 100 ms; pump messages after each sleep so the Cancel button and
  // window movement work throughout the download. Timeout after 15 minutes.
  BootstrapCancelled := False;
  Ticks := 0;
  while (not FileExists(SentinelFile)) and (not BootstrapCancelled) do begin
    Sleep(100);
    ProcessMessages;
    if BootstrapCancelled then
      break;
    RefreshBootstrapProgress(ProgressFile);
    RefreshBootstrapLog(LogFile);
    Ticks := Ticks + 1;
    if Ticks >= 9000 then begin
      MsgBox(
        'The runtime download did not finish within 15 minutes.' + #13#10 + #13#10 +
        'Check your internet connection, then retry by running "EasyUniVPNCli.exe bootstrap".',
        mbError, MB_OK);
      Exit;
    end;
  end;
  if BootstrapCancelled then
    Exit;
  RefreshBootstrapLog(LogFile);

  Status := '';
  LoadStringFromFile(SentinelFile, Status);
  if Pos('error', String(Status)) = 1 then
    MsgBox(
      'EasyUniVPN could not finish setting itself up:' + #13#10 + #13#10 +
      String(Status) + #13#10 + #13#10 +
      'You can retry by running "EasyUniVPNCli.exe bootstrap" once you have internet access.',
      mbError, MB_OK);
end;

// Kill the bootstrap child process and signal the polling loop to exit when the
// user cancels during the "Installing" page. Inno Setup's rollback then removes
// the files it copied; we additionally wipe the partial runtime/python download
// since that is not tracked by [Files].
// Cancel=True (default) allows the cancel to proceed; Confirm=True means the user
// already clicked Yes in the "Are you sure?" dialog - that is when we kill bootstrap.
procedure CancelButtonClick(CurPageID: Integer; var Cancel: Boolean; var Confirm: Boolean);
var
  ResultCode: Integer;
begin
  if Confirm and (CurPageID = wpInstalling) then begin
    BootstrapCancelled := True;
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM EasyUniVPNCli.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    // Wipe any partial Python download - not in [Files] so not removed by rollback.
    Exec(ExpandConstant('{sys}\cmd.exe'),
         '/C rmdir /S /Q "' + ExpandConstant('{app}\runtime\python') + '"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then begin
    // Hard-kill all EasyUniVPN processes and the VPN tunnel so Windows
    // releases file locks before Setup copies the new binaries over.
    // /T includes each process's children (openconnect-saml.exe runs
    // python.exe underneath it).
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM EasyUniVPN.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM EasyUniVPNLauncher.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM openconnect-saml.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM openconnect.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM EasyUniVPNCli.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  if CurStep = ssPostInstall then begin
    // Bootstrap always runs first: downloads Python, installs deps,
    // applies headless.py patch, and registers the autostart task (disabled).
    RunBootstrapResponsively();

    // Now that Python is provisioned and the task exists, honour the checkbox.
    // "autostart on" requires a live runtime/python to pass _runtime_ready(),
    // which is why this cannot be a plain [Run] entry (those fire before bootstrap).
    if WizardIsTaskSelected('startup') then
      Exec(ExpandConstant('{app}\{#MyAppExeName}'), 'autostart on', '',
           SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    'This will remove EasyUniVPN, along with your saved credentials, VPN profile, and settings.' + #13#10 + #13#10
    + 'Continue?',
    mbConfirmation, MB_YESNO) = IDYES;
end;
