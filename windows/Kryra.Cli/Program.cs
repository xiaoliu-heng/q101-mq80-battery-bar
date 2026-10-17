using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Kryra.Core;

try
{
    var arguments=args.ToList();
    if(arguments.Count==0||arguments[0] is "--help" or "help")
    {
        Console.WriteLine("Kryra Windows CLI\n  kryractl status | battery [--refresh] | refresh | settings [--poll-interval 60]\n  kryractl solid|blink|breathe HEX [--brightness 25] [--seconds 5] [--period 1000]\n  kryractl rainbow [--brightness 25] [--seconds 5] [--period 5000]\n  kryractl release | show | quit | launch\n  KRYRA_HOME selects an isolated runtime directory."); return 0;
    }
    var command=arguments[0]; arguments.RemoveAt(0);
    if(command=="launch")
    {
        var app=Path.Combine(AppContext.BaseDirectory,"Kryra.exe");
        if(!File.Exists(app)) throw new ControlException("请把 kryractl 放在 Kryra.exe 所在目录。");
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(app){UseShellExecute=true}); return 0;
    }
    var home=Environment.GetEnvironmentVariable("KRYRA_HOME")??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Kryra");
    using var config=JsonDocument.Parse(File.ReadAllText(Path.Combine(home,"api.json")));
    var root=config.RootElement;
    if(root.GetProperty("host").GetString()!="127.0.0.1") throw new ControlException("无效的本机 API 地址。");
    var port=root.GetProperty("port").GetInt32(); if(port is <1 or >65535) throw new ControlException("无效端口。");
    using var client=new HttpClient(new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false}){Timeout=TimeSpan.FromSeconds(10),BaseAddress=new Uri($"http://127.0.0.1:{port}")};
    client.DefaultRequestHeaders.Authorization=new AuthenticationHeaderValue("Bearer",root.GetProperty("token").GetString());
    string method="GET",path; Dictionary<string,object>? body=null;
    if(command=="status") path="/v1/status";
    else if(command=="battery") { path=arguments.Remove("--refresh")?"/v1/battery?refresh=true":"/v1/battery"; }
    else if(command is "refresh" or "release" or "show" or "quit") { method="POST"; path=command switch {"release"=>"/v1/lighting/release","show"=>"/v1/window/show","quit"=>"/v1/app/quit",_=>"/v1/refresh"}; }
    else if(command=="settings")
    {
        path="/v1/settings";
        if(arguments.Count>0) { method="POST"; body=new(){{"battery_poll_interval_seconds",TakeInt("--poll-interval")}}; }
    }
    else if(LightingCommand.Effects.Contains(command))
    {
        method="POST"; path="/v1/lighting"; body=new(){{"mode",command}};
        if(command!="rainbow") { if(arguments.Count==0) throw new ControlException("请提供六位颜色。",400); body["color"]=arguments[0]; arguments.RemoveAt(0); }
        foreach(var option in new[]{("--brightness","brightness"),("--period","period_ms")}) if(arguments.Contains(option.Item1)) body[option.Item2]=TakeInt(option.Item1);
        if(arguments.Contains("--seconds")) body["duration_seconds"]=double.Parse(Take("--seconds"),System.Globalization.CultureInfo.InvariantCulture);
        if(arguments.Contains("--request-id")) body["request_id"]=uint.Parse(Take("--request-id"),System.Globalization.CultureInfo.InvariantCulture);
    }
    else throw new ControlException("未知命令，请运行 kryractl --help。",400);
    if(arguments.Count>0) throw new ControlException("未知或重复参数："+string.Join(' ',arguments),400);
    using var request=new HttpRequestMessage(new HttpMethod(method),path);
    if(method=="POST") request.Content=new StringContent(JsonSerializer.Serialize(body??new(),PrivateStore.Json),Encoding.UTF8,"application/json");
    using var response=await client.SendAsync(request);
    var text=await response.Content.ReadAsStringAsync(); Console.WriteLine(text); return response.IsSuccessStatusCode?0:1;
    string Take(string option) { var at=arguments.IndexOf(option); if(at<0||at+1>=arguments.Count) throw new ControlException("缺少参数："+option,400); var value=arguments[at+1]; arguments.RemoveRange(at,2); return value; }
    int TakeInt(string option)=>int.Parse(Take(option),System.Globalization.CultureInfo.InvariantCulture);
}
catch(Exception e) { Console.Error.WriteLine(JsonSerializer.Serialize(new{error=e is FileNotFoundException?"请先启动 Kryra Windows。":e.Message},PrivateStore.Json)); return 1; }
