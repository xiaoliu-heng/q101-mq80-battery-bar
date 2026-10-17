import AppKit
import SwiftUI

@MainActor struct ControlPanel: View {
    @ObservedObject var app: AppModel
    @ObservedObject var keyboard: KeyboardController
    @State private var tab = "lighting"
    @State private var colors = Array(repeating:RGB(0,200,160),count:9)
    @State private var brightness = Double(LightingCommand.defaultBrightness)
    @State private var period = 1.0
    @State private var selectedSegment: Int?
    @State private var effect = "solid"
    @State private var seconds = LightingCommand.defaultDuration
    @State private var pendingEdit: Task<Void,Never>?
    var body: some View {
        VStack(spacing:0) {
            HStack(spacing:10) {
                Image(systemName:"keyboard.fill").font(.title2).foregroundStyle(.mint)
                    .frame(width:40,height:40).background(.mint.opacity(0.12),in:RoundedRectangle(cornerRadius:12))
                VStack(alignment:.leading,spacing:2) {
                    Text("Kryra").font(.system(size:17,weight:.semibold))
                    Text(keyboard.connected ? "\(keyboard.connectionName) 已连接" : "等待键盘连接").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if app.demo { Text("演示").font(.caption.bold()).padding(6).background(.orange.opacity(0.15),in:Capsule()) }
                Circle().fill(keyboard.connected ? Color.mint : .secondary.opacity(0.4)).frame(width:8,height:8)
            }.padding(18)
            ScrollView {
                VStack(spacing:16) {
                    batteryCard
                    devicePicker
                    if let issue = keyboard.issue {
                        Label(issue,systemImage:"info.circle").font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth:.infinity,alignment:.leading).fixedSize(horizontal:false,vertical:true)
                    }
                    Picker("工作区",selection:$tab) { Text("灯光").tag("lighting"); Text("集成与设置").tag("integration") }.pickerStyle(.segmented)
                    if tab == "lighting" { lighting } else { integration }
                }.padding(.horizontal,18).padding(.bottom,18)
            }
            Divider()
            HStack {
                Text("v\(AppIdentity.version)").font(.caption2).foregroundStyle(.tertiary)
                Spacer()
                Button("打开面板") { app.showWindow?() }.buttonStyle(.plain).font(.caption)
                Button("退出") { NSApp.terminate(nil) }.buttonStyle(.plain).font(.caption).padding(.leading,10)
            }.padding(.horizontal,18).padding(.vertical,11)
        }
        .frame(width:410,height:690).background(Color(nsColor:.windowBackgroundColor))
        .onAppear(perform:syncControls)
        .onChange(of:keyboard.command) { _ in syncControls() }
    }
    private var batteryCard: some View {
        VStack(alignment:.leading,spacing:10) {
            HStack(alignment:.center) {
                VStack(alignment:.leading,spacing:1) {
                    Text("键盘电量").font(.caption).foregroundStyle(.secondary)
                    HStack(alignment:.firstTextBaseline,spacing:3) {
                        Text(keyboard.battery.map(String.init) ?? "—").font(.system(size:44,weight:.semibold,design:.rounded)).monospacedDigit()
                        Text("%").font(.title3).foregroundStyle(.secondary)
                        if keyboard.stale && keyboard.battery != nil { Text("上次读数").font(.caption).foregroundStyle(.secondary).padding(.leading,5) }
                    }
                }
                Spacer()
                Button { Task { _ = try? await keyboard.refresh() } } label: {
                    Image(systemName:keyboard.busy ? "ellipsis" : "arrow.clockwise").frame(width:26,height:26)
                }.buttonStyle(.borderless).disabled(keyboard.busy).help("刷新键盘电量").accessibilityLabel("刷新电量")
            }
            GeometryReader { proxy in
                ZStack(alignment:.leading) {
                    Capsule().fill(.primary.opacity(0.07))
                    Capsule().fill((keyboard.battery ?? 100) <= 20 ? Color.orange : .mint)
                        .frame(width:proxy.size.width*Double(keyboard.battery ?? 0)/100)
                }
            }.frame(height:5)
            HStack {
                if let version = keyboard.firmware?.version { Text("固件 v\(version)") }
                Spacer()
                TimelineView(.periodic(from:.now,by:15)) { context in
                    Text(keyboard.updatedAt.map { "更新于 \($0.formatted(date:.omitted,time:.shortened))" } ?? "连接后自动读取")
                }
            }.font(.caption2).foregroundStyle(.secondary)
        }.padding(16).background(Color(nsColor:.controlBackgroundColor),in:RoundedRectangle(cornerRadius:16))
    }
    private var devicePicker: some View {
        HStack {
            Image(systemName:keyboard.device?.transport == "rf" ? "antenna.radiowaves.left.and.right" : "cable.connector").foregroundStyle(.secondary)
            Picker("设备",selection:Binding(get:{keyboard.selection},set:{ value in Task { await keyboard.select(value) } })) {
                Text("自动选择 · USB 优先").tag("auto")
                ForEach(keyboard.devices) { device in Text(device.label).tag(device.id) }
                if keyboard.selection != "auto" && !keyboard.devices.contains(where:{$0.id == keyboard.selection}) {
                    Text("上次选择的设备").tag(keyboard.selection)
                }
            }.labelsHidden().pickerStyle(.menu).disabled(keyboard.busy)
            Spacer(minLength:0)
        }.font(.caption)
    }
    private var lighting: some View {
        VStack(alignment:.leading,spacing:14) {
            HStack {
                Text("侧边灯条").font(.headline)
                Spacer(); Text(keyboard.modeName).font(.caption).foregroundStyle(.secondary)
            }
            TimelineView(.animation(minimumInterval:0.05,paused:!["rainbow","blink","breathe"].contains(keyboard.mode))) { context in
                HStack(spacing:5) {
                    ForEach(0..<9,id:\.self) { index in
                        let pixel = preview(index,at:context.date)
                        Button { selectedSegment = selectedSegment == index ? nil : index } label: {
                            RoundedRectangle(cornerRadius:6).fill(swiftColor(pixel))
                                .overlay(RoundedRectangle(cornerRadius:6).stroke(selectedSegment == index ? Color.primary : .clear,lineWidth:2))
                                .frame(height:32)
                        }.buttonStyle(.plain).accessibilityLabel("选择第 \(index+1) 段侧灯")
                    }
                }
            }
            HStack {
                ColorPicker(selectedSegment.map { "第 \($0+1) 段" } ?? "整条颜色",selection:colorBinding,supportsOpacity:false)
                if selectedSegment != nil { Button("整条调色") { selectedSegment = nil }.font(.caption) }
            }
            HStack(spacing:10) {
                ForEach(["FF5A5F","FFAA33","FFE066","34D399","00C8A0","45B8FF","747BFF","E879F9"],id:\.self) { hex in
                    Button { if let pixel = try? RGB(hex:hex) { setColor(pixel) } } label: {
                        Circle().fill(swiftColor(try! RGB(hex:hex))).frame(width:24,height:24)
                    }.buttonStyle(.plain).accessibilityLabel("侧灯颜色 \(hex)")
                }
            }
            HStack {
                Text("亮度").font(.caption)
                Slider(value:Binding(get:{brightness},set:{ brightness = $0.rounded(); if keyboard.command != nil { queueLighting(keyboard.command?.mode ?? "solid") } }),in:0...100,step:1)
                    .accessibilityLabel("侧灯亮度")
                Text("\(Int(brightness))%").font(.caption.monospacedDigit()).frame(width:36,alignment:.trailing)
            }
            HStack {
                Text("动画周期").font(.caption)
                Slider(value:Binding(get:{period},set:{ period = $0; if ["rainbow","blink","breathe"].contains(keyboard.mode) { queueLighting(keyboard.mode) } }),in:0.5...20,step:0.5)
                    .accessibilityLabel("动画周期")
                Text(String(format:"%.1fs",period)).font(.caption.monospacedDigit()).frame(width:36,alignment:.trailing)
            }
            HStack(spacing:8) {
                Picker("灯效",selection:Binding(get:{effect},set:{ value in
                    effect = value; period = value == "rainbow" ? 5 : 1
                })) {
                    Text("纯色").tag("solid"); Text("闪烁").tag("blink"); Text("呼吸").tag("breathe"); Text("彩虹").tag("rainbow")
                }.labelsHidden().frame(width:105)
                Button("发送提醒") { queueLighting(effect == "solid" && !colors.allSatisfy({$0 == colors[0]}) ? "frame" : effect,immediate:true) }
                    .buttonStyle(.borderedProminent).tint(.mint)
                Spacer(minLength:0)
            }
            Button("恢复键盘原灯效",action:restore).buttonStyle(.link)
            HStack {
                Text("提醒时长").font(.caption)
                Picker("时长",selection:Binding(get:{seconds},set:{ value in
                    seconds = value
                    if let command = keyboard.command { queueLighting(command.mode) }
                })) {
                    Text("3 秒").tag(3.0);Text("5 秒").tag(5.0);Text("10 秒").tag(10.0);Text("30 秒").tag(30.0);Text("1 分钟").tag(60.0)
                    if ![3.0,5.0,10.0,30.0,60.0].contains(seconds) { Text(String(format:"%g 秒",seconds)).tag(seconds) }
                }.labelsHidden().frame(width:95)
                Spacer()
            }
            if keyboard.device?.transport == "rf" {
                Text("内置灯效由键盘播放，原有休眠可提前结束提醒。九段颜色最长 10 秒，期间保持接收。")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            if keyboard.firmware?.nativeEffects == false { Text("四种内置灯效需要固件 v3.1.0；可先使用九段静态颜色。")
                .font(.caption2).foregroundStyle(.orange) }
        }.disabled(!keyboard.connected || keyboard.sleeping)
    }
    private var integration: some View {
        VStack(alignment:.leading,spacing:16) {
            VStack(alignment:.leading,spacing:8) {
                Label("本机接口",systemImage:"point.3.connected.trianglepath.dotted").font(.headline)
                Text(app.apiPort.map { "127.0.0.1:\($0) · 仅此 Mac" } ?? app.apiIssue ?? "正在启动…")
                    .font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                HStack { Button("复制查询命令",action:app.copyCommand); Button("API 说明",action:app.openGuide) }.buttonStyle(.bordered)
            }
            Divider()
            VStack(alignment:.leading,spacing:8) {
                Label("Codex Skill",systemImage:"sparkles").font(.headline)
                Text("安装后可直接说：查键盘电量、侧灯变蓝 5 秒、短时彩虹提醒。").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button(app.installedSkill == nil ? "一键安装 Skill" : "更新 Skill",action:app.installSkill).buttonStyle(.borderedProminent)
                    Button("选择目录…",action:app.chooseSkillRoot).buttonStyle(.borderless)
                }
                Text(app.installedSkill?.path ?? app.skillRoot.appendingPathComponent("kryra").path)
                    .font(.system(size:10,design:.monospaced)).foregroundStyle(.tertiary).lineLimit(2).textSelection(.enabled)
            }
            Divider()
            Toggle("登录时启动",isOn:Binding(get:{app.loginEnabled},set:{ app.setLogin($0) })).toggleStyle(.switch)
            Text("电量约每 30 秒刷新。键盘休眠时保留上次读数，唤醒后可手动刷新。").font(.caption).foregroundStyle(.secondary)
            if let message = app.message { Text(message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
        }
    }
    private var colorBinding: Binding<Color> {
        Binding(get:{swiftColor(colors[selectedSegment ?? 0])},set:{ value in
            guard let color = NSColor(value).usingColorSpace(.deviceRGB) else { return }
            func channel(_ value: CGFloat) -> UInt8 { value.isFinite ? UInt8((min(1,max(0,value))*255).rounded()) : 0 }
            setColor(RGB(channel(color.redComponent),channel(color.greenComponent),channel(color.blueComponent)))
        })
    }
    private func swiftColor(_ rgb: RGB) -> Color { Color(red:Double(rgb.r)/255,green:Double(rgb.g)/255,blue:Double(rgb.b)/255) }
    private func syncControls() {
        if let command = keyboard.command {
            if command.mode != "rainbow" { colors = command.colors }
            brightness = Double(command.brightness); period = Double(command.periodMS)/1000
            if command.nativeEffectID != nil { effect = command.mode }
            if let duration = command.duration { seconds = duration }
        }
    }
    private func setColor(_ pixel: RGB) {
        if let selectedSegment { colors[selectedSegment] = pixel } else { colors = Array(repeating:pixel,count:9) }
        queueLighting(selectedSegment == nil ? effect : "frame")
    }
    private func preview(_ index: Int, at date: Date) -> RGB {
        if let command = keyboard.command {
            let data = command.payload(elapsed:keyboard.effectElapsed)
            return RGB(data[index*3],data[index*3+1],data[index*3+2])
        }
        return colors[index]
    }
    private func queueLighting(_ mode: String, immediate: Bool = false) {
        pendingEdit?.cancel()
        var value: [String:Any] = ["mode":mode,"brightness":Int(brightness),"period_ms":Int(period*1000)]
        if ["solid","blink","breathe"].contains(mode) { value["color"] = colors[0].hex }
        if mode == "frame" { value["colors"] = colors.map(\.hex) }
        value["duration_seconds"] = mode == "frame" ? min(10,seconds) : seconds
        pendingEdit = Task {
            do {
                if !immediate { try await Task.sleep(nanoseconds:100_000_000) }
                try Task.checkCancellation()
                _ = try await keyboard.setLighting(LightingCommand.parse(value))
            } catch is CancellationError {} catch {}
        }
    }
    private func restore() { pendingEdit?.cancel(); Task { _ = try? await keyboard.release() } }
}
