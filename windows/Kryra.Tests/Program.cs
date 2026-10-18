using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Kryra.Core;

var results=new List<object>();
async Task Check(string name,Func<Task> test)
{
    try {await test();results.Add(new{name,pass=true});Console.WriteLine("PASS "+name);}
    catch(Exception e){results.Add(new{name,pass=false,error=e.ToString()});Console.WriteLine("FAIL "+name+": "+e.Message);}
}
void Require(bool condition,string message="Assertion failed") {if(!condition)throw new Exception(message);}
void Reject(Action action) {try {action();}catch(ControlException){return;}throw new Exception("Expected rejection");}
PrivateStore Store()=>new(Path.Combine(Path.GetTempPath(),"kryra-tests",Guid.NewGuid().ToString("N")));
JsonElement Json(string text)=>JsonDocument.Parse(text).RootElement.Clone();
await Check("real USB/RF v3.1.0 captures decode",()=>
{
    using var doc=JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory,"fixtures","hardware-v3.1.0.json")));
    foreach(var sample in doc.RootElement.GetProperty("samples").EnumerateArray())
    {
        var r=Convert.FromHexString(sample.GetProperty("request").GetString()!);var reply=Convert.FromHexString(sample.GetProperty("reply").GetString()!);
        var state=Wire.Decode(r,reply);Require(state.Version=="3.1.0"&&state.Build==3&&state.Connection==sample.GetProperty("transport").GetString());
        var corrupt=reply.ToArray();corrupt[4]++;Reject(()=>Wire.Decode(r,corrupt));corrupt=reply.ToArray();corrupt[27]=0;Reject(()=>Wire.Decode(r,corrupt));
    }
    return Task.CompletedTask;
});
await Check("PLAY payload bounds, color and little endian",()=>
{
    var c=LightingCommand.Create("blink","34D399",25,1000,5000,0x12345678);var p=c.Payload();
    Require(Convert.ToHexString(p)=="021934D399E803881378563412");
    foreach(var invalid in new[]{"##FF0000"," FF00 ","GGFFFF","FF00"})Reject(()=>Rgb.Parse(invalid));
    Reject(()=>LightingCommand.Create("breathe",periodMs:499));Reject(()=>LightingCommand.Create(durationMs:60001));
    Reject(()=>LightingCommand.Parse(Json("{\"mode\":\"solid\",\"brightness\":true}")));
    Reject(()=>LightingCommand.Parse(Json("{\"mode\":\"rainbow\",\"continuous\":true}")));
    Reject(()=>LightingCommand.Parse(Json("{\"mode\":\"rainbow\",\"color\":\"FF0000\"}")));
    Reject(()=>Wire.Packet(3,1));return Task.CompletedTask;
});
await Check("settings persist; changes never query HID",async()=>
{
    var store=Store();var b=new DemoBackend();var c=new KeyboardController(b,store);await c.RefreshAsync();var before=b.Requests;
    c.SetPollInterval(120);c.SetTheme("dark");await Task.Delay(80);Require(b.Requests==before);Require(store.Load().BatteryPollIntervalSeconds==120);
    Reject(()=>c.SetPollInterval(29));await c.StopAsync();Require(b.Requests==before,"Read-only exit must not RELEASE external lighting");
});
await Check("single PLAY, natural expiry, replay dedup and no heartbeat",async()=>
{
    var b=new DemoBackend();var c=new KeyboardController(b,Store());var command=LightingCommand.Create("solid",durationMs:200,id:123);
    await c.PlayAsync(command);Require(c.Snapshot().OwnActive);var before=b.Requests;await Task.Delay(350);
    Require(!c.Snapshot().OwnActive&&b.Requests==before);Require(!b.Commands.Contains(3)&&!b.Commands.Contains(2));
    await c.PlayAsync(command);Require(!c.Snapshot().OwnActive,"Expired duplicate must not restart");await c.StopAsync();
});
await Check("explicit release, suspend, resume do not replay",async()=>
{
    var b=new DemoBackend();var c=new KeyboardController(b,Store());await c.PlayAsync(LightingCommand.Create("rainbow",durationMs:3000));
    await c.SuspendAsync();Require(!c.Snapshot().OwnActive&&c.Snapshot().Suspended);Require(b.Commands.Count(x=>x==2)==1);
    c.Resume();await Task.Delay(100);Require(b.Commands.Count(x=>x==5)==1);await c.StopAsync();
});
await Check("queued cancellation sends no PLAY; HID work is serialized",async()=>
{
    var b=new GatedBackend();var c=new KeyboardController(b,Store());
    var first=c.RefreshAsync();await b.Entered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    using var cancel=new CancellationTokenSource();
    var abandoned=c.PlayAsync(LightingCommand.Create(),cancel.Token);cancel.Cancel();
    try {await abandoned;throw new Exception("Expected queued cancellation");}catch(OperationCanceledException) { }
    var second=c.RefreshAsync();await Task.Delay(80);
    Require(b.MaxConcurrent==1&&b.Inner.Requests==0,"Queued work must not enter the open HID transfer");
    b.Continue.TrySetResult();await Task.WhenAll(first,second);
    Require(b.MaxConcurrent==1&&b.Inner.Commands.SequenceEqual(new byte[]{0,0}));await c.StopAsync();
});
await Check("exit preserves a newer external notification",async()=>
{
    var b=new DemoBackend();var c=new KeyboardController(b,Store());
    await c.PlayAsync(LightingCommand.Create("solid",durationMs:3000,id:101));
    using(var external=b.Open(b.Enumerate()[0]))
        external.Request(Wire.Packet(Wire.Play,200,LightingCommand.Create("blink",durationMs:3000,id:102).Payload()),CancellationToken.None);
    await c.StopAsync();Require(!b.Commands.Contains(Wire.Release),"Exit must not release another controller's newer PLAY");
});
await Check("loopback auth, Origin/Host validation and strict settings",async()=>
{
    var store=Store();var backend=new DemoBackend();var controller=new KeyboardController(backend,store);await controller.RefreshAsync();
    await using var api=new LoopbackServer(controller,store);api.Start();
    using var http=new HttpClient(new HttpClientHandler{UseProxy=false}){BaseAddress=new Uri(api.Address)};
    Require((await http.GetAsync("/v1/health")).StatusCode==HttpStatusCode.Unauthorized);
    http.DefaultRequestHeaders.Authorization=new AuthenticationHeaderValue("Bearer",api.Token);
    Require((await http.GetAsync("/v1/health")).IsSuccessStatusCode);
    using(var origin=new HttpRequestMessage(HttpMethod.Get,"/v1/status")){origin.Headers.Add("Origin","http://127.0.0.1:8766");Require((await http.SendAsync(origin)).StatusCode==HttpStatusCode.Forbidden);}
    using(var host=new HttpRequestMessage(HttpMethod.Get,"/v1/status")){host.Headers.Host="evil.example";Require((await http.SendAsync(host)).StatusCode==HttpStatusCode.Forbidden);}
    var before=backend.Requests;
    foreach(var body in new[]{"{\"battery_poll_interval_seconds\":true}","{\"battery_poll_interval_seconds\":30.5}","{\"unknown\":60}","{\"battery_poll_interval_seconds\":29}"})
    {using var bad=await http.PostAsync("/v1/settings",new StringContent(body,Encoding.UTF8,"application/json"));Require(bad.StatusCode==HttpStatusCode.BadRequest);}
    using(var ok=await http.PostAsync("/v1/settings",new StringContent("{\"battery_poll_interval_seconds\":120}",Encoding.UTF8,"application/json")))Require(ok.IsSuccessStatusCode);
    Require(backend.Requests==before);
    using var tcp=new TcpClient();await tcp.ConnectAsync(IPAddress.Loopback,api.Port);var stream=tcp.GetStream();
    await stream.WriteAsync(Encoding.ASCII.GetBytes($"GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1:{api.Port}\r\nAuthorization: Bearer {api.Token}\r\nAuthorization: Bearer {api.Token}\r\n\r\n"));
    var buffer=new byte[1024];var n=await stream.ReadAsync(buffer);Require(Encoding.ASCII.GetString(buffer,0,n).StartsWith("HTTP/1.1 400"));
    await controller.StopAsync();
});
await Check("native Win32 HID enumeration filters collections",()=>
{
    foreach(var device in new WindowsHidBackend().Enumerate())Require(device.InputLength==33&&device.OutputLength==33&&device.Transport is "usb" or "rf");
    return Task.CompletedTask;
});
string SkillApp()
{
    var app=Path.Combine(Path.GetTempPath(),"kryra-skill-tests",Guid.NewGuid().ToString("N"));
    var bundle=Path.Combine(app,"Skill","kryra");Directory.CreateDirectory(Path.Combine(bundle,"agents"));
    File.WriteAllText(Path.Combine(app,"kryractl.exe"),"test fixture, not executable");
    foreach(var file in new[]{"SKILL.md",Path.Combine("agents","openai.yaml")})File.Copy(Path.Combine(AppContext.BaseDirectory,"skill-fixture",file),Path.Combine(bundle,file));
    return app;
}
await Check("Skill installs, repairs bindings and stores no credential",()=>
{
    var app=SkillApp();var root=Path.Combine(app,"test-skills");var home=Path.Combine(app,"runtime");
    var installer=new SkillInstaller(app,home);Require(!installer.Inspect(root).Installed);
    var target=installer.Install(root);Require(installer.Inspect(root) is {Installed:true,NeedsUpdate:false});
    using(var binding=JsonDocument.Parse(File.ReadAllText(Path.Combine(target,"app.json"))))
    {
        Require(binding.RootElement.GetProperty("cli_path").GetString()==Path.Combine(app,"kryractl.exe"));
        Require(binding.RootElement.GetProperty("runtime_home").GetString()==home);
        Require(binding.RootElement.EnumerateObject().Select(p=>p.Name).Order().SequenceEqual(new[]{"cli_path","runtime_home"}));
    }
    File.WriteAllText(Path.Combine(target,"app.json"),"{}");Require(installer.Inspect(root).NeedsUpdate);
    installer.Install(root);Require(installer.Inspect(root) is {Installed:true,NeedsUpdate:false});
    Require(!Directory.EnumerateDirectories(root).Any(p=>Path.GetFileName(p).StartsWith(".kryra-")));return Task.CompletedTask;
});
await Check("Skill refuses foreign directories and incomplete update keeps existing install",()=>
{
    var app=SkillApp();var root=Path.Combine(app,"test-skills");var target=Path.Combine(root,"kryra");Directory.CreateDirectory(target);
    var foreign=Path.Combine(target,"SKILL.md");File.WriteAllText(foreign,"foreign skill");var installer=new SkillInstaller(app,Path.Combine(app,"runtime"));
    Require(!installer.Inspect(root).CanInstall);
    try{installer.Install(root);throw new Exception("Expected foreign directory rejection");}catch(IOException){}
    Require(File.ReadAllText(foreign)=="foreign skill");
    var ownedRoot=Path.Combine(app,"owned-skills");var owned=installer.Install(ownedRoot);var original=File.ReadAllBytes(Path.Combine(owned,"SKILL.md"));
    File.Move(Path.Combine(app,"Skill","kryra","agents","openai.yaml"),Path.Combine(app,"openai.yaml.saved"));
    try{installer.Install(ownedRoot);throw new Exception("Expected incomplete bundle rejection");}catch(IOException){}
    Require(File.ReadAllBytes(Path.Combine(owned,"SKILL.md")).SequenceEqual(original));return Task.CompletedTask;
});
var failed=results.Count(r=>JsonSerializer.SerializeToElement(r).GetProperty("pass").GetBoolean()==false);
var output=Environment.GetEnvironmentVariable("KRYRA_TEST_REPORT");if(output!=null){Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output))!);File.WriteAllText(output,JsonSerializer.Serialize(new{passed=results.Count-failed,failed,results},PrivateStore.Json));}
Console.WriteLine($"{results.Count-failed} passed, {failed} failed");return failed==0?0:1;

sealed class GatedBackend : IHidBackend
{
    public DemoBackend Inner {get;}=new();
    public TaskCompletionSource Entered {get;}=new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource Continue {get;}=new(TaskCreationOptions.RunContinuationsAsynchronously);
    private int first=1,active,maximum;
    public int MaxConcurrent=>Volatile.Read(ref maximum);
    public bool Demo=>true;
    public IReadOnlyList<DeviceInfo> Enumerate()=>Inner.Enumerate();
    public IHidConnection Open(DeviceInfo device)=>new Connection(this,Inner.Open(device));
    private sealed class Connection(GatedBackend parent,IHidConnection inner):IHidConnection
    {
        public byte[] Request(byte[] request,CancellationToken cancellation)
        {
            var count=Interlocked.Increment(ref parent.active);
            Interlocked.Exchange(ref parent.maximum,Math.Max(count,parent.MaxConcurrent));
            try
            {
                if(Interlocked.Exchange(ref parent.first,0)==1)
                {parent.Entered.TrySetResult();parent.Continue.Task.Wait(cancellation);}
                return inner.Request(request,cancellation);
            }
            finally {Interlocked.Decrement(ref parent.active);}
        }
        public void Dispose()=>inner.Dispose();
    }
}
