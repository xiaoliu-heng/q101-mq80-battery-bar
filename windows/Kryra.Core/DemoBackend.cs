using System.Buffers.Binary;
using System.Diagnostics;

namespace Kryra.Core;

public sealed class DemoBackend : IHidBackend
{
    public bool Demo=>true;
    public int Requests { get; private set; }
    public List<byte> Commands { get; }=[];
    private long deadline;
    private uint lastId;
    private byte[]? lastPayload;
    private byte lastSequence;
    private int timeout=2000;
    private bool active;
    public IReadOnlyList<DeviceInfo> Enumerate()=>[new("usb:DEMO","演示键盘","usb","",33,33),new("rf:DEMO","演示接收器","rf","",33,33)];
    public IHidConnection Open(DeviceInfo device)=>new Connection(this,device.Transport);
    private sealed class Connection(DemoBackend parent,string mode):IHidConnection
    {
        public byte[] Request(byte[] request,CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested(); parent.Requests++; parent.Commands.Add((byte)(request[3]&7));
            var op=request[3]&7; byte status=0;
            if(Stopwatch.GetTimestamp()>=parent.deadline) parent.active=false;
            if(op==Wire.Release) parent.active=false;
            if(op==Wire.Timeout) parent.timeout=BinaryPrimitives.ReadUInt16LittleEndian(request.AsSpan(5));
            if(op==Wire.Play)
            {
                var id=BinaryPrimitives.ReadUInt32LittleEndian(request.AsSpan(14));
                if(id==parent.lastId) { if(!request.AsSpan(5,13).SequenceEqual(parent.lastPayload)) status=3; }
                else { parent.lastId=id; parent.lastPayload=request.AsSpan(5,13).ToArray(); parent.timeout=BinaryPrimitives.ReadUInt16LittleEndian(request.AsSpan(12)); parent.active=true; parent.lastSequence=request[4]; parent.deadline=Stopwatch.GetTimestamp()+(long)(parent.timeout/1000d*Stopwatch.Frequency); }
            }
            if(op==Wire.Frame) { parent.lastId=0; parent.active=true; parent.lastSequence=request[4]; parent.deadline=Stopwatch.GetTimestamp()+(long)(parent.timeout/1000d*Stopwatch.Frequency); }
            var r=new byte[32]; request.AsSpan(0,5).CopyTo(r); r[3]|=8; r[5]=status; r[6]=1; r[7]=9;
            r[8]=(byte)(parent.active?1:0); r[9]=88; r[10]=(byte)(mode=="usb"?2:1); r[11]=parent.lastSequence; r[12]=3;
            BinaryPrimitives.WriteUInt16LittleEndian(r.AsSpan(13),(ushort)parent.timeout);
            BinaryPrimitives.WriteUInt16LittleEndian(r.AsSpan(15),(ushort)(parent.active?Math.Max(0,(parent.deadline-Stopwatch.GetTimestamp())*1000/Stopwatch.Frequency):0));
            "Q101"u8.CopyTo(r.AsSpan(17)); r[21]=162; r[22]=31; new byte[]{0x46,0x56,1,3,1,0,31,3,0}.CopyTo(r,23); return r;
        }
        public void Dispose() { }
    }
}
