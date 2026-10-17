using System.Buffers.Binary;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;

namespace Kryra.Core;

public sealed class ControlException(string message, int status = 503) : Exception(message)
{
    public int Status { get; } = status;
}

public readonly record struct Rgb(byte R, byte G, byte B)
{
    public string Hex => $"{R:X2}{G:X2}{B:X2}";
    public static Rgb Parse(string value)
    {
        value = value.StartsWith('#') ? value[1..] : value;
        if (value.Length != 6 || !value.All(char.IsAsciiHexDigit) || !uint.TryParse(value, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out var n))
            throw new ControlException("颜色需要六位十六进制，例如 34D399。", 400);
        return new((byte)(n >> 16), (byte)(n >> 8), (byte)n);
    }
    public static Rgb Rainbow(double hue)
    {
        var h = (hue - Math.Floor(hue)) * 6; var i = (int)h; var f = h - i;
        var v = i switch { 0 => (1d, f, 0d), 1 => (1-f, 1d, 0d), 2 => (0d, 1d, f),
            3 => (0d, 1-f, 1d), 4 => (f, 0d, 1d), _ => (1d, 0d, 1-f) };
        return new((byte)Math.Round(v.Item1*255), (byte)Math.Round(v.Item2*255), (byte)Math.Round(v.Item3*255));
    }
}

public sealed record FirmwareState(int Percent, string Connection, bool Active, bool BatteryQuery,
    bool UsbControl, bool RfControl, int TimeoutMs, int RemainingMs, byte LastSequence, string Version, int Build)
{
    public bool Permits(string mode) => mode == Connection && (mode == "usb" ? UsbControl : mode == "rf" && RfControl);
}

public static class Wire
{
    public const byte Info=0, Frame=1, Release=2, Timeout=4, Play=5;
    public static byte[] Packet(byte command, byte sequence, ReadOnlySpan<byte> payload = default)
    {
        if (command is not (Info or Frame or Release or Timeout or Play) || payload.Length > 27
            || (command == Frame && payload.Length != 27) || (command == Play && payload.Length != 13))
            throw new ControlException("不支持的侧灯命令或参数长度。", 400);
        var data = new byte[32]; data[0]=0xfe; data[1]=0x53; data[2]=0x4c; data[3]=(byte)(0x10|command); data[4]=sequence;
        payload.CopyTo(data.AsSpan(5)); return data;
    }
    public static bool Matches(ReadOnlySpan<byte> request, ReadOnlySpan<byte> reply) => request.Length == 32 && reply.Length == 32
        && request[4] == reply[4] && ((reply[..3].SequenceEqual(new byte[]{0xfe,0x53,0x4c}) && reply[3] == (request[3]|8))
        || (reply[0] == 1 && reply[1] == request[1]));
    public static FirmwareState Decode(byte[] request, byte[] reply)
    {
        if (!Matches(request,reply) || !reply.AsSpan(0,3).SequenceEqual(new byte[]{0xfe,0x53,0x4c})
            || !reply.AsSpan(17,4).SequenceEqual("Q101"u8) || reply[6] != 1)
            throw new ControlException("键盘没有返回兼容的侧灯协议，请确认已刷入 v3.1.0。");
        string[] errors=["", "协议版本不支持", "未知命令", "灯效参数无效", "灯效已结束", "当前连接方式不允许控灯", "HID 报告长度错误"];
        if (reply[5] != 0) throw new ControlException(reply[5] < errors.Length ? errors[reply[5]] : "固件返回未知错误。");
        if (reply[7]!=9 || reply[21]!=162 || reply[9]>100 || (reply[12]&3)==0)
            throw new ControlException("键盘返回的能力或电量无效。");
        if (!reply.AsSpan(23,6).SequenceEqual(new byte[]{0x46,0x56,1,3,1,0}) || (reply[22]&16)==0 || (reply[29]&16)==0)
            throw new ControlException("Kryra Windows 仅支持 v3.1.0 本地灯效固件。");
        return new(reply[9],reply[10] switch {2=>"usb",1=>"rf",_=>"ble"},(reply[8]&1)!=0,(reply[8]&2)!=0,
            (reply[12]&1)!=0,(reply[12]&2)!=0,BinaryPrimitives.ReadUInt16LittleEndian(reply.AsSpan(13)),
            BinaryPrimitives.ReadUInt16LittleEndian(reply.AsSpan(15)),reply[11],"3.1.0",BinaryPrimitives.ReadUInt16LittleEndian(reply.AsSpan(30)));
    }
}

public sealed record LightingCommand(string Mode, Rgb Color, int Brightness, int PeriodMs, int DurationMs, uint RequestId, Rgb[]? Colors = null)
{
    public static readonly string[] Effects=["solid","blink","breathe","rainbow"];
    public static uint NewId() { uint id; do { id=BinaryPrimitives.ReadUInt32LittleEndian(RandomNumberGenerator.GetBytes(4)); } while (id==0); return id; }
    public static LightingCommand Create(string mode="rainbow", string color="34D399", int brightness=25, int periodMs=5000, int durationMs=5000, uint? id=null)
    {
        if (!Effects.Contains(mode) || brightness is <0 or >100 || durationMs is <100 or >60000 || id == 0
            || (mode != "solid" && periodMs is <500 or >20000)) throw new ControlException("灯效参数超出范围。",400);
        return new(mode,mode=="rainbow" ? new(0,0,0) : Rgb.Parse(color),brightness,mode=="solid"?0:periodMs,durationMs,id??NewId());
    }
    public byte[] Payload()
    {
        var data=new byte[13]; data[0]=(byte)(Array.IndexOf(Effects,Mode)+1); data[1]=(byte)Brightness;
        data[2]=Color.R; data[3]=Color.G; data[4]=Color.B;
        BinaryPrimitives.WriteUInt16LittleEndian(data.AsSpan(5),(ushort)PeriodMs);
        BinaryPrimitives.WriteUInt16LittleEndian(data.AsSpan(7),(ushort)DurationMs);
        BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(9),RequestId); return data;
    }
    public static LightingCommand Parse(JsonElement json)
    {
        var keys=new HashSet<string>{"mode","color","colors","brightness","period_ms","duration_seconds","continuous","request_id"};
        if (json.ValueKind!=JsonValueKind.Object || json.EnumerateObject().Any(p=>!keys.Contains(p.Name))
            || json.EnumerateObject().GroupBy(p=>p.Name).Any(g=>g.Count()>1)) throw new ControlException("灯效参数名称无效。",400);
        string mode=Text(json,"mode","");
        if (json.TryGetProperty("continuous",out var continuous) && continuous.ValueKind!=JsonValueKind.False)
            throw new ControlException("请使用 0.1～60 秒的有限时长。",400);
        if (mode=="release") return new("release",new(),0,0,0,0);
        var brightness=Integer(json,"brightness",25,0,100);
        var seconds=5d;
        if (json.TryGetProperty("duration_seconds",out var duration) && (!duration.TryGetDouble(out seconds) || !double.IsFinite(seconds)))
            throw new ControlException("时长必须为数字。",400);
        if (seconds is <0.1 or >60) throw new ControlException("时长范围为 0.1～60 秒。",400);
        var ms=(int)Math.Round(seconds*1000);
        var id=json.TryGetProperty("request_id",out var requestId)
            ? (requestId.TryGetUInt32(out var parsed) && parsed!=0 ? parsed : throw new ControlException("请求编号必须为非零 UInt32。",400)) : NewId();
        if (mode=="frame")
        {
            if (ms is <500 or >10000 || !json.TryGetProperty("colors",out var colors) || colors.ValueKind!=JsonValueKind.Array || colors.GetArrayLength()!=9)
                throw new ControlException("渐变需要九个颜色，时长为 0.5～10 秒。",400);
            return new(mode,new(),brightness,0,ms,id,colors.EnumerateArray().Select(v=>Rgb.Parse(v.GetString()??"")).ToArray());
        }
        var period=Integer(json,"period_ms",mode=="rainbow"?5000:mode=="solid"?0:1000,0,20000);
        if (mode=="solid" && period!=0) throw new ControlException("纯色的周期必须为零。",400);
        if (json.TryGetProperty("colors",out _)) throw new ControlException("只有 frame 使用 colors。",400);
        if (mode=="rainbow" && json.TryGetProperty("color",out _)) throw new ControlException("彩虹不接受单独颜色。",400);
        return Create(mode,Text(json,"color","34D399"),brightness,period,ms,id);
    }
    public static int Integer(JsonElement json,string key,int fallback,int min,int max)
    {
        if (!json.TryGetProperty(key,out var value)) return fallback;
        if (value.ValueKind!=JsonValueKind.Number || !value.TryGetInt32(out var n) || n<min || n>max)
            throw new ControlException($"{key} 必须是 {min}～{max} 的整数。",400);
        return n;
    }
    public static string Text(JsonElement json,string key,string fallback) => !json.TryGetProperty(key,out var v) ? fallback
        : v.ValueKind==JsonValueKind.String ? v.GetString()??fallback : throw new ControlException($"{key} 必须是字符串。",400);
}
