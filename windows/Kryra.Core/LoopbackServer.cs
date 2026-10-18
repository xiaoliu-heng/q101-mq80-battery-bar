using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Kryra.Core;

public sealed class LoopbackServer : IAsyncDisposable
{
    private readonly TcpListener listener=new(IPAddress.Loopback,0);
    private readonly CancellationTokenSource stop=new();
    private readonly SemaphoreSlim capacity=new(8,8);
    private readonly KeyboardController controller;
    private readonly PrivateStore store;
    public string Token { get; }=Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
    public int Port { get; private set; }
    public string Address=>$"http://127.0.0.1:{Port}";
    public event Action? ShowRequested;
    public event Action? QuitRequested;
    public LoopbackServer(KeyboardController controller,PrivateStore store) { this.controller=controller; this.store=store; }
    public void Start()
    {
        listener.Start(8); Port=((IPEndPoint)listener.LocalEndpoint).Port;
        store.Write("api.json",new {host="127.0.0.1",port=Port,token=Token,pid=Environment.ProcessId,app="Kryra Windows",version=AppIdentity.Version,demo=controller.Snapshot().Demo});
        _=Accept();
    }
    private async Task Accept()
    {
        while(!stop.IsCancellationRequested)
        {
            TcpClient client;
            try { client=await listener.AcceptTcpClientAsync(stop.Token); }
            catch(Exception) when(stop.IsCancellationRequested) { return; }
            if(!capacity.Wait(0)) { client.Dispose(); continue; }
            _=Serve(client);
        }
    }
    private async Task Serve(TcpClient client)
    {
        using(client)
        using(var deadline=CancellationTokenSource.CreateLinkedTokenSource(stop.Token))
        {
            deadline.CancelAfter(TimeSpan.FromSeconds(8));
            try
            {
                var stream=client.GetStream(); var header=new List<byte>(); var one=new byte[1];
                while(header.Count<8192)
                {
                    if(await stream.ReadAsync(one,deadline.Token)!=1) return;
                    header.Add(one[0]); var n=header.Count;
                    if(n>=4&&header[n-4]==13&&header[n-3]==10&&header[n-2]==13&&header[n-1]==10) break;
                }
                if(header.Count>=8192) throw new ControlException("请求头过大。",413);
                if(header.Any(b=>b>127||b==0)) throw new ControlException("请求头编码无效。",400);
                var lines=Encoding.ASCII.GetString(header.ToArray()).Split("\r\n"); var first=lines[0].Split(' ');
                if(first.Length!=3||first[2]!="HTTP/1.1"||first[0] is not ("GET" or "POST")||!first[1].StartsWith("/v1/"))
                    throw new ControlException("请求格式无效。",400);
                var headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
                foreach(var line in lines.Skip(1).Where(l=>l.Length>0))
                {
                    var colon=line.IndexOf(':');
                    if(colon<=0||line[0] is ' ' or '\t'||line[..colon].Any(c=>!char.IsAsciiLetterOrDigit(c)&&c!='-')
                        ||!headers.TryAdd(line[..colon],line[(colon+1)..].Trim())) throw new ControlException("重复或无效的请求头。",400);
                }
                if(headers.ContainsKey("Origin")||!headers.TryGetValue("Host",out var host)||host!=$"127.0.0.1:{Port}")
                    throw new ControlException("只接受本机原生客户端。",403);
                var auth=headers.GetValueOrDefault("Authorization",""); var expected=Encoding.UTF8.GetBytes("Bearer "+Token);
                var provided=Encoding.UTF8.GetBytes(auth);
                if(provided.Length!=expected.Length||!CryptographicOperations.FixedTimeEquals(expected,provided)) throw new ControlException("未授权。",401);
                if(headers.ContainsKey("Transfer-Encoding")||headers.ContainsKey("Expect")) throw new ControlException("不支持分块或分段请求。",400);
                var length=0;
                if(headers.TryGetValue("Content-Length",out var raw)&&(!int.TryParse(raw,out length)||length<0||raw.Any(c=>!char.IsAsciiDigit(c))))
                    throw new ControlException("Content-Length 无效。",400);
                if(length>16384) throw new ControlException("请求体过大。",413);
                if(first[0]=="GET"&&length!=0) throw new ControlException("GET 不接受请求体。",400);
                if(length>0&&headers.GetValueOrDefault("Content-Type","").Split(';')[0].Trim()!="application/json")
                    throw new ControlException("请求体必须使用 application/json。",400);
                var body=new byte[length]; var received=0;
                while(received<length)
                {
                    var count=await stream.ReadAsync(body.AsMemory(received),deadline.Token);
                    if(count==0) throw new ControlException("请求体不完整。",400); received+=count;
                }
                using var json=JsonDocument.Parse(length==0?"{}"u8.ToArray():body);
                var result=await Route(first[0],first[1],json.RootElement,deadline.Token);
                await Respond(stream,200,result,deadline.Token);
            }
            catch(Exception e)
            {
                var status=e is ControlException control?control.Status:e is JsonException or InvalidOperationException or FormatException?400:503;
                try { await Respond(client.GetStream(),status,new {error=e.Message},stop.Token); } catch { }
            }
            finally { capacity.Release(); }
        }
    }
    private async Task<object> Route(string method,string path,JsonElement body,CancellationToken ct)
    {
        if(method=="GET"&&path=="/v1/health") return new {app="Kryra Windows",version=AppIdentity.Version,api_version=1,demo=controller.Snapshot().Demo};
        if(method=="GET"&&path=="/v1/status") return controller.StatusJson();
        if(method=="GET"&&path=="/v1/settings") return new {battery_poll_interval_seconds=controller.Snapshot().Settings.BatteryPollIntervalSeconds};
        if(method=="GET"&&path is "/v1/battery" or "/v1/battery?refresh=true")
        {
            if(path.EndsWith("refresh=true")) await controller.RefreshAsync(ct);
            var s=controller.Snapshot(); return new {percent=s.BatteryPercent,stale=s.Stale,updated_at=s.Updated,poll_interval_seconds=s.Settings.BatteryPollIntervalSeconds,demo=s.Demo};
        }
        if(method=="POST"&&path=="/v1/refresh") { await controller.RefreshAsync(ct); return controller.StatusJson(); }
        if(method=="POST"&&path=="/v1/settings")
        {
            if(body.ValueKind!=JsonValueKind.Object||body.EnumerateObject().Count()!=1||!body.TryGetProperty("battery_poll_interval_seconds",out _))
                throw new ControlException("仅接受 battery_poll_interval_seconds。",400);
            var n=LightingCommand.Integer(body,"battery_poll_interval_seconds",60,30,3600); controller.SetPollInterval(n);
            return new {battery_poll_interval_seconds=n};
        }
        if(method=="POST"&&path=="/v1/lighting") { await controller.PlayAsync(LightingCommand.Parse(body),ct); return controller.StatusJson(); }
        if(method=="POST"&&path=="/v1/lighting/release") { await controller.ReleaseAsync(ct); return controller.StatusJson(); }
        if(method=="POST"&&path=="/v1/window/show") { ShowRequested?.Invoke(); return new {ok=true}; }
        if(method=="POST"&&path=="/v1/app/quit") { _=Task.Run(async()=>{await Task.Delay(250);QuitRequested?.Invoke();}); return new {ok=true}; }
        throw new ControlException("未找到接口。",404);
    }
    private static async Task Respond(NetworkStream stream,int status,object body,CancellationToken ct)
    {
        var data=JsonSerializer.SerializeToUtf8Bytes(body,PrivateStore.Json);
        var header=Encoding.ASCII.GetBytes($"HTTP/1.1 {status} {(status==200?"OK":"Error")}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {data.Length}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n");
        await stream.WriteAsync(header,ct); await stream.WriteAsync(data,ct);
    }
    public ValueTask DisposeAsync() { stop.Cancel(); listener.Stop(); store.RemoveApi(Token); return ValueTask.CompletedTask; }
}
