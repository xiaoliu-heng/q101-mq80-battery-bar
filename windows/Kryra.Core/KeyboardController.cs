using System.Diagnostics;
using System.Text.Json;

namespace Kryra.Core;

public sealed record AppSnapshot(bool Connected,int? BatteryPercent,DateTimeOffset? Updated,string Mode,string DeviceName,
    string? Version,int? Build,bool Stale,bool Suspended,bool OwnActive,bool ExternalActive,int RemainingMs,
    LightingCommand? Lighting,double ElapsedSeconds,int CommandsSent,Preferences Settings,IReadOnlyList<DeviceInfo> Devices,string Message,bool Demo);

public sealed class KeyboardController
{
    private readonly IHidBackend backend;
    private readonly PrivateStore store;
    private readonly object sync=new();
    private readonly SemaphoreSlim work=new(1,1);
    private CancellationTokenSource? polling;
    private Preferences preferences;
    private IReadOnlyList<DeviceInfo> devices=[];
    private FirmwareState? firmware;
    private DeviceInfo? current;
    private int? battery;
    private DateTimeOffset? updated;
    private bool connected,suspended,stopping;
    private int failures,commandsSent,wireRequests;
    private string message="正在查找键盘…";
    private long ownDeadline,ownStarted;
    private byte ownSequence,sequence;
    private string? ownDevice;
    private LightingCommand? lighting;
    public event Action? Changed;
    public KeyboardController(IHidBackend backend,PrivateStore store) { this.backend=backend; this.store=store; preferences=store.Load(); }
    private bool OwnActive=>ownDevice!=null && Stopwatch.GetTimestamp()<ownDeadline;
    public AppSnapshot Snapshot()
    {
        lock(sync)
        {
            var active=OwnActive;
            return new(connected,battery,updated,current?.Transport??"",current?.Name??"未连接",firmware?.Version,firmware?.Build,
                !connected||updated==null||(DateTimeOffset.UtcNow-updated.Value).TotalSeconds>preferences.BatteryPollIntervalSeconds*2+5,
                suspended,active,firmware?.Active==true&&!active&&updated!=null&&(DateTimeOffset.UtcNow-updated.Value).TotalMilliseconds<firmware.RemainingMs,
                active?(int)Math.Max(0,(ownDeadline-Stopwatch.GetTimestamp())*1000/Stopwatch.Frequency):0,
                lighting,active?(double)(Stopwatch.GetTimestamp()-ownStarted)/Stopwatch.Frequency:0,commandsSent,preferences,devices,message,backend.Demo);
        }
    }
    public object StatusJson()
    {
        var s=Snapshot();
        return new { app="Kryra Windows",version=AppIdentity.Version,demo=s.Demo,connected=s.Connected,
            device=new {id=current?.Id,name=s.DeviceName,transport=s.Mode,firmware_version=s.Version,firmware_build=s.Build},
            battery=new {percent=s.BatteryPercent,stale=s.Stale,updated_at=s.Updated,poll_interval_seconds=s.Settings.BatteryPollIntervalSeconds},
            lighting=new {active=s.OwnActive||s.ExternalActive,owner=s.OwnActive?"self":s.ExternalActive?"external":"none",mode=s.Lighting?.Mode,
                execution=s.Lighting?.Mode=="frame"?"frame_lease":"firmware_local",remaining_ms=s.RemainingMs,
                commands_sent=s.CommandsSent,frames_sent=s.CommandsSent,request_id=s.Lighting?.RequestId,
                prevents_idle_sleep=s.OwnActive&&s.Mode=="rf"&&s.Lighting?.Mode=="frame"},
            settings=new {battery_poll_interval_seconds=s.Settings.BatteryPollIntervalSeconds,device_selection=s.Settings.Selection,theme=s.Settings.Theme},
            suspended=s.Suspended,message=s.Message,diagnostics=new{hid_requests_sent=wireRequests},
            devices=s.Devices.Select(d=>new{id=d.Id,name=d.Name,transport=d.Transport}) };
    }
    private async Task<T> Run<T>(Func<CancellationToken,T> action,CancellationToken ct)
    {
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(ct); deadline.CancelAfter(TimeSpan.FromSeconds(7));
        await work.WaitAsync(deadline.Token).ConfigureAwait(false);
        try { return await Task.Run(()=>{deadline.Token.ThrowIfCancellationRequested(); return action(deadline.Token);},deadline.Token).ConfigureAwait(false); }
        finally { work.Release(); }
    }
    private FirmwareState Request(IHidConnection hid,byte command,CancellationToken ct,byte[]? payload=null)
    {
        var request=Wire.Packet(command,unchecked(++sequence),payload??[]);
        Interlocked.Increment(ref wireRequests);
        return Wire.Decode(request,hid.Request(request,ct));
    }
    private (IHidConnection Hid,DeviceInfo Device,FirmwareState State) Open(CancellationToken ct)
    {
        var found=backend.Enumerate(); string selection;
        lock(sync) { devices=found; selection=preferences.Selection; }
        var candidates=found.Where(d=>selection=="auto"||selection==d.Transport||selection==d.Id)
            .OrderBy(d=>d.Transport=="usb"?0:1).ToArray();
        if(candidates.Length==0) throw new ControlException("未找到键盘；请连接 USB，或插入 2.4G 接收器并唤醒键盘。");
        string? error=null;
        foreach(var group in candidates.GroupBy(d=>d.Transport))
        {
            if(group.Count()>1) throw new ControlException("检测到多个同类设备，请在设置中选择具体设备。");
            var d=group.Single(); IHidConnection? hid=null;
            try
            {
                hid=backend.Open(d); var state=Request(hid,Wire.Info,ct);
                if(!state.Permits(d.Transport)) throw new ControlException("键盘连接模式与所选接口不同，请切换 USB / 2.4G 模式。");
                return(hid,d,state);
            }
            catch(OperationCanceledException) { hid?.Dispose(); throw; }
            catch(Exception e) { hid?.Dispose(); error=e.Message; }
        }
        throw new ControlException(error??"键盘暂未响应。");
    }
    private void Adopt(DeviceInfo device,FirmwareState state)
    {
        lock(sync)
        {
            connected=true; current=device; firmware=state; battery=state.Percent; updated=DateTimeOffset.UtcNow; failures=0;
            if(ownDevice!=null && (ownDevice!=device.Id || !state.Active || ownSequence!=state.LastSequence)) ownDevice=null;
        }
    }
    private void Error(Exception error)
    {
        lock(sync) { connected=false; failures++; message=error is OperationCanceledException?"操作已取消或超时，请重试。":error.Message; }
        Changed?.Invoke();
    }
    public async Task RefreshAsync(CancellationToken ct=default)
    {
        try
        {
            await Run(token=>
            {
                lock(sync) { if(suspended) throw new ControlException("电脑正在休眠。"); }
                var session=Open(token); using(session.Hid) { Adopt(session.Device,session.State); }
                lock(sync) message="电量已更新";
                return true;
            },ct).ConfigureAwait(false);
            Changed?.Invoke();
        }
        catch(Exception error) { Error(error); throw; }
    }
    public async Task PlayAsync(LightingCommand command,CancellationToken ct=default)
    {
        if(command.Mode=="release") { await ReleaseAsync(ct); return; }
        try
        {
            await Run(token=>
            {
                lock(sync) if(suspended||stopping) throw new ControlException("应用当前不能发送提醒。");
                var session=Open(token); using(session.Hid)
                {
                    Adopt(session.Device,session.State); FirmwareState state; var count=1;
                    if(command.Mode=="frame")
                    {
                        var timeout=new byte[]{(byte)command.DurationMs,(byte)(command.DurationMs>>8)};
                        Request(session.Hid,Wire.Timeout,token,timeout);
                        var rgb=command.Colors!.SelectMany(c=>new[]{(byte)(c.R*command.Brightness/100),(byte)(c.G*command.Brightness/100),(byte)(c.B*command.Brightness/100)}).ToArray();
                        state=Request(session.Hid,Wire.Frame,token,rgb); count=2;
                    }
                    else state=Request(session.Hid,Wire.Play,token,command.Payload());
                    Adopt(session.Device,state);
                    lock(sync)
                    {
                        lighting=command; commandsSent=count; ownDevice=state.Active?session.Device.Id:null; ownSequence=state.LastSequence;
                        ownDeadline=Stopwatch.GetTimestamp()+(long)(state.RemainingMs/1000d*Stopwatch.Frequency);
                        ownStarted=ownDeadline-(long)(command.DurationMs/1000d*Stopwatch.Frequency);
                        message="提醒已发送，键盘将自行播放并恢复原灯效。";
                    }
                }
                return true;
            },ct).ConfigureAwait(false);
            Changed?.Invoke();
        }
        catch(Exception error) { Error(error); throw; }
    }
    public async Task ReleaseAsync(CancellationToken ct=default,bool onlyOwned=false)
    {
        lock(sync) if(onlyOwned&&!OwnActive) return;
        try
        {
            await Run(token=>
            {
                var session=Open(token); using(session.Hid)
                {
                    bool release; lock(sync) release=!onlyOwned||(OwnActive&&ownDevice==session.Device.Id&&ownSequence==session.State.LastSequence);
                    var state=release?Request(session.Hid,Wire.Release,token):session.State;
                    Adopt(session.Device,state); lock(sync) { ownDevice=null; message=release?"已恢复键盘原灯效。":"键盘当前由其他来源控制。"; }
                }
                return true;
            },ct).ConfigureAwait(false); Changed?.Invoke();
        }
        catch(Exception error) { Error(error); if(!onlyOwned) throw; }
    }
    public void Start()=>ResetPolling(true);
    private void ResetPolling(bool immediate)
    {
        polling?.Cancel(); polling=new(); var cancellation=polling.Token;
        _=Task.Run(async()=>
        {
            try
            {
                if(immediate) try { await RefreshAsync(cancellation); } catch(Exception) when(!cancellation.IsCancellationRequested) { }
                while(!cancellation.IsCancellationRequested)
                {
                    int seconds; lock(sync) seconds=devices.Count==0?5:Math.Min(Math.Max(300,preferences.BatteryPollIntervalSeconds),
                        preferences.BatteryPollIntervalSeconds*(1<<Math.Min(8,Math.Max(0,failures-1))));
                    await Task.Delay(TimeSpan.FromSeconds(seconds),cancellation);
                    lock(sync) if(suspended||OwnActive) continue;
                    try { await RefreshAsync(cancellation); } catch(Exception) when(!cancellation.IsCancellationRequested) { }
                }
            }
            catch(OperationCanceledException) { }
        },cancellation);
    }
    public void SetPollInterval(int seconds)
    {
        if(seconds is <30 or >3600) throw new ControlException("电量更新间隔需要为 30～3600 秒。",400);
        lock(sync) { preferences=preferences with{BatteryPollIntervalSeconds=seconds}; store.Save(preferences); }
        ResetPolling(false); Changed?.Invoke();
    }
    public void SetTheme(string theme)
    {
        if(theme is not ("light" or "dark" or "system")) throw new ControlException("外观选项无效。",400);
        lock(sync) { preferences=preferences with{Theme=theme}; store.Save(preferences); } Changed?.Invoke();
    }
    public async Task SelectAsync(string selection)
    {
        if(!PrivateStore.ValidSelection(selection)) throw new ControlException("设备选择无效。",400);
        await ReleaseAsync(onlyOwned:true);
        lock(sync) { preferences=preferences with{Selection=selection}; store.Save(preferences); connected=false; }
        ResetPolling(false); await RefreshAsync();
    }
    public async Task SuspendAsync()
    {
        lock(sync) { suspended=true; connected=false; }
        polling?.Cancel(); await ReleaseAsync(onlyOwned:true);
        lock(sync) { connected=false; message="电脑休眠，查询已暂停。"; } Changed?.Invoke();
    }
    public void Resume() { lock(sync) { suspended=false; ownDevice=null; } ResetPolling(true); Changed?.Invoke(); }
    public async Task StopAsync() { lock(sync) stopping=true; polling?.Cancel(); await ReleaseAsync(onlyOwned:true); }
}
