; Inno Setup script for ScreenTranslator (Windows installer)
; Build output: dist\installer\ScreenTranslator_Setup_*.exe
;
; Prerequisite:
;   Install Inno Setup 6, then run build_installer.bat

#define MyAppName "屏幕翻译工具"
#define MyAppExeName "ScreenTranslator.exe"
#define MyAppPublisher "14ku"
#define MyAppURL "https://14ku.date"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef AppArch
  #define AppArch "x64"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\ScreenTranslator"
#endif
#ifndef DualBuild
  #define DualBuild "0"
#endif
#ifndef SourceDirX86
  #define SourceDirX86 "..\dist\ScreenTranslator-x86"
#endif
#ifndef SourceDirX64
  #define SourceDirX64 "..\dist\ScreenTranslator-x64"
#endif

; Prefer a source icon if present; otherwise use the icon bundled in the PyInstaller dist output.
#if FileExists("..\assets\icons\app_icon.ico")
  #define MySetupIcon "..\assets\icons\app_icon.ico"
#else
  #if DualBuild == "1"
    #define MySetupIcon SourceDirX64 + "\_internal\assets\icons\app_icon.ico"
  #else
    #define MySetupIcon SourceDir + "\_internal\assets\icons\app_icon.ico"
  #endif
#endif

[Setup]
AppId={{A0B6E0B3-CE6D-4C3D-9C3C-1C2D86B3A7D2}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\ScreenTranslator
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=ScreenTranslator_Setup_{#MyAppVersion}
SetupIconFile={#MySetupIcon}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
#if DualBuild == "1"
ArchitecturesAllowed=x86 x64
#elif AppArch == "x86"
ArchitecturesAllowed=x86
#else
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#endif

; ---- Large installer support (> ~4.2GB) ----
; When the payload is larger than what Windows supports for a single Setup.exe,
; Inno Setup requires Disk Spanning. This generates:
;   ScreenTranslator_Setup_x.y.z.exe + ScreenTranslator_Setup_x.y.z-*.bin
; Keep all files in the same folder when distributing/running the installer.
DiskSpanning=no

[Languages]
; Why English before?
; - Your Inno Setup install does not include ChineseSimplified.isl, so compilation failed.
; This script now prefers Chinese when the .isl file is available, otherwise falls back to English.
;
; How to enable Chinese UI:
; - Option A (recommended): Copy ChineseSimplified.isl into THIS folder:
;     screen-translator\installer\ChineseSimplified.isl
; - Option B: Install the full Inno Setup package that includes language files, so that:
;     compiler:Languages\ChineseSimplified.isl
;   exists on your machine.
#if FileExists("ChineseSimplified.isl")
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
#else
Name: "en"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务"; Flags: unchecked

[Files]
; PyInstaller onedir output
#if DualBuild == "1"
Source: "{#SourceDirX86}\*"; DestDir: "{app}\ScreenTranslator-x86"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "logs\*;*.log;shortcuts_created.tag;installed.tag"
Source: "{#SourceDirX64}\*"; DestDir: "{app}\ScreenTranslator-x64"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "logs\*;*.log;shortcuts_created.tag;installed.tag"
Source: "..\installer\ScreenTranslatorLauncher.bat"; DestDir: "{app}"; Flags: ignoreversion
#else
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "logs\*;*.log;shortcuts_created.tag;installed.tag"
#if FileExists("..\dist\ScreenTranslator-x86\HookAgent\HookAgent.exe")
Source: "..\dist\ScreenTranslator-x86\HookAgent\*"; DestDir: "{app}\ScreenTranslator-x86\HookAgent"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "logs\*;*.log"
#endif
#if FileExists("..\dist\ScreenTranslator-x64\HookAgent\HookAgent.exe")
Source: "..\dist\ScreenTranslator-x64\HookAgent\*"; DestDir: "{app}\ScreenTranslator-x64\HookAgent"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "logs\*;*.log"
#endif
#endif

[Icons]
; Dual build uses launcher
#if DualBuild == "1"
Name: "{group}\{#MyAppName}"; Filename: "{app}\ScreenTranslatorLauncher.bat"
#else
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
#endif
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
#if DualBuild == "1"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\ScreenTranslatorLauncher.bat"; Tasks: desktopicon
#else
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
#endif

[Run]
#if DualBuild == "1"
Filename: "{app}\ScreenTranslatorLauncher.bat"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
#else
Filename: "{app}\{#MyAppExeName}"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
#endif
