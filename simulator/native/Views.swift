import AppKit
import SwiftUI

struct RootView: View {
    @ObservedObject var lab: LabModel
    var body: some View {
        HStack(spacing:0) {
            VStack(alignment:.leading,spacing:5) {
                Text("固件实验室").font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.horizontal,10).padding(.vertical,8)
                ForEach(LabPage.allCases) { entry in
                    Button { lab.page = entry } label: {
                        Label(entry.title,systemImage:entry.icon).frame(maxWidth:.infinity,alignment:.leading).padding(.horizontal,10).padding(.vertical,9)
                            .background(lab.page == entry ? Color.accentColor.opacity(0.14) : .clear,in:RoundedRectangle(cornerRadius:6))
                    }.buttonStyle(.plain).foregroundStyle(lab.page == entry ? Color.accentColor : .primary)
                }
                Spacer()
                VStack(alignment: .leading, spacing: 5) {
                    Label(lab.ready ? "引擎已连接" : "引擎连接中", systemImage: lab.ready ? "circle.fill" : "circle.dotted")
                        .foregroundStyle(lab.ready ? Color.green : Color.secondary)
                    Text("2.1 · SwiftUI / AppKit").foregroundStyle(.secondary)
                }.font(.caption).frame(maxWidth: .infinity, alignment: .leading).padding(15)
            }.padding(10).frame(width:185).background(Color(nsColor:.windowBackgroundColor))
            Divider()
            VStack(spacing: 0) {
                if lab.ready {
                    HStack(spacing:0) {
                        page.frame(maxWidth: .infinity, maxHeight: .infinity)
                        if lab.showInspector && (lab.page == .workbench || lab.page == .lighting) {
                            Divider()
                            EnvironmentView(lab: lab).frame(width:255)
                        }
                    }
                } else {
                    VStack(spacing: 12) { ProgressView(); Text("正在加载离线固件引擎…").foregroundStyle(.secondary) }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                Divider()
                HStack(spacing: 10) {
                    if let issue = lab.issue {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(issue).lineLimit(2).textSelection(.enabled)
                        Button { lab.issue = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("关闭提示")
                    } else { Text(lab.status).foregroundStyle(.secondary) }
                    Spacer(minLength: 10)
                    if lab.pendingCount > 0 { ProgressView().controlSize(.mini); Text("处理操作…").foregroundStyle(.secondary) }
                    Text("离线 · 不连接键盘").foregroundStyle(.secondary)
                }.font(.caption).padding(.horizontal, 14).padding(.vertical, 9)
            }
        }
        .tint(.blue)
    }
    @ViewBuilder private var page: some View {
        switch lab.page {
        case .workbench: WorkbenchView(lab: lab)
        case .lighting: LightingView(lab: lab)
        case .scenarios: ScenariosView(lab: lab)
        case .inspect: InspectView(lab: lab)
        case .coverage: CoverageView(lab: lab)
        }
    }
}

struct WindowControls:View {
    @ObservedObject var lab:LabModel
    var body:some View {
        HStack(spacing:10) {
            Picker("固件",selection:Binding(get:{lab.selectedFirmware},set:lab.selectFirmware)) {
                ForEach(lab.images,id:\.self) { image in Text(image["name"].text).tag(image["id"].text) }
            }.labelsHidden().frame(width:220).disabled(!lab.ready)
            Button(action:lab.importFirmware) { Image(systemName:"square.and.arrow.down") }.help("导入固件… ⌘O").disabled(!lab.ready)
            Menu {
                Button("保存快照…") { lab.export("snapshot",name:"MQ80-snapshot.json") }
                Button("载入快照…") { lab.importJSON(snapshot:true) }
                Divider()
                Button("导出操作录制…") { lab.export("recording",name:"MQ80-recording.json") }
                Button("导出完整追踪…") { lab.export("report",name:"MQ80-trace.ndjson") }
            } label: { Image(systemName:"clock.arrow.circlepath") }.help("快照与录制").disabled(!lab.ready)
            Button { lab.export("report",name:"MQ80-runtime-report.json") } label: { Image(systemName:"square.and.arrow.up") }.help("导出运行报告… ⌘S").disabled(!lab.ready)
            Button { lab.showInspector.toggle() } label: { Image(systemName:"sidebar.right") }.help("显示或隐藏环境设置")
        }.controlSize(.small)
    }
}

struct ClockBar: View {
    @ObservedObject var lab: LabModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Label(lab.runtimeLabel, systemImage: lab.runtime["status"].text == "sleeping" ? "moon.zzz" : "cpu")
                Text(String(format: "%.3f ms", lab.runtime["time_ms"].double)).monospacedDigit().foregroundStyle(.secondary).frame(minWidth: 112, alignment: .leading)
                Spacer()
                Picker("时钟", selection: Binding(get: { lab.clockMode }, set: lab.setClock)) {
                    Text("实时预览").tag("preview"); Text("严格仿真").tag("strict")
                }.pickerStyle(.segmented).frame(width: 190)
                Button(action: lab.togglePlayback) { Label(lab.playing ? "暂停" : "继续", systemImage: lab.playing ? "pause.fill" : "play.fill").frame(width: 55) }
                    .keyboardShortcut("r").help("继续 / 暂停 ⌘R")
                Menu {
                    Button("步进 1 ms") { lab.command(["action":"advance", "ms":1], pauseFirst:true) }
                    Button("步进 100 ms") { lab.command(["action":"advance", "ms":100], pauseFirst:true) }
                    Divider()
                    Button("全关灯 · 休眠检查点") { lab.command(["action":"sleep", "gate":"fast"], pauseFirst:true) }
                    Button("普通空闲 · 休眠检查点") { lab.command(["action":"sleep", "gate":"normal"], pauseFirst:true) }
                    Divider()
                    Button("重置运行环境") { lab.reset() }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).fixedSize().help("步进、休眠与重置")
            }
            Text(lab.clockMode == "preview" ? "按墙钟预览灯效；有输入或待处理报告时执行离散调度。" : String(format: "逐次执行 1 ms IRQ 与任务调度 · 当前约 %.2f× 实时速度", lab.speed))
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct WorkbenchView: View {
    @ObservedObject var lab: LabModel
    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                ClockBar(lab: lab)
                GroupBox {
                    VStack(spacing: 10) {
                        HStack {
                            Text("Q101 / MQ80").font(.headline)
                            Text("84 键 · 9 颗侧灯").foregroundStyle(.secondary)
                            Spacer()
                            Text(lab.queryCaption).font(.caption).monospacedDigit().foregroundStyle(.secondary).accessibilityIdentifier("batteryQueryStatus")
                            Button("Fn＋B 查询电量", action: lab.queryBattery).help("自动推进三秒显示时间 ⇧⌘B")
                        }
                        KeyboardSurface(keys:lab.keys, main:lab.mainPixels, side:lab.sidePixels, selected:lab.highlightedKeys, lighting:lab.showKeyLighting, coordinates:lab.showCoordinates, onKey:lab.tap)
                            .aspectRatio(17.1/6.35, contentMode:.fit).frame(maxHeight: 345)
                        HStack {
                            Toggle("组合键", isOn:$lab.combo).toggleStyle(.checkbox)
                            Button("发送组合", action:lab.sendCombo).disabled(lab.selectedKeys.isEmpty)
                            Button("松开所有键", action:lab.releaseKeys)
                            if lab.combo { Text("已选 \(lab.selectedKeys.count) 键").foregroundStyle(.secondary) }
                            Spacer()
                            Toggle("矩阵坐标", isOn:$lab.showCoordinates).toggleStyle(.checkbox)
                        }.controlSize(.small)
                        HStack(spacing:12) {
                            Button { lab.setKeyboardCapture(!lab.keyboardCaptureActive) } label: {
                                Label(lab.keyboardCaptureActive ? "停止键盘输入" : "使用实体键盘",systemImage:lab.keyboardCaptureActive ? "stop.circle.fill" : "keyboard")
                            }.help("仅接收本窗口的按下与松开；⌘. 停止输入。⇧⌘K 开始。")
                            Toggle("右 ⌘ → Fn",isOn:$lab.rightCommandIsFn).toggleStyle(.checkbox)
                                .help("用右 Command 测试虚拟 Fn 组合；实体键盘自己的 Fn 通常不会发送给 macOS。")
                            Toggle("按住 Fn",isOn:Binding(get:{lab.virtualFnHeld},set:lab.setVirtualFn)).toggleStyle(.checkbox)
                                .help("没有右 Command 时，勾选后直接按 B 等实体键；松开所有键会同时释放 Fn。")
                            Text(lab.keyboardCaptureActive ? "输入中 · 按住组合即可测试 · ⌘. 退出" : "开启后，按键按下与松开会同步到虚拟键盘")
                                .font(.caption).foregroundStyle(lab.keyboardCaptureActive ? Color.accentColor : .secondary)
                            Spacer(minLength:0)
                        }.controlSize(.small)
                        .background(PhysicalInputFocus(lab:lab).frame(width:1,height:1))
                        Divider()
                        HStack { Text("主灯原始帧").font(.caption).foregroundStyle(.secondary); LEDStrip(pixels:lab.mainPixels) }
                    }.padding(8)
                }
                HStack(alignment:.top, spacing:14) {
                    GroupBox("虚拟主机") {
                        VStack(alignment:.leading, spacing:8) {
                            Text(lab.runtime["host_text"].text.isEmpty ? "等待输入…" : lab.runtime["host_text"].text)
                                .font(.system(size:22,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,minHeight:32,alignment:.leading)
                            Text("HID  " + spacedHex(lab.runtime["hid"].text)).font(.system(size:11,design:.monospaced)).foregroundStyle(.secondary).textSelection(.enabled)
                            Text(lab.keyboardCaptureActive ? "实体键盘按住 \(lab.physicalKeys.count) 键；按下、松开均进入固件矩阵。" : "单击虚拟按键会按下 50 ms，再松开。").font(.caption).foregroundStyle(.secondary)
                        }.padding(6)
                    }
                    GroupBox("电源与报告队列") {
                        VStack(spacing:6) {
                            LabeledContent("Timer1", value:lab.runtime["power"]["timer1_enabled"].flag ? "开启" : "关闭")
                            LabeledContent("队列 head / tail", value:"\(lab.runtime["queue"]["head"].int) / \(lab.runtime["queue"]["tail"].int)")
                            LabeledContent("快速空闲计数", value:"\(lab.runtime["power"]["fast_idle"].int)")
                            LabeledContent("PM 准备 / 禁止发送", value:"\(lab.runtime["power"]["prepared"].int) / \(lab.runtime["power"]["tx_inhibit"].int)")
                        }.font(.caption).padding(6).frame(maxWidth:.infinity)
                    }
                }
                TraceView(lab:lab)
            }.padding(20)
        }
    }
}

func spacedHex(_ text: String) -> String {
    stride(from:0,to:text.count,by:2).map { i in
        let a = text.index(text.startIndex,offsetBy:i)
        return String(text[a..<text.index(a,offsetBy:min(2,text.count-i))])
    }.joined(separator:" ")
}

struct LEDStrip: View {
    let pixels: [Pixel]
    var body: some View {
        HStack(spacing:2) {
            ForEach(pixels.indices,id:\.self) { i in
                RoundedRectangle(cornerRadius:2).fill(pixels[i].isLit ? pixels[i].color : Color.secondary.opacity(0.12))
                    .frame(minWidth:2, minHeight:12, maxHeight:12).help("LED \(i) · \(pixels[i].description)")
            }
        }.accessibilityLabel("\(pixels.count) 颗 LED；\(pixels.filter(\.isLit).count) 颗点亮")
    }
}

struct TraceView: View {
    @ObservedObject var lab: LabModel
    @State private var selected: String?
    var body: some View {
        VStack(alignment:.leading, spacing:8) {
            HStack { Text("执行追踪").font(.headline); Spacer(); Text("最近 100 条；完整记录可导出").font(.caption).foregroundStyle(.secondary) }
            Table(lab.trace,selection:$selected) {
                TableColumn("时间 ms") { row in Text(row.time).font(.system(size:11,design:.monospaced)) }.width(82)
                TableColumn("事件",value:\.kind).width(75)
                TableColumn("说明",value:\.message)
                TableColumn("来源") { row in Text(row.source).foregroundStyle(.secondary).help(row.value["origin"].text) }.width(60)
            }.frame(height:185).border(Color.secondary.opacity(0.15))
            if let row = lab.trace.first(where: { $0.id == selected }) {
                Text(row.value.pretty).font(.system(size:10,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
            }
        }
    }
}

struct EnvironmentView: View {
    @ObservedObject var lab: LabModel
    var body: some View {
        ScrollView {
            VStack(alignment:.leading, spacing:18) {
                Text("环境设置").font(.headline)
                Picker("连接",selection:lab.stringBinding("connection")) {
                    Text("2.4G").tag("rf"); Text("USB").tag("usb"); Text("蓝牙").tag("ble")
                }.pickerStyle(.segmented)
                GroupBox("电池") {
                    VStack(alignment:.leading,spacing:10) {
                        environmentSlider(lab,"电量 %","battery_percent",0...100)
                        Toggle("正在充电",isOn:lab.boolBinding("charging"))
                        Toggle("USB 线已连接",isOn:lab.boolBinding("cable"))
                    }.padding(6)
                }
                GroupBox("灯光") {
                    VStack(alignment:.leading,spacing:12) {
                        Toggle("开启主灯",isOn:lab.boolBinding("main_off",inverted:true))
                        Picker("主灯",selection:Binding(get:{lab.environment["main_effect"]?.int ?? 0},set:{lab.setEnvironment("main_effect",.int($0))})) {
                            ForEach(effectNames.indices,id:\.self) { i in Text("\(i) · \(effectNames[i])").tag(i) }
                        }
                        environmentSlider(lab,"亮度","main_brightness",0...9)
                        environmentSlider(lab,"速度（0 快 → 4 慢）","main_speed",0...4)
                        Divider()
                        Picker("侧灯",selection:Binding(get:{lab.environment["side_mode"]?.int ?? 4},set:{lab.setEnvironment("side_mode",.int($0))})) {
                            Text("模式 0").tag(0); Text("模式 1").tag(1); Text("模式 2").tag(2); Text("模式 3").tag(3); Text("4 · 关闭").tag(4)
                        }
                        environmentSlider(lab,"侧灯亮度","side_brightness",0...3)
                    }.padding(6)
                }
                GroupBox("主机与唤醒") {
                    VStack(alignment:.leading,spacing:10) {
                        Toggle("主机可接收报告",isOn:lab.boolBinding("host_ready"))
                        Toggle("接收器已连接",isOn:lab.boolBinding("receiver"))
                        environmentStepper(lab,"ACK 延迟 ms","ack_delay_ms",0...1000)
                        environmentStepper(lab,"唤醒延迟 ms","wake_latency_ms",0...1000)
                    }.padding(6)
                }
                DisclosureGroup("故障注入与键位") {
                    VStack(alignment:.leading,spacing:12) {
                        Toggle("USB 挂起",isOn:lab.boolBinding("host_suspended"))
                        Toggle("蓝牙通知忙",isOn:lab.boolBinding("ble_busy"))
                        environmentStepper(lab,"每 N 包丢 1 包","drop_every",0...10000)
                        Text("N=0 不丢包；N=1 全部丢包。").font(.caption).foregroundStyle(.secondary)
                        environmentStepper(lab,"主机 LED 位图","host_leds",0...31)
                        Picker("Fn 层",selection:lab.stringBinding("keymap_preset")) {
                            Text("Q101 已捕获键位").tag("captured-q101"); Text("镜像默认键位").tag("image")
                        }
                    }.padding(.top,10)
                }
                Button("重置运行环境") { lab.reset() }.frame(maxWidth:.infinity)
            }.controlSize(.small).padding(15)
        }.background(Color(nsColor:.controlBackgroundColor))
    }
}

@MainActor func environmentSlider(_ lab: LabModel,_ title:String,_ key:String,_ range:ClosedRange<Double>) -> some View {
    VStack(alignment:.leading,spacing:4) {
        HStack { Text(title); Spacer(); Text("\(lab.environment[key]?.int ?? 0)").monospacedDigit().foregroundStyle(.secondary) }
        Slider(value:lab.intBinding(key,delayed:true),in:range,step:1).accessibilityLabel(title)
    }
}
@MainActor func environmentStepper(_ lab:LabModel,_ title:String,_ key:String,_ range:ClosedRange<Int>) -> some View {
    Stepper(value:Binding(get:{lab.environment[key]?.int ?? 0},set:{lab.setEnvironment(key,.int($0),delayed:true)}),in:range) {
        HStack { Text(title); Spacer(); Text("\(lab.environment[key]?.int ?? 0)").monospacedDigit().foregroundStyle(.secondary) }
    }
}
