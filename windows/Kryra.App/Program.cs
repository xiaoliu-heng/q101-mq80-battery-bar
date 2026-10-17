using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using Kryra.Core;
using Microsoft.Win32;
using Forms=System.Windows.Forms;

namespace Kryra.Windows;

public static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        try
        {
            var homeAt=Array.IndexOf(args,"--home");
            if(homeAt>=0) {if(homeAt+1>=args.Length)throw new ArgumentException("--home 需要路径。");Environment.SetEnvironmentVariable("KRYRA_HOME",Path.GetFullPath(args[homeAt+1]));}
            var uiAt=Array.IndexOf(args,"--ui-test");
            if(uiAt>=0&&homeAt<0) Environment.SetEnvironmentVariable("KRYRA_HOME",Path.Combine(Path.GetFullPath(args[uiAt+1]),"profile"));
            var store=new PrivateStore();
            var identity=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(WindowsIdentity.GetCurrent().User+"|"+store.Home)))[..20];
            using var single=new Mutex(true,"Local\\Kryra.Windows."+identity,out var first);
            if(!first) {using var existing=EventWaitHandle.OpenExisting("Local\\Kryra.Show."+identity);existing.Set();return 0;}
            using var show=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\Kryra.Show."+identity);
            try { return new KryraApplication(args,store,show).Run(); }
            finally {single.ReleaseMutex();}
        }
        catch(Exception error)
        {
            var folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Kryra");Directory.CreateDirectory(folder);
            File.WriteAllText(Path.Combine(folder,"startup-error.log"),error.ToString());
            MessageBox.Show(error.Message,"Kryra 启动失败",MessageBoxButton.OK,MessageBoxImage.Error);return 1;
        }
    }
}

internal sealed class KryraApplication:Application
{
    private readonly string[] args;
    private readonly PrivateStore store;
    private readonly EventWaitHandle show;
    private KeyboardController controller=null!;
    private LoopbackServer server=null!;
    private MainWindow panel=null!;
    private Forms.NotifyIcon? tray;
    private RegisteredWaitHandle? wait;
    private bool ending;
    public KryraApplication(string[] args,PrivateStore store,EventWaitHandle show)
    {this.args=args;this.store=store;this.show=show;ShutdownMode=ShutdownMode.OnExplicitShutdown;}
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        try
        {
            var demo=args.Contains("--demo")||args.Contains("--ui-test");
            controller=new KeyboardController(demo?new DemoBackend():new WindowsHidBackend(),store);
            server=new LoopbackServer(controller,store);server.Start();
            panel=new MainWindow(controller,server,RequestQuit,store); MainWindow=panel;
            server.ShowRequested+=ShowPanel;server.QuitRequested+=RequestQuit;
            wait=ThreadPool.RegisterWaitForSingleObject(show,(_,_)=>ShowPanel(),null,Timeout.Infinite,false);
            if(!args.Contains("--headless")) CreateTray();
            controller.Changed+=UpdateTray;
            SystemEvents.PowerModeChanged+=PowerChanged;SystemEvents.UserPreferenceChanged+=PreferenceChanged;
            controller.Start();
            if(!args.Contains("--tray")&&!args.Contains("--headless"))panel.Show();
            var uiAt=Array.IndexOf(args,"--ui-test");
            if(uiAt>=0) {await panel.UiCheck(Path.GetFullPath(args[uiAt+1]));RequestQuit();}
        }
        catch(Exception error) {File.WriteAllText(Path.Combine(store.Home,"startup-error.log"),error.ToString());MessageBox.Show(error.Message,"Kryra",MessageBoxButton.OK,MessageBoxImage.Error);Shutdown(1);}
    }
    private void CreateTray()
    {
        var menu=new Forms.ContextMenuStrip{Font=new Font(Forms.Control.DefaultFont.FontFamily,10f)};
        menu.Items.Add("打开 Kryra",null,(_,_)=>ShowPanel());
        menu.Items.Add("刷新电量",null,async(_,_)=>{try{await controller.RefreshAsync();}catch{}});
        menu.Items.Add("设置",null,(_,_)=>Dispatcher.BeginInvoke(panel.ShowSettings));
        menu.Items.Add(new Forms.ToolStripSeparator());menu.Items.Add("退出 Kryra",null,(_,_)=>RequestQuit());
        tray=new Forms.NotifyIcon{Visible=true,Text="Kryra",ContextMenuStrip=menu,Icon=MakeIcon(null)};
        tray.MouseClick+=(_,e)=>{if(e.Button==Forms.MouseButtons.Left)ShowPanel();};
    }
    private void ShowPanel()=>Dispatcher.BeginInvoke(()=>{panel.Show();if(panel.WindowState==WindowState.Minimized)panel.WindowState=WindowState.Normal;panel.Activate();});
    private void UpdateTray()=>Dispatcher.BeginInvoke(()=>
    {
        if(tray==null)return;var state=controller.Snapshot(); var previous=tray.Icon;tray.Icon=MakeIcon(state.BatteryPercent);previous?.Dispose();
        var batteryLabel=state.BatteryPercent is int percent?$"{percent}%":"等待读数";
        tray.Text=$"Kryra{(state.Demo?" 演示":"")} · {batteryLabel} · {(state.Connected?state.Mode=="rf"?"2.4G":"USB":"未连接")}";
    });
    private static Icon MakeIcon(int? percent)
    {
        var dark=false;
        try{using var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");dark=(int?)key?.GetValue("SystemUsesLightTheme")==0;}catch{}
        return TrayIconRenderer.Create(percent,dark);
    }
    private async void PowerChanged(object sender,PowerModeChangedEventArgs e)
    {
        try {if(e.Mode==PowerModes.Suspend)await controller.SuspendAsync();else if(e.Mode==PowerModes.Resume)controller.Resume();}catch{}
    }
    private void PreferenceChanged(object sender,UserPreferenceChangedEventArgs e)=>Dispatcher.BeginInvoke(()=>{panel.RenderState();UpdateTray();});
    private async void RequestQuit()
    {
        if(!Dispatcher.CheckAccess()){_=Dispatcher.BeginInvoke(RequestQuit);return;}
        if(ending)return;ending=true;
        try {await controller.StopAsync();await server.DisposeAsync();}
        catch(Exception error){File.WriteAllText(Path.Combine(store.Home,"shutdown-error.log"),error.ToString());}
        finally
        {
            wait?.Unregister(null);SystemEvents.PowerModeChanged-=PowerChanged;SystemEvents.UserPreferenceChanged-=PreferenceChanged;
            if(tray!=null){tray.Visible=false;tray.Icon?.Dispose();tray.Dispose();}panel.PrepareExit();Shutdown();
        }
    }
}
