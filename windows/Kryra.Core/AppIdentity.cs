using System.Reflection;

namespace Kryra.Core;

public static class AppIdentity
{
    public static string Version {get;}=typeof(AppIdentity).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0];
}
