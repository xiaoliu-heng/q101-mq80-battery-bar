using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Unicode;

namespace Kryra.Core;

public sealed record Preferences(int BatteryPollIntervalSeconds=60,string Selection="auto",string Theme="system");
public sealed class PrivateStore
{
    public static readonly JsonSerializerOptions Json=new(){PropertyNamingPolicy=JsonNamingPolicy.SnakeCaseLower,WriteIndented=true,Encoder=JavaScriptEncoder.Create(UnicodeRanges.All)};
    public string Home { get; }
    public string ApiPath=>Path.Combine(Home,"api.json");
    public PrivateStore(string? home=null)
    {
        Home=Path.GetFullPath(home??Environment.GetEnvironmentVariable("KRYRA_HOME")
            ??Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Kryra"));
        Directory.CreateDirectory(Home);
        var sid=WindowsIdentity.GetCurrent().User??throw new IOException("无法确定当前 Windows 用户。");
        var acl=new DirectorySecurity(); acl.SetOwner(sid); acl.SetAccessRuleProtection(true,false);
        acl.AddAccessRule(new FileSystemAccessRule(sid,FileSystemRights.FullControl,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
        new DirectoryInfo(Home).SetAccessControl(acl);
    }
    public Preferences Load()
    {
        try
        {
            var p=JsonSerializer.Deserialize<Preferences>(File.ReadAllText(Path.Combine(Home,"settings.json")),Json)!;
            if(p.BatteryPollIntervalSeconds is <30 or >3600 || p.Theme is not ("system" or "light" or "dark") || !ValidSelection(p.Selection)) return new();
            return p;
        }
        catch { return new(); }
    }
    public static bool ValidSelection(string selection)=>selection is "auto" or "usb" or "rf"
        || ((selection.StartsWith("usb:")||selection.StartsWith("rf:")) && selection.Length<80);
    public void Save(Preferences preferences)=>Write("settings.json",preferences);
    public void Write(string file,object value)
    {
        var target=Path.Combine(Home,file); var temp=target+"."+Guid.NewGuid().ToString("N")+".tmp";
        try { File.WriteAllText(temp,JsonSerializer.Serialize(value,Json)); File.Move(temp,target,true); }
        finally { if(File.Exists(temp)) File.Delete(temp); }
    }
    public void RemoveApi(string token)
    {
        try
        {
            using var j=JsonDocument.Parse(File.ReadAllText(ApiPath));
            if(j.RootElement.GetProperty("token").GetString()==token) File.Delete(ApiPath);
        }
        catch(IOException) { } catch(JsonException) { }
    }
}
