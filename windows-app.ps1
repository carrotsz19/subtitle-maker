# Makes Subtitle Maker a proper Windows app for this user (no admin needed): a Start menu entry
# that opens the window straight away (search finds it as an app, not a .bat), the same app ID as
# the window (so pinning to the taskbar and notifications belong to it), and an entry in
# Settings > Apps with its version, size and an Uninstall button. -Remove takes them away again.
#
# Run by winapp.py (the app does this by itself when it starts, if anything is missing or out
# of date) and by install.ps1. uninstall.py removes the same things.
param(
    [Parameter(Mandatory)] [string] $Install,
    [Parameter(Mandatory)] [string] $Data,
    [string] $Version = '',
    [int] $SizeKB = 0,
    [switch] $Remove
)
$ErrorActionPreference = 'Stop'
$appId = 'Veyra.SubtitleMaker'                       # matches app.py's SetCurrentProcessExplicitAppUserModelID
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Veyra.SubtitleMaker'
$startLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Subtitle Maker.lnk'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Subtitle Maker.lnk'

if ($Remove) {
    Remove-Item -LiteralPath $startLink -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $key -Recurse -Force -ErrorAction SilentlyContinue
    return
}

$pythonw = Join-Path $Data 'venv\Scripts\pythonw.exe'
$app = Join-Path $Install 'app.py'
$icon = Join-Path $Install 'ui\icon.ico'

# A shortcut's app ID can only be set through its property store (COM), so a few lines of C#
Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SubtitleMakerShortcut {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid fmtid; public uint pid; }
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr value; }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr bindCtx, int flags, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);
    public static void SetAppId(string link, string appId) {
        Guid iid = typeof(IPropertyStore).GUID;
        IPropertyStore store;
        SHGetPropertyStoreFromParsingName(link, IntPtr.Zero, 2 /* GPS_READWRITE */, ref iid, out store);
        try {
            // PKEY_AppUserModel_ID
            PropertyKey key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
            PropVariant value = new PropVariant { vt = 31 /* VT_LPWSTR */, value = Marshal.StringToCoTaskMemUni(appId) };
            try { store.SetValue(ref key, ref value); store.Commit(); }
            finally { Marshal.FreeCoTaskMem(value.value); }
        } finally { Marshal.ReleaseComObject(store); }
    }
}
'@

function Set-AppShortcut($path) {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "`"$app`""
    $shortcut.WorkingDirectory = $Install
    $shortcut.IconLocation = $icon
    $shortcut.Description = 'Make subtitles for DaVinci Resolve'
    $shortcut.Save()
    [SubtitleMakerShortcut]::SetAppId($path, $appId)
}

# The Start menu entry, always; the desktop one only if it's there (it may have been deleted on purpose)
Set-AppShortcut $startLink
if (Test-Path -LiteralPath $desktopLink) { Set-AppShortcut $desktopLink }

# Settings > Apps: Uninstall opens the app's own uninstall screen (with the "keep voice clips" choice)
New-Item -Path $key -Force | Out-Null
$values = @{
    DisplayName = 'Subtitle Maker'; Publisher = 'Veyra'; DisplayIcon = $icon; DisplayVersion = $Version
    InstallLocation = $Install; URLInfoAbout = 'https://github.com/carrotsz19/subtitle-maker'
    UninstallString = "`"$pythonw`" `"$app`" --uninstall"; Comments = 'Make subtitles for DaVinci Resolve'
}
foreach ($name in $values.Keys) { Set-ItemProperty -Path $key -Name $name -Value $values[$name] }
foreach ($name in 'NoModify', 'NoRepair') { New-ItemProperty -Path $key -Name $name -Value 1 -PropertyType DWord -Force | Out-Null }
if ($SizeKB -gt 0) { New-ItemProperty -Path $key -Name EstimatedSize -Value $SizeKB -PropertyType DWord -Force | Out-Null }
if (-not (Get-ItemProperty -Path $key -Name InstallDate -ErrorAction SilentlyContinue)) {
    Set-ItemProperty -Path $key -Name InstallDate -Value (Get-Date -Format 'yyyyMMdd')
}
