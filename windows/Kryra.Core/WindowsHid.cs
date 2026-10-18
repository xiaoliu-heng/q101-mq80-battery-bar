using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace Kryra.Core;

public sealed record DeviceInfo(string Id,string Name,string Transport,string Path,int InputLength,int OutputLength);
public interface IHidConnection : IDisposable { byte[] Request(byte[] request,CancellationToken cancellation); }
public interface IHidBackend { IReadOnlyList<DeviceInfo> Enumerate(); IHidConnection Open(DeviceInfo device); bool Demo { get; } }

public sealed class WindowsHidBackend : IHidBackend
{
    public bool Demo=>false;
    public IReadOnlyList<DeviceInfo> Enumerate()
    {
        Native.HidD_GetHidGuid(out var guid); var set=Native.SetupDiGetClassDevs(ref guid,null,IntPtr.Zero,0x12);
        if (set==new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());
        var devices=new List<DeviceInfo>();
        try
        {
            for(uint index=0;;index++)
            {
                var iface=new Native.InterfaceData{Size=Marshal.SizeOf<Native.InterfaceData>()};
                if (!Native.SetupDiEnumDeviceInterfaces(set,IntPtr.Zero,ref guid,index,ref iface))
                { if(Marshal.GetLastWin32Error()==259) break; throw new Win32Exception(Marshal.GetLastWin32Error()); }
                Native.SetupDiGetDeviceInterfaceDetail(set,ref iface,IntPtr.Zero,0,out var needed,IntPtr.Zero);
                var detail=Marshal.AllocHGlobal((int)needed);
                try
                {
                    Marshal.WriteInt32(detail,IntPtr.Size==8?8:6);
                    if(!Native.SetupDiGetDeviceInterfaceDetail(set,ref iface,detail,needed,out _,IntPtr.Zero)) continue;
                    var path=Marshal.PtrToStringUni(detail+4)!;
                    using var handle=Native.CreateFile(path,0,3,IntPtr.Zero,3,0,IntPtr.Zero);
                    if(handle.IsInvalid) continue;
                    var attrs=new Native.Attributes{Size=Marshal.SizeOf<Native.Attributes>()};
                    if(!Native.HidD_GetAttributes(handle,ref attrs) || attrs.Vendor!=0x320f || attrs.Product is not (0x5055 or 0x5088)) continue;
                    if(!Native.HidD_GetPreparsedData(handle,out var data)) continue;
                    try
                    {
                        if(Native.HidP_GetCaps(data,out var caps)!=0x110000 || caps.UsagePage!=0xff60 || caps.Usage!=0x61) continue;
                        if(caps.InputLength!=33 || caps.OutputLength!=33) throw new ControlException("目标 HID 报告长度不是 33 字节，已停止连接。");
                        var mode=attrs.Product==0x5055?"usb":"rf";
                        var id=mode+":"+Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(path))).Substring(0,12);
                        devices.Add(new(id,mode=="usb"?"IQUNIX MQ80 / Q101":"IQUNIX 2.4GHz",mode,path,caps.InputLength,caps.OutputLength));
                    }
                    finally { Native.HidD_FreePreparsedData(data); }
                }
                finally { Marshal.FreeHGlobal(detail); }
            }
        }
        finally { Native.SetupDiDestroyDeviceInfoList(set); }
        return devices;
    }
    public IHidConnection Open(DeviceInfo device)=>new WindowsHidConnection(device);
}

internal sealed class WindowsHidConnection : IHidConnection
{
    private readonly SafeFileHandle handle;
    public WindowsHidConnection(DeviceInfo device)
    {
        handle=Native.CreateFile(device.Path,0xc0000000,3,IntPtr.Zero,3,0x40000000,IntPtr.Zero);
        if(handle.IsInvalid) { handle.Dispose(); throw new ControlException("无法打开侧灯接口，请检查连接及其他控灯程序。"); }
        Native.HidD_SetNumInputBuffers(handle,32);
    }
    // Unmanaged OVERLAPPED storage and pinned buffers remain valid until every I/O is completed or cancelled.
    private unsafe int Transfer(byte[] buffer,bool write,CancellationToken cancellation,int timeoutMs)
    {
        cancellation.ThrowIfCancellationRequested();
        using var signal=new EventWaitHandle(false,EventResetMode.ManualReset);
        var overlapped=new Native.Overlapped{Event=signal.SafeWaitHandle.DangerousGetHandle()};
        fixed(byte* bytes=buffer)
        {
            var success=write?Native.WriteFile(handle,bytes,buffer.Length,out var n,&overlapped)
                :Native.ReadFile(handle,bytes,buffer.Length,out n,&overlapped);
            if(success) return n;
            var error=Marshal.GetLastWin32Error();
            if(error!=997) throw new ControlException("HID 设备已断开或无法收发："+new Win32Exception(error).Message);
            var index=WaitHandle.WaitAny([signal,cancellation.WaitHandle],timeoutMs);
            if(index!=0)
            {
                Native.CancelIoEx(handle,&overlapped);
                Native.GetOverlappedResult(handle,&overlapped,out _,true);
                cancellation.ThrowIfCancellationRequested();
                throw new ControlException("等待键盘回复超时；2.4G 模式请先按键唤醒。");
            }
            if(!Native.GetOverlappedResult(handle,&overlapped,out n,false))
                throw new ControlException("HID 收发失败："+new Win32Exception(Marshal.GetLastWin32Error()).Message);
            return n;
        }
    }
    public byte[] Request(byte[] request,CancellationToken cancellation)
    {
        if(request.Length!=32) throw new ArgumentException("A logical report is 32 bytes.");
        var output=new byte[33]; request.CopyTo(output,1);
        if(Transfer(output,true,cancellation,1000)!=33) throw new ControlException("HID 报告未完整发送。");
        var timer=Stopwatch.StartNew();
        while(timer.ElapsedMilliseconds<1200)
        {
            var input=new byte[33]; var count=Transfer(input,false,cancellation,(int)Math.Max(1,1200-timer.ElapsedMilliseconds));
            if(count!=33 || input[0]!=0) continue;
            var reply=input.AsSpan(1,32).ToArray();
            if(Wire.Matches(request,reply)) return reply;
        }
        throw new ControlException("未收到与当前命令匹配的键盘回复。");
    }
    public void Dispose()=>handle.Dispose();
}

internal static partial class Native
{
    [StructLayout(LayoutKind.Sequential)] internal struct InterfaceData { public int Size; public Guid Guid; public int Flags; public IntPtr Reserved; }
    [StructLayout(LayoutKind.Sequential)] internal struct Attributes { public int Size; public ushort Vendor,Product,Version; }
    [StructLayout(LayoutKind.Sequential)] internal struct Caps
    {
        public ushort Usage,UsagePage,InputLength,OutputLength,FeatureLength;
        [MarshalAs(UnmanagedType.ByValArray,SizeConst=17)] public ushort[] Reserved;
        public ushort LinkNodes,InputButtons,InputValues,InputIndices,OutputButtons,OutputValues,OutputIndices,FeatureButtons,FeatureValues,FeatureIndices;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct Overlapped { public UIntPtr Internal,InternalHigh; public uint Offset,OffsetHigh; public IntPtr Event; }
    [DllImport("hid.dll")] internal static extern void HidD_GetHidGuid(out Guid guid);
    [DllImport("hid.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.U1)] internal static extern bool HidD_GetAttributes(SafeFileHandle h,ref Attributes attributes);
    [DllImport("hid.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.U1)] internal static extern bool HidD_GetPreparsedData(SafeFileHandle h,out IntPtr data);
    [DllImport("hid.dll")] [return:MarshalAs(UnmanagedType.U1)] internal static extern bool HidD_FreePreparsedData(IntPtr data);
    [DllImport("hid.dll")] internal static extern int HidP_GetCaps(IntPtr data,out Caps caps);
    [DllImport("hid.dll")] [return:MarshalAs(UnmanagedType.U1)] internal static extern bool HidD_SetNumInputBuffers(SafeFileHandle h,uint count);
    [DllImport("setupapi.dll",CharSet=CharSet.Unicode,SetLastError=true)] internal static extern IntPtr SetupDiGetClassDevs(ref Guid guid,string? enumerator,IntPtr parent,uint flags);
    [DllImport("setupapi.dll",SetLastError=true)] internal static extern bool SetupDiEnumDeviceInterfaces(IntPtr set,IntPtr info,ref Guid guid,uint index,ref InterfaceData data);
    [DllImport("setupapi.dll",CharSet=CharSet.Unicode,SetLastError=true,EntryPoint="SetupDiGetDeviceInterfaceDetailW")] internal static extern bool SetupDiGetDeviceInterfaceDetail(IntPtr set,ref InterfaceData data,IntPtr detail,uint size,out uint required,IntPtr info);
    [DllImport("setupapi.dll")] internal static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true,EntryPoint="CreateFileW")] internal static extern SafeFileHandle CreateFile(string path,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern unsafe bool ReadFile(SafeFileHandle handle,byte* buffer,int length,out int transferred,Overlapped* overlapped);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern unsafe bool WriteFile(SafeFileHandle handle,byte* buffer,int length,out int transferred,Overlapped* overlapped);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern unsafe bool GetOverlappedResult(SafeFileHandle handle,Overlapped* overlapped,out int transferred,bool wait);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern unsafe bool CancelIoEx(SafeFileHandle handle,Overlapped* overlapped);
}
