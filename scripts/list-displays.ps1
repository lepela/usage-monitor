# 연결된 디스플레이의 좌표와 크기를 출력한다. kiosk.cmd 의 X, Y 값을 여기서 고른다.
# 논리 좌표·크기는 Windows 배율이 적용된 값이고, Chrome --window-position 에는 이 값을 쓴다.
# 물리 해상도는 EnumDisplaySettings 로 따로 읽는다(WinForms Screen 은 배율 적용 값만 준다).
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class DisplayMode {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
    public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields;
    public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
    public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
  }
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  static extern bool EnumDisplaySettings(string name, int mode, ref DEVMODE dm);
  public static int[] Physical(string name) {
    var dm = new DEVMODE(); dm.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE));
    return EnumDisplaySettings(name, -1, ref dm) ? new[] { dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency } : new[] { 0, 0, 0 };
  }
}
'@

[System.Windows.Forms.Screen]::AllScreens | Sort-Object { $_.Bounds.X } | ForEach-Object {
    $b = $_.Bounds
    $p = [DisplayMode]::Physical($_.DeviceName)
    $scale = if ($b.Width) { [math]::Round($p[0] / $b.Width * 100) } else { 0 }
    [pscustomobject]@{
        Display     = $_.DeviceName
        Primary     = $_.Primary
        '논리 X,Y'  = "$($b.X),$($b.Y)"
        '논리 크기' = "$($b.Width)x$($b.Height)"
        '물리 해상도' = "$($p[0])x$($p[1]) @$($p[2])Hz"
        '배율%'     = $scale
        비고         = if ($p[0] -eq 1920 -and $p[1] -eq 480) { '바 모니터' }
                       elseif ($p[0] -eq 480 -and $p[1] -eq 1920) { '바 모니터(세로) - 디스플레이 설정에서 가로로 회전 필요' }
                       else { '' }
    }
} | Format-Table -AutoSize

'kiosk 실행 예: scripts\kiosk.cmd <논리 X> <논리 Y>'
