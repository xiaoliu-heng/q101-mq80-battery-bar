import AppKit
import SwiftUI

enum PanelPage { case lighting, settings }

@MainActor struct ControlPanel: View {
    @ObservedObject var app: AppModel
    @ObservedObject var keyboard: KeyboardController
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var page: PanelPage
    @State private var draft: LightingDraft
    @State private var previewStarted = Date()
    init(app: AppModel, keyboard: KeyboardController, initialPage: PanelPage = .lighting, initialDraft: LightingDraft = LightingDraft()) {
        self.app = app; self.keyboard = keyboard
        _page = State(initialValue:initialPage); _draft = State(initialValue:initialDraft)
    }
    var body: some View {
        GlassControlGroup {
            VStack(spacing:0) {
                header.frame(height:36)
                if page == .lighting { lighting } else { settings }
                statusLine.frame(height:34)
            }
        }
        .padding(.horizontal,22).padding(.top,18).padding(.bottom,14)
        .frame(width:PanelMetrics.width,height:PanelMetrics.height)
        .background(PanelBackdrop())
        .onAppear(perform:syncControls)
        .onChange(of:keyboard.command) { _ in syncControls() }
    }
    private var header: some View {
        HStack(spacing:9) {
            if page == .settings {
                Button { page = .lighting } label: { Image(systemName:"chevron.left").frame(width:32,height:32) }
                    .buttonStyle(GlassButtonStyle()).accessibilityLabel("返回灯光")
            } else {
                Image(systemName:"waveform.path").font(.system(size:18,weight:.medium)).foregroundStyle(.mint)
            }
            Text(page == .lighting ? "Kryra" : "设置").font(.system(size:18,weight:.semibold,design:.rounded))
            if app.demo { Text("演示").font(.system(size:10,weight:.medium)).foregroundStyle(.orange) }
            Spacer(minLength:0)
            if page == .lighting {
                Button { Task { _ = try? await keyboard.refresh() } } label: {
                    Image(systemName:keyboard.busy ? "ellipsis" : "arrow.clockwise").font(.system(size:12,weight:.medium)).frame(width:32,height:32)
                }.buttonStyle(GlassButtonStyle()).disabled(keyboard.busy).accessibilityLabel("刷新电量")
                Button { page = .settings } label: { Image(systemName:"slider.horizontal.3").font(.system(size:13)).frame(width:32,height:32) }
                    .buttonStyle(GlassButtonStyle()).accessibilityLabel("打开设置")
            } else {
                Menu {
                    Button("打开独立窗口") { app.showWindow?() }
                    Button("API 说明",action:app.openGuide)
                    Divider()
                    Button("退出 Kryra") { NSApp.terminate(nil) }
                } label: { Image(systemName:"ellipsis").frame(width:32,height:32) }
                .menuStyle(.borderlessButton).fixedSize().help("更多操作")
            }
        }
    }
    private var lighting: some View {
        VStack(spacing:0) {
            hero.frame(height:248)
            effects.frame(height:38)
            palette.padding(.top,15).frame(height:50)
            brightnessControl.frame(height:34)
            timing.frame(height:38).padding(.top,3)
            actions.frame(height:44).padding(.top,15)
            Spacer(minLength:8)
        }
        .frame(maxWidth:.infinity,maxHeight:.infinity)
    }
    private var hero: some View {
        HStack(spacing:21) {
            TimelineView(.animation(minimumInterval:0.05,paused:reduceMotion || draft.effect == .solid)) { context in
                let elapsed = reduceMotion ? 0 : context.date.timeIntervalSince(previewStarted)
                LightStripPreview(samples:draft.samples(elapsed:elapsed),intensity:draft.intensity(elapsed:elapsed,reduceMotion:reduceMotion))
            }
            VStack(alignment:.leading,spacing:6) {
                Text("键盘电量").font(.system(size:12)).foregroundStyle(.secondary)
                HStack(alignment:.firstTextBaseline,spacing:3) {
                    Text(keyboard.battery.map(String.init) ?? "—")
                        .font(.system(size:55,weight:.light,design:.rounded)).monospacedDigit()
                    Text("%").font(.system(size:20,weight:.light)).foregroundStyle(.secondary)
                }.frame(height:67,alignment:.leading)
                deviceMenu.font(.system(size:12)).frame(height:24,alignment:.leading)
                Text(keyboard.stale && keyboard.battery != nil ? "上次读数" : keyboard.connected ? "电量自动更新" : "连接后显示电量")
                    .font(.system(size:10)).foregroundStyle(.tertiary).frame(height:16)
                HStack(spacing:5) {
                    Circle().fill(keyboard.command == nil ? Color.secondary.opacity(0.35) : Color.mint).frame(width:5,height:5)
                    Text(keyboard.command == nil ? "灯带预览" : keyboard.modeName).font(.system(size:11))
                }.foregroundStyle(.secondary).frame(height:22).padding(.top,10)
            }
            .frame(maxWidth:.infinity,alignment:.leading)
        }
        .padding(.horizontal,13)
    }
    private var deviceMenu: some View {
        Menu {
            Button { Task { await keyboard.select("auto") } } label: {
                if keyboard.selection == "auto" { Label("自动选择 · USB 优先",systemImage:"checkmark") } else { Text("自动选择 · USB 优先") }
            }
            ForEach(keyboard.devices) { device in
                Button { Task { await keyboard.select(device.id) } } label: {
                    if keyboard.selection == device.id { Label(device.label,systemImage:"checkmark") } else { Text(device.label) }
                }
            }
        } label: {
            HStack(spacing:5) {
                Circle().fill(keyboard.connected ? Color.mint : .secondary.opacity(0.4)).frame(width:5,height:5)
                Text(keyboard.connected ? "\(keyboard.connectionName) 已连接" : "选择设备").lineLimit(1)
                Image(systemName:"chevron.down").font(.system(size:8,weight:.semibold))
            }
        }
        .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize(horizontal:false,vertical:true)
        .disabled(keyboard.busy).accessibilityLabel("选择连接设备")
    }
    private var effects: some View {
        HStack(spacing:3) {
            ForEach(LightingEffect.allCases) { effect in
                Button {
                    draft.select(effect); previewStarted = Date()
                } label: {
                    Text(effect.title).font(.system(size:12,weight:draft.effect == effect ? .semibold : .regular))
                        .frame(maxWidth:.infinity).frame(height:32)
                        .contentShape(Capsule())
                        .background {
                            if draft.effect == effect { Color.clear.modifier(CapsuleSurface(prominent:false)) }
                        }
                }
                .buttonStyle(.plain)
                .foregroundStyle(draft.effect == effect ? Color.primary : Color.secondary)
                .accessibilityLabel("\(effect.title)灯效").accessibilityAddTraits(draft.effect == effect ? .isSelected : [])
            }
        }
        .padding(3).background(Capsule().fill(.quaternary.opacity(0.5)))
    }
    private var palette: some View {
        HStack(spacing:12) {
            if draft.effect == .rainbow {
                Capsule().fill(LinearGradient(colors:[.green,.yellow,.orange,.pink,.purple,.blue],startPoint:.leading,endPoint:.trailing))
                    .frame(width:64,height:9)
                Text("整条渐变，自然流动").font(.system(size:11)).foregroundStyle(.secondary)
                Spacer(minLength:0)
            } else {
                ForEach(["FF5A70","FFAA33","FFE066","34D399","45B8FF","A78BFA"],id:\.self) { hex in
                    let rgb = try! RGB(hex:hex)
                    Button { draft.color = rgb } label: {
                        Circle().fill(color(rgb)).frame(width:23,height:23)
                            .overlay(Circle().strokeBorder(.white.opacity(0.65),lineWidth:1))
                            .padding(3).overlay(Circle().stroke(draft.color == rgb ? Color.primary.opacity(0.65) : .clear,lineWidth:1))
                    }.buttonStyle(.plain).accessibilityLabel("颜色 \(hex)")
                }
                NativeColorWell(color:colorBinding).frame(width:29,height:29).clipShape(Circle())
                    .overlay(Circle().strokeBorder(AngularGradient(colors:[.pink,.yellow,.green,.cyan,.blue,.purple,.pink],center:.center),lineWidth:2).allowsHitTesting(false))
                    .overlay(Image(systemName:"eyedropper").font(.system(size:11,weight:.medium)).foregroundStyle(.white).shadow(radius:1).allowsHitTesting(false))
            }
        }.frame(maxWidth:.infinity,alignment:.leading)
    }
    private var brightnessControl: some View {
        HStack(spacing:10) {
            Image(systemName:"sun.max").font(.system(size:12)).foregroundStyle(.secondary).frame(width:18)
            Slider(value:Binding(get:{Double(draft.brightness)},set:{draft.brightness = Int($0.rounded())}),in:0...100)
                .tint(.mint).accessibilityLabel("灯带亮度")
            Text("\(draft.brightness)%").font(.system(size:11).monospacedDigit()).foregroundStyle(.secondary).frame(width:34,alignment:.trailing)
        }
    }
    private var timing: some View {
        HStack(spacing:12) {
            Menu {
                ForEach([3.0,5,10,30,60],id:\.self) { value in Button("\(Int(value)) 秒") { draft.seconds = value } }
            } label: { Label("\(durationText)后恢复",systemImage:"timer").font(.system(size:11)) }
            .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("提醒时长")
            Spacer(minLength:0)
            if draft.effect != .solid {
                Menu {
                    ForEach([500,1000,2000,3000,5000,10000,20000],id:\.self) { value in
                        Button("周期 \(String(format:"%g",Double(value)/1000)) 秒") { draft.periodMS = value }
                    }
                } label: { Text("周期 \(String(format:"%g",Double(draft.periodMS)/1000))s").font(.system(size:11)) }
                .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("动画周期")
            }
        }.foregroundStyle(.secondary)
    }
    private var durationText: String { String(format:"%g 秒",draft.seconds) }
    private var actions: some View {
        HStack(spacing:10) {
            Button {
                Task { if let command = try? draft.command() { _ = try? await keyboard.setLighting(command) } }
            } label: {
                Label("发送提醒",systemImage:"sparkle").font(.system(size:13,weight:.medium)).frame(maxWidth:.infinity).frame(height:44)
            }.buttonStyle(GlassButtonStyle(prominent:true)).disabled(!keyboard.connected || keyboard.busy || keyboard.sleeping)
            Button { Task { _ = try? await keyboard.release() } } label: {
                Image(systemName:"arrow.uturn.backward").font(.system(size:13)).frame(width:44,height:44)
            }.buttonStyle(GlassButtonStyle()).disabled(!keyboard.connected || keyboard.busy)
                .help("恢复键盘原灯效").accessibilityLabel("恢复键盘原灯效")
        }
    }
    private var statusLine: some View {
        let message = page == .settings ? app.message ?? keyboard.issue : keyboard.issue
        return Text(message ?? (page == .settings ? "Kryra \(AppIdentity.version) · 固件 3.1.0" : keyboard.connected ? "调整预览后发送，届时自动恢复原灯效。" : "连接 USB 或 2.4G 后即可发送提醒。"))
            .font(.system(size:10)).foregroundStyle(message == nil ? Color.secondary : Color.orange)
            .lineLimit(2).truncationMode(.tail).frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.center)
            .help(message ?? "")
    }
    private var settings: some View {
        VStack(alignment:.leading,spacing:18) {
            settingsGroup("设备",symbol:"keyboard") {
                deviceMenu.frame(maxWidth:.infinity,alignment:.leading)
                Text(keyboard.firmware?.version.map { "固件 \($0)" } ?? "支持固件 3.1.0")
                    .font(.system(size:10)).foregroundStyle(.secondary).lineLimit(1)
                HStack {
                    Text("电量更新").foregroundStyle(.secondary)
                    Spacer()
                    Menu {
                        ForEach(BatteryPolling.presets,id:\.self) { seconds in
                            Button { try? keyboard.setPollingInterval(seconds) } label: {
                                if keyboard.pollIntervalSeconds == seconds { Label(BatteryPolling.title(seconds),systemImage:"checkmark") }
                                else { Text(BatteryPolling.title(seconds)) }
                            }
                        }
                    } label: { Text("每 \(BatteryPolling.title(keyboard.pollIntervalSeconds))") }
                    .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("电量更新间隔")
                }.font(.system(size:11)).frame(height:24)
            }
            settingsGroup("本机接口",symbol:"point.3.connected.trianglepath.dotted") {
                Text(app.apiPort.map { "127.0.0.1:\($0)" } ?? app.apiIssue ?? "打开应用后自动提供")
                    .font(.system(size:11,design:.monospaced)).foregroundStyle(.secondary).lineLimit(1)
                HStack(spacing:8) {
                    smallButton("复制命令",symbol:"doc.on.doc",action:app.copyCommand)
                    smallButton("API 说明",symbol:"arrow.up.right",action:app.openGuide)
                }
            }
            settingsGroup("Codex Skill",symbol:"sparkles") {
                Text(app.installedSkill?.path ?? app.skillRoot.appendingPathComponent("kryra").path)
                    .font(.system(size:10,design:.monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                HStack(spacing:8) {
                    smallButton(app.installedSkill == nil ? "安装 Skill" : "更新 Skill",symbol:"arrow.down",action:app.installSkill)
                    Button("更改位置…",action:app.chooseSkillRoot).buttonStyle(.plain).font(.system(size:11)).foregroundStyle(.secondary)
                }
            }
            Divider().opacity(0.5)
            Toggle("登录时启动",isOn:Binding(get:{app.loginEnabled},set:{app.setLogin($0)}))
                .font(.system(size:12)).toggleStyle(.switch).controlSize(.small)
            HStack {
                Button("打开独立窗口") { app.showWindow?() }.buttonStyle(.plain)
                Spacer()
                Button("退出 Kryra") { NSApp.terminate(nil) }.buttonStyle(.plain)
            }.font(.system(size:11)).foregroundStyle(.secondary)
            Spacer(minLength:0)
        }.padding(.top,24).frame(maxWidth:.infinity,maxHeight:.infinity)
    }
    private func settingsGroup<Content: View>(_ title: String, symbol: String,@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment:.leading,spacing:10) {
            Label(title,systemImage:symbol).font(.system(size:12,weight:.medium))
            content()
        }.frame(maxWidth:.infinity,alignment:.leading)
    }
    private func smallButton(_ title: String,symbol: String,action: @escaping () -> Void) -> some View {
        Button(action:action) { Label(title,systemImage:symbol).font(.system(size:11)).padding(.horizontal,13).frame(height:30) }
            .buttonStyle(GlassButtonStyle())
    }
    private var colorBinding: Binding<Color> {
        Binding(get:{color(draft.color)},set:{ value in
            guard let rgb = NSColor(value).usingColorSpace(.deviceRGB) else { return }
            func channel(_ value: CGFloat) -> UInt8 { value.isFinite ? UInt8((min(1,max(0,value))*255).rounded()) : 0 }
            draft.color = RGB(channel(rgb.redComponent),channel(rgb.greenComponent),channel(rgb.blueComponent))
        })
    }
    private func color(_ rgb: RGB) -> Color { Color(red:Double(rgb.r)/255,green:Double(rgb.g)/255,blue:Double(rgb.b)/255) }
    private func syncControls() {
        if let command = keyboard.command, command.nativeEffectID != nil { draft.adopt(command); previewStarted = Date().addingTimeInterval(-keyboard.effectElapsed) }
    }
}

/// A real AppKit color well keeps the whole round swatch clickable.
private struct NativeColorWell: NSViewRepresentable {
    @Binding var color: Color
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSColorWell {
        let well = NSColorWell(frame:.zero)
        well.colorWellStyle = .minimal; well.isBordered = false
        well.target = context.coordinator; well.action = #selector(Coordinator.changed(_:))
        well.setAccessibilityLabel("自定义灯带颜色")
        return well
    }
    func updateNSView(_ well: NSColorWell, context: Context) {
        context.coordinator.parent = self
        let value = NSColor(color)
        if well.color != value { well.color = value }
    }
    static func dismantleNSView(_ well: NSColorWell, coordinator: Coordinator) { well.deactivate() }
    @MainActor final class Coordinator: NSObject {
        var parent: NativeColorWell
        init(_ parent: NativeColorWell) { self.parent = parent }
        @objc func changed(_ well: NSColorWell) { parent.color = Color(nsColor:well.color) }
    }
}
