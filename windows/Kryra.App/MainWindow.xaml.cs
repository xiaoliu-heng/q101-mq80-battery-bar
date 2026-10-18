using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Kryra.Core;
using Microsoft.Win32;

namespace Kryra.Windows;

public partial class MainWindow:Window
{
    public sealed record Choice(string Label,object Value);
    private readonly KeyboardController controller;
    private readonly LoopbackServer server;
    private readonly Action quit;
    private readonly PrivateStore store;
    private readonly SkillInstaller skillInstaller;
    private string skillRoot;
    private bool installingSkill;
    private bool updating=true,busy,allowClose;
    private string mode="rainbow",color="34D399",theme="";
    private int brightness=25,durationMs=5000,periodMs=5000;
    private uint adopted;
    private string selections="";
    private readonly DispatcherTimer animation;
    public MainWindow(KeyboardController controller,LoopbackServer server,Action quit,PrivateStore store)
    {
        this.controller=controller; this.server=server; this.quit=quit;this.store=store;
        skillInstaller=new SkillInstaller(AppContext.BaseDirectory,store.Home);
        skillRoot=controller.Snapshot().Demo?Path.Combine(store.Home,"test-skills"):LoadSkillRoot();
        InitializeComponent();
        DurationPicker.ItemsSource=new[]{3,5,10,30,60}.Select(s=>new Choice($"◷ {s} 秒后恢复",s*1000)).ToArray();
        PeriodPicker.ItemsSource=new[]{500,1000,2000,5000,10000,20000}.Select(ms=>new Choice($"周期 {ms/1000d:0.#}s",ms)).ToArray();
        PollPicker.ItemsSource=new[]{30,60,120,300,600,1800,3600}.Select(s=>new Choice(s<60?$"每 {s} 秒":s<3600?$"每 {s/60} 分钟":"每 1 小时",s)).ToArray();
        ThemePicker.ItemsSource=new[]{new Choice("跟随 Windows","system"),new Choice("浅色","light"),new Choice("深色","dark")};
        animation=new DispatcherTimer(TimeSpan.FromMilliseconds(50),DispatcherPriority.Background,(_,_)=>Tick(),Dispatcher);
        controller.Changed+=ControllerChanged;
        SourceInitialized+=(_,_)=>ApplyTheme(true);
        IsVisibleChanged+=(_,_)=>{if(IsVisible)animation.Start();else animation.Stop();};
        Closed+=(_,_)=>{animation.Stop();controller.Changed-=ControllerChanged;};
        updating=false; RenderState(); RenderDraft();
    }
    private void ControllerChanged()=>Dispatcher.BeginInvoke(RenderState);
    public void RenderState()
    {
        var s=controller.Snapshot(); updating=true;
        Title=s.Demo?"Kryra · 演示":"Kryra"; DemoBadge.Visibility=s.Demo?Visibility.Visible:Visibility.Collapsed;
        BatteryText.Text=s.BatteryPercent?.ToString()??"—";
        BatteryDetail.Text=s.Updated==null?"尚无设备读数":s.Stale?$"上次读数 · {s.Updated.Value.ToLocalTime():HH:mm}":$"{(s.Mode=="rf"?"2.4G":"USB")} 已连接 · {s.Updated.Value.ToLocalTime():HH:mm} 更新";
        FirmwareLabel.Text=s.Version==null?"尚未读取兼容固件":$"固件 v{s.Version} · 构建 {s.Build} · {(s.Connected?"已连接":"上次连接")}";
        VersionFooter.Text=$"Kryra Windows {AppIdentity.Version}{(s.Demo?" · 演示":"")} · 固件 v3.1.0";
        UpdateSkillState();
        if(!PollPicker.Items.Cast<Choice>().Any(c=>(int)c.Value==s.Settings.BatteryPollIntervalSeconds))
            PollPicker.ItemsSource=new[]{new Choice($"每 {s.Settings.BatteryPollIntervalSeconds} 秒",s.Settings.BatteryPollIntervalSeconds)}.Concat(PollPicker.Items.Cast<Choice>()).ToArray();
        PollPicker.SelectedValue=s.Settings.BatteryPollIntervalSeconds; ThemePicker.SelectedValue=s.Settings.Theme;
        ApiAddress.Text=server.Address; ApiAddress.ToolTip=server.Address;
        var identity=string.Join(';',s.Devices.Select(d=>d.Id))+"|"+s.Settings.Selection;
        if(identity!=selections)
        {
            var choices=new List<Choice>{new("自动选择 · USB 优先","auto"),new("USB 有线","usb"),new("2.4G 无线","rf")};
            if(s.Devices.GroupBy(d=>d.Transport).Any(g=>g.Count()>1)) choices.AddRange(s.Devices.Select(d=>new Choice($"{d.Name} · {d.Id.Split(':').Last()[..6]}",d.Id)));
            if(!choices.Any(c=>(string)c.Value==s.Settings.Selection)) choices.Add(new("指定设备 · 未连接",s.Settings.Selection));
            DevicePicker.ItemsSource=choices; DevicePicker.SelectedValue=s.Settings.Selection; selections=identity;
        }
        if(s.Lighting!=null&&s.Lighting.RequestId!=adopted&&s.OwnActive&&s.Lighting.Mode!="frame")
        {
            adopted=s.Lighting.RequestId; mode=s.Lighting.Mode; color=s.Lighting.Mode=="rainbow"?color:s.Lighting.Color.Hex;
            brightness=s.Lighting.Brightness; durationMs=s.Lighting.DurationMs; periodMs=s.Lighting.PeriodMs;
            Preview.Start=Stopwatch.GetTimestamp(); Preview.PhaseOffset=s.ElapsedSeconds; RenderDraft();
        }
        SendButton.IsEnabled=!busy&&!s.Suspended&&s.Connected; RestoreButton.IsEnabled=SendButton.IsEnabled; RefreshButton.IsEnabled=!busy&&!s.Suspended;
        Hint.Text=s.Message; Hint.ToolTip=s.Message;
        try { using var run=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"); LoginStartup.IsChecked=run?.GetValue("KryraWindows") is string value&&value.Contains(Environment.ProcessPath??"never"); } catch { LoginStartup.IsChecked=false; }
        updating=false; ApplyTheme(); Tick();
    }
    private void Tick()
    {
        if(!IsVisible) return;
        var s=controller.Snapshot(); LightingState.Text=s.Suspended?"● 查询已暂停":s.OwnActive?$"● 提醒剩余 {s.RemainingMs/1000d:0.0}s":s.ExternalActive?"● 外部程序控灯":"● 灯带预览";
        Preview.InvalidateVisual();
    }
    private void RenderDraft()
    {
        var previous=updating; updating=true;
        foreach(var button in new[]{SolidButton,BlinkButton,BreatheButton,RainbowButton})
        {
            var selected=(string)button.Tag==mode;
            button.SetResourceReference(Control.BackgroundProperty,selected?"Glass":"TransparentBrush");
            button.FontWeight=selected?FontWeights.SemiBold:FontWeights.Normal;
            button.SetResourceReference(Control.ForegroundProperty,selected?"Foreground":"Muted");
        }
        RainbowColors.Visibility=mode=="rainbow"?Visibility.Visible:Visibility.Collapsed; SolidColors.Visibility=mode=="rainbow"?Visibility.Collapsed:Visibility.Visible;
        Brightness.Value=brightness; BrightnessText.Text=$"{brightness}%"; HexColor.Text=color;
        if(!DurationPicker.Items.Cast<Choice>().Any(c=>(int)c.Value==durationMs)) DurationPicker.ItemsSource=new[]{new Choice($"◷ {durationMs/1000d:0.#} 秒后恢复",durationMs)}.Concat(DurationPicker.Items.Cast<Choice>()).ToArray();
        if(mode!="solid"&&!PeriodPicker.Items.Cast<Choice>().Any(c=>(int)c.Value==periodMs))
            PeriodPicker.ItemsSource=new[]{new Choice($"周期 {periodMs/1000d:0.###}s",periodMs)}.Concat(PeriodPicker.Items.Cast<Choice>()).ToArray();
        DurationPicker.SelectedValue=durationMs; PeriodPicker.SelectedValue=periodMs; PeriodPicker.Visibility=mode=="solid"?Visibility.Hidden:Visibility.Visible;
        Preview.Mode=mode; Preview.Color=Rgb.Parse(color); Preview.Brightness=brightness; Preview.PeriodMs=periodMs;
        Preview.InvalidateVisual(); updating=previous;
    }
    private async Task UiTask(Func<Task> action)
    {
        if(busy)return; busy=true; RenderState();
        try { await action(); }
        catch(Exception e) { Hint.Text=e.Message; Hint.ToolTip=e.ToString(); }
        finally { busy=false; RenderState(); }
    }
    private async void RefreshClick(object sender,RoutedEventArgs e)=>await UiTask(()=>controller.RefreshAsync());
    private async void SendClick(object sender,RoutedEventArgs e)
    {
        if(!CommitColor())return;
        await UiTask(()=>controller.PlayAsync(LightingCommand.Create(mode,color,brightness,periodMs,durationMs)));
    }
    private async void RestoreClick(object sender,RoutedEventArgs e)=>await UiTask(()=>controller.ReleaseAsync());
    private void EffectClick(object sender,RoutedEventArgs e)
    {
        mode=(string)((Button)sender).Tag; periodMs=mode=="rainbow"?5000:mode=="solid"?0:1000;
        Preview.Start=Stopwatch.GetTimestamp(); Preview.PhaseOffset=0; RenderDraft();
    }
    private void PresetClick(object sender,RoutedEventArgs e) { color=(string)((Button)sender).Tag; RenderDraft(); }
    private void BrightnessChanged(object sender,RoutedPropertyChangedEventArgs<double> e) { if(updating||BrightnessText==null)return; brightness=(int)Math.Round(e.NewValue); RenderDraft(); }
    private void DurationChanged(object sender,SelectionChangedEventArgs e) { if(!updating&&DurationPicker.SelectedValue is int value) {durationMs=value;RenderDraft();} }
    private void PeriodChanged(object sender,SelectionChangedEventArgs e) { if(!updating&&PeriodPicker.SelectedValue is int value) {periodMs=value;Preview.Start=Stopwatch.GetTimestamp();RenderDraft();} }
    private bool CommitColor()
    {
        try { color=Rgb.Parse(HexColor.Text).Hex; RenderDraft(); return true; }
        catch(Exception e) { Hint.Text=e.Message;Hint.ToolTip=e.Message;HexColor.Text=color;return false; }
    }
    private void HexKeyDown(object sender,KeyEventArgs e) { if(e.Key==Key.Enter) {CommitColor();e.Handled=true;} }
    private void HexLostFocus(object sender,RoutedEventArgs e) { if(!updating)CommitColor(); }
    private void PickColorClick(object sender,RoutedEventArgs e)
    {
        var rgb=Rgb.Parse(color); using var picker=new System.Windows.Forms.ColorDialog{FullOpen=true,Color=System.Drawing.Color.FromArgb(rgb.R,rgb.G,rgb.B)};
        if(picker.ShowDialog(new DialogOwner(new WindowInteropHelper(this).Handle))==System.Windows.Forms.DialogResult.OK) {color=$"{picker.Color.R:X2}{picker.Color.G:X2}{picker.Color.B:X2}";RenderDraft();}
    }
    private async void DeviceChanged(object sender,SelectionChangedEventArgs e)
    { if(!updating&&DevicePicker.SelectedValue is string selected) await UiTask(()=>controller.SelectAsync(selected)); }
    private void PollChanged(object sender,SelectionChangedEventArgs e) { if(!updating&&PollPicker.SelectedValue is int value)controller.SetPollInterval(value); }
    private void ThemeChanged(object sender,SelectionChangedEventArgs e) { if(!updating&&ThemePicker.SelectedValue is string value)controller.SetTheme(value); }
    public void ShowSettings() { SkillPage.Visibility=Visibility.Collapsed;SettingsPage.Visibility=Visibility.Visible;LightingPage.Visibility=Visibility.Collapsed;UpdateSkillState();Show();Activate(); }
    private void SettingsClick(object sender,RoutedEventArgs e)=>ShowSettings();
    private void DoneClick(object sender,RoutedEventArgs e) {SettingsPage.Visibility=Visibility.Collapsed;LightingPage.Visibility=Visibility.Visible;}
    private void CopyCliClick(object sender,RoutedEventArgs e) { Clipboard.SetText('"'+Path.Combine(AppContext.BaseDirectory,"kryractl.exe")+'"'+" battery --refresh"); }
    private void HelpClick(object sender,RoutedEventArgs e)
    {
        var file=Path.Combine(AppContext.BaseDirectory,"使用说明.html");
        if(File.Exists(file))Process.Start(new ProcessStartInfo(file){UseShellExecute=true});
        else Hint.Text="请查看源代码 windows/README.md。";
    }
    private string LoadSkillRoot()
    {
        try{using var saved=System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(store.Home,"skill-settings.json")));return Path.GetFullPath(saved.RootElement.GetProperty("root").GetString()!);}
        catch{return SkillInstaller.DefaultRoot;}
    }
    private void UpdateSkillState()
    {
        try
        {
            var state=skillInstaller.Inspect(skillRoot);
            SkillSummary.Text=state.Message;SkillSummary.ToolTip=state.Target;
            SkillPath.Text=state.Target;SkillPath.ToolTip=state.Target;
            SkillMessage.Text=state.Message;SkillInstallButton.Content=installingSkill?"正在安装…":state.Installed?"更新 Skill":"安装 Skill";
            SkillInstallButton.IsEnabled=!installingSkill&&state.CanInstall;SkillOpenButton.IsEnabled=state.Installed;
        }
        catch(Exception e){SkillSummary.Text="请检查安装位置";SkillMessage.Text=e.Message;SkillInstallButton.IsEnabled=false;}
    }
    private void SkillManageClick(object sender,RoutedEventArgs e)
    {SettingsPage.Visibility=Visibility.Collapsed;SkillPage.Visibility=Visibility.Visible;UpdateSkillState();}
    private void SkillBackClick(object sender,RoutedEventArgs e)=>ShowSettings();
    private void SkillChooseClick(object sender,RoutedEventArgs e)
    {
        if(installingSkill)return;
        using var picker=new System.Windows.Forms.FolderBrowserDialog{Description="选择 Skill 安装父目录（会创建 kryra 子目录）",UseDescriptionForTitle=true,SelectedPath=skillRoot,ShowNewFolderButton=true};
        if(picker.ShowDialog(new DialogOwner(new WindowInteropHelper(this).Handle))==System.Windows.Forms.DialogResult.OK)
        {skillRoot=Path.GetFullPath(picker.SelectedPath);store.Write("skill-settings.json",new{root=skillRoot});UpdateSkillState();}
    }
    private void SkillDefaultClick(object sender,RoutedEventArgs e)
    {
        if(installingSkill)return;
        skillRoot=controller.Snapshot().Demo?Path.Combine(store.Home,"test-skills"):SkillInstaller.DefaultRoot;
        store.Write("skill-settings.json",new{root=skillRoot});UpdateSkillState();
    }
    private async Task InstallSkillAsync()
    {
        if(installingSkill)return;installingSkill=true;UpdateSkillState();
        try{await Task.Run(()=>skillInstaller.Install(skillRoot));installingSkill=false;UpdateSkillState();SkillMessage.Text="安装完成。可使用 $kryra 查询电量或发送提醒；未出现时请重新打开 Codex。";}
        catch(Exception e){installingSkill=false;UpdateSkillState();SkillMessage.Text=e.Message;}
    }
    private async void SkillInstallClick(object sender,RoutedEventArgs e)=>await InstallSkillAsync();
    private void SkillOpenClick(object sender,RoutedEventArgs e)
    {
        var target=skillInstaller.Inspect(skillRoot).Target;
        if(Directory.Exists(target))Process.Start(new ProcessStartInfo(target){UseShellExecute=true});
    }
    private void SkillCopyClick(object sender,RoutedEventArgs e)=>Clipboard.SetText("使用 $kryra 查询键盘电量，或发送三秒紫色呼吸提醒。");
    private sealed class DialogOwner(IntPtr handle):System.Windows.Forms.IWin32Window {public IntPtr Handle=>handle;}
    private void StartupChanged(object sender,RoutedEventArgs e)
    {
        if(updating||controller.Snapshot().Demo)return;
        try { using var key=Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"); if(LoginStartup.IsChecked==true)key.SetValue("KryraWindows",'"'+Environment.ProcessPath+'"'+" --tray"); else key.DeleteValue("KryraWindows",false); }
        catch(Exception error) { Hint.Text=error.Message; }
    }
    private void QuitClick(object sender,RoutedEventArgs e)=>quit();
    private void WindowClosing(object? sender,CancelEventArgs e) { if(!allowClose) {e.Cancel=true;Hide();} }
    public void PrepareExit()=>allowClose=true;
    private void ApplyTheme(bool force=false)
    {
        var preference=controller.Snapshot().Settings.Theme; var dark=preference=="dark";
        if(preference=="system") try {using var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");dark=(int?)key?.GetValue("AppsUseLightTheme")==0;} catch { }
        var identity=$"{dark}:{SystemParameters.HighContrast}"; if(theme==identity&&!force)return;theme=identity;
        SolidColorBrush Brush(string text)=>(SolidColorBrush)new BrushConverter().ConvertFromString(text)!;
        Resources["Background"]=SystemParameters.HighContrast?SystemColors.WindowBrush:Brush(dark?"#202925":"#EEF1EF");
        Resources["Foreground"]=SystemParameters.HighContrast?SystemColors.WindowTextBrush:Brush(dark?"#EAF3EE":"#1D2B25");
        Resources["Muted"]=SystemParameters.HighContrast?SystemColors.WindowTextBrush:Brush(dark?"#A6B6AC":"#607167");
        Resources["Card"]=Brush(dark?"#313E36":"#DEE4DF"); Resources["Glass"]=Brush(dark?"#FF445149":"#FFFBFCFB");
        Resources["Edge"]=SystemParameters.HighContrast?SystemColors.WindowTextBrush:Brush(dark?"#5A6B60":"#FFFFFF");
        Resources["AccentSurface"]=Brush(dark?"#285E50":"#CFE9E1"); Resources["TransparentBrush"]=Brush("#00FFFFFF");
        if(new WindowInteropHelper(this).Handle!=IntPtr.Zero)
        {
            var value=dark?1:0; DwmSetWindowAttribute(new WindowInteropHelper(this).Handle,20,ref value,4);
            value=2;DwmSetWindowAttribute(new WindowInteropHelper(this).Handle,33,ref value,4);
        }
        RenderDraft();
    }
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr hwnd,int attribute,ref int value,int size);
    public void ExportPreview(string path,double scale)
    {
        PanelRoot.UpdateLayout(); var bitmap=new RenderTargetBitmap((int)(368*scale),(int)(584*scale),96*scale,96*scale,PixelFormats.Pbgra32);
        bitmap.Render(PanelRoot);var png=new PngBitmapEncoder();png.Frames.Add(BitmapFrame.Create(bitmap));using var file=File.Create(path);png.Save(file);
    }
    public async Task UiCheck(string folder)
    {
        Directory.CreateDirectory(folder);var errors=new List<string>();var alignment=new List<object>();
        void CheckAlignment(params ComboBox[] controls)
        {
            foreach(var combo in controls.Where(c=>c.IsVisible))
            {
                var label=FindLabel(combo);if(label==null){errors.Add(combo.Name+": selected label missing");continue;}
                var center=label.TransformToAncestor(combo).Transform(new Point(0,label.ActualHeight/2)).Y;
                var delta=Math.Abs(center-combo.ActualHeight/2);
                alignment.Add(new{control=combo.Name,delta_dip=delta});if(delta>1)errors.Add(combo.Name+": text is not vertically centered");
            }
        }
        TextBlock? FindLabel(DependencyObject element)
        {
            for(var i=0;i<VisualTreeHelper.GetChildrenCount(element);i++)
            {var child=VisualTreeHelper.GetChild(element,i);if(child is TextBlock text&&text.Name=="SelectedLabel")return text;var found=FindLabel(child);if(found!=null)return found;}
            return null;
        }
        void CheckSettingsBounds()
        {
            var footer=VersionFooter.TransformToAncestor(PanelRoot).Transform(new Point()).Y;
            foreach(var element in new FrameworkElement[]{PollPicker,ThemePicker,SkillSummary,LoginStartup})
                if(element.TransformToAncestor(PanelRoot).Transform(new Point(0,element.ActualHeight)).Y>footer)errors.Add(element.Name+": overlaps footer");
        }
        foreach(var appearance in new[]{"light","dark"})
        {
            controller.SetTheme(appearance);
            foreach(var effect in LightingCommand.Effects)
            {
                mode=effect;periodMs=effect=="rainbow"?5000:effect=="solid"?0:1000;RenderDraft();
                await Dispatcher.Yield(DispatcherPriority.Render);
                foreach(var scale in new[]{1d,1.25,1.5,2})ExportPreview(Path.Combine(folder,$"{appearance}-{effect}-{scale:0.##}.png"),scale);
                CheckAlignment(DevicePicker,DurationPicker,PeriodPicker);
                if(PanelRoot.ActualWidth!=368||PanelRoot.ActualHeight!=584)errors.Add("Panel dimensions changed");
            }
            ShowSettings();await Dispatcher.Yield(DispatcherPriority.Render);CheckAlignment(PollPicker,ThemePicker);CheckSettingsBounds();ExportPreview(Path.Combine(folder,$"{appearance}-settings.png"),2);
            SkillManageClick(this,new());await InstallSkillAsync();await Dispatcher.Yield(DispatcherPriority.Render);ExportPreview(Path.Combine(folder,$"{appearance}-skill.png"),2);
            SkillBackClick(this,new());DoneClick(this,new());
        }
        var installed=skillInstaller.Inspect(skillRoot);if(!installed.Installed)errors.Add("Demo skill installation failed");
        TrayIconRenderer.ExportChecks(folder);
        File.WriteAllText(Path.Combine(folder,"layout.json"),System.Text.Json.JsonSerializer.Serialize(new{demo=true,logical_width=368,logical_height=584,render_scales=new[]{100,125,150,200},alignment,skill_installed=installed.Installed,skill_root=skillRoot,errors},PrivateStore.Json));
    }
}
