using System.Text.Json;

namespace Kryra.Core;

public sealed record SkillInstallation(string Target,string Message,bool Installed,bool CanInstall,bool NeedsUpdate);

public sealed class SkillInstaller
{
    public const string Owner="local.xiaoliu.kryra.windows";
    private const string Marker=".kryra.json";
    private readonly string appDirectory,runtimeHome;
    public static string DefaultRoot=>Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".agents","skills");
    public SkillInstaller(string appDirectory,string runtimeHome)
    {this.appDirectory=Path.GetFullPath(appDirectory);this.runtimeHome=Path.GetFullPath(runtimeHome);}
    public SkillInstallation Inspect(string root)
    {
        var target=Path.Combine(Path.GetFullPath(root),"kryra");
        if(File.Exists(target))return new(target,"目标位置已有同名文件，请选择其他目录。",false,false,false);
        if(!Directory.Exists(target))return new(target,"尚未安装",false,true,false);
        if(!IsOwned(target))return new(target,"此目录已有其他来源的 Skill，请选择其他目录。",false,false,false);
        try
        {
            RejectLinks(target);
            using var marker=JsonDocument.Parse(File.ReadAllText(Path.Combine(target,Marker)));
            using var binding=JsonDocument.Parse(File.ReadAllText(Path.Combine(target,"app.json")));
            var current=marker.RootElement.GetProperty("app_version").GetString()==AppIdentity.Version
                &&binding.RootElement.GetProperty("cli_path").GetString()==Path.Combine(appDirectory,"kryractl.exe")
                &&binding.RootElement.GetProperty("runtime_home").GetString()==runtimeHome
                &&File.Exists(Path.Combine(target,"SKILL.md"));
            return new(target,current?$"已安装 · v{AppIdentity.Version}":"已安装，可更新或修复",true,true,!current);
        }
        catch{return new(target,"已安装，可更新或修复",true,true,true);}
    }
    public string Install(string root)
    {
        var parent=Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
        var target=Path.Combine(parent,"kryra");var status=Inspect(parent);
        if(!status.CanInstall)throw new IOException(status.Message);
        var resource=Path.Combine(appDirectory,"Skill","kryra");
        var required=new[]{"SKILL.md",Path.Combine("agents","openai.yaml")};
        if(required.Any(file=>!File.Exists(Path.Combine(resource,file)))||!File.Exists(Path.Combine(appDirectory,"kryractl.exe")))
            throw new IOException("Skill 安装资源不完整，请使用完整发行目录。" );
        Directory.CreateDirectory(parent);
        if((File.GetAttributes(parent)&FileAttributes.ReparsePoint)!=0)throw new IOException("请选择实际文件夹作为安装父目录。" );
        if(Directory.Exists(target))RejectLinks(target);
        var stage=Path.Combine(parent,".kryra-stage-"+Guid.NewGuid().ToString("N"));
        var backup=Path.Combine(parent,".kryra-backup-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        try
        {
            File.WriteAllText(Path.Combine(stage,Marker),JsonSerializer.Serialize(new{owner=Owner,app_version=AppIdentity.Version},PrivateStore.Json));
            foreach(var file in required)
            {var destination=Path.Combine(stage,file);Directory.CreateDirectory(Path.GetDirectoryName(destination)!);File.Copy(Path.Combine(resource,file),destination);}
            File.WriteAllText(Path.Combine(stage,"app.json"),JsonSerializer.Serialize(new{cli_path=Path.Combine(appDirectory,"kryractl.exe"),runtime_home=runtimeHome},PrivateStore.Json));
            var existed=Directory.Exists(target);
            if(existed)Directory.Move(target,backup);
            try{Directory.Move(stage,target);}
            catch{if(existed)Directory.Move(backup,target);throw;}
            // A failed cleanup preserves the owned backup instead of undoing a successful install.
            if(existed)try{DeleteOwnedSibling(parent,backup);}catch(IOException){}catch(UnauthorizedAccessException){}
            return target;
        }
        finally{if(Directory.Exists(stage))DeleteOwnedSibling(parent,stage);}
    }
    private static bool IsOwned(string path)
    {
        try{using var marker=JsonDocument.Parse(File.ReadAllText(Path.Combine(path,Marker)));return marker.RootElement.GetProperty("owner").GetString()==Owner;}
        catch{return false;}
    }
    private static void RejectLinks(string directory)
    {
        if((File.GetAttributes(directory)&FileAttributes.ReparsePoint)!=0)throw new IOException("Skill 目录含链接，请选择其他安装位置。" );
        foreach(var entry in Directory.EnumerateFileSystemEntries(directory))
        {
            var attributes=File.GetAttributes(entry);
            if((attributes&FileAttributes.ReparsePoint)!=0)throw new IOException("Skill 目录含链接，请选择其他安装位置。" );
            if((attributes&FileAttributes.Directory)!=0)RejectLinks(entry);
        }
    }
    private static void DeleteOwnedSibling(string parent,string directory)
    {
        var full=Path.GetFullPath(directory);
        if(!string.Equals(Path.GetDirectoryName(full),Path.GetFullPath(parent),StringComparison.OrdinalIgnoreCase)
            ||!Path.GetFileName(full).StartsWith(".kryra-",StringComparison.Ordinal)||!IsOwned(full))
            throw new IOException("拒绝清理不属于此安装操作的目录。" );
        RejectLinks(full);Directory.Delete(full,true);
    }
}
