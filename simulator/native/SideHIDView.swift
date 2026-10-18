import AppKit
import SwiftUI

struct SideHIDView: View {
    @ObservedObject var lab: LabModel
    private let presets: [(String,Color)] = [("红",.red),("橙",.orange),("黄",.yellow),("绿",.green),("青",.cyan),("蓝",.blue),("紫",.purple)]

    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:18) {
                ClockBar(lab:lab)
                HStack(alignment:.top) {
                    VStack(alignment:.leading,spacing:5) {
                        Text("侧灯 HID").font(.title2.weight(.semibold))
                        Text("调颜色、播放彩虹，实时观察实验固件的侧灯输出。")
                            .foregroundStyle(.secondary)
                        Text(lab.firmwareVersionCaption).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(lab.sideHIDReady ? "重置侧灯测试" : "载入实验固件") { lab.prepareSideHID(connection:lab.sideHIDConnection) }
                        .disabled(!lab.ready)
                }
                Picker("传输",selection:Binding(get:{lab.sideHIDConnection},set:{lab.prepareSideHID(connection:$0)})) {
                    Text("虚拟 USB · 20 FPS").tag("usb")
                    Text("虚拟 2.4G · 10 FPS").tag("rf")
                }.pickerStyle(.segmented).disabled(!lab.ready)
                GroupBox {
                    VStack(alignment:.leading,spacing:14) {
                        HStack {
                            Label(lab.sideHIDCaption,systemImage:lab.sideHID["active"].flag ? "circle.fill" : "circle")
                                .foregroundStyle(lab.sideHID["active"].flag ? Color.green : .secondary)
                            Spacer()
                            Text("固件实际输出").font(.caption).foregroundStyle(.secondary)
                        }
                        HStack(spacing:8) {
                            ForEach(lab.sidePixels.indices,id:\.self) { i in
                                VStack(spacing:6) {
                                    RoundedRectangle(cornerRadius:8)
                                        .fill(lab.sidePixels[i].isLit ? lab.sidePixels[i].color : Color.secondary.opacity(0.12))
                                        .frame(height:52)
                                    Text("\(i+1)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                }.help(lab.sidePixels[i].description)
                            }
                        }
                        HStack {
                            Text("已发送 \(lab.sideHID["frames_sent"].int) 帧").monospacedDigit()
                            if lab.sideHID["active"].flag { Text("超时余量 \(lab.sideHID["remaining_ms"].int) ms").monospacedDigit() }
                            Spacer()
                            Button("Fn＋B 查询电量",action:lab.queryBattery).disabled(!lab.sideHIDReady)
                        }.font(.caption).foregroundStyle(.secondary)
                    }.padding(10)
                }
                controls.disabled(!lab.sideHIDReady)
                GroupBox {
                    KeyboardSurface(keys:lab.keys,main:lab.mainPixels,side:lab.sidePixels,selected:lab.selectedKeys,
                                    lighting:lab.showKeyLighting,coordinates:false,onKey:lab.tap)
                        .aspectRatio(17.1/6.35,contentMode:.fit).frame(maxHeight:290).padding(8)
                }
                Text("离线虚拟 \(lab.sideHIDTransportName)：颜色经 HID 报文进入实验固件，再由固件渲染。暂停会冻结动画与倒计时；Fn+B 临时覆盖约 3 秒，结束后恢复。")
                    .font(.caption).foregroundStyle(.secondary)
                if lab.sideHIDConnection == "rf" {
                    Text("2.4G 控制有效时保持无线接收；释放或停发超时后恢复原休眠计时。已休眠的键盘需要先按键唤醒。接收器与空口时序仍为模型，实机效果待验证。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.padding(20)
        }
    }

    private var controls: some View {
        GroupBox("电脑端控制") {
            VStack(alignment:.leading,spacing:15) {
                HStack(spacing:12) {
                    ColorPicker("整条颜色",selection:lab.sideHIDColorBinding(),supportsOpacity:false).fixedSize()
                    ForEach(presets,id:\.0) { name,color in
                        Button { lab.setSideHIDColor(color) } label: {
                            Circle().fill(color).frame(width:19,height:19)
                        }.buttonStyle(.plain).help("整条\(name)色").accessibilityLabel("侧灯\(name)色")
                    }
                    Spacer(minLength:0)
                    Button("应用颜色") { lab.controlSideHID("solid") }
                }
                HStack(spacing:14) {
                    Text("亮度").frame(width:45,alignment:.leading)
                    Slider(value:Binding(get:{lab.sideHIDBrightness},set:lab.setSideHIDBrightness),in:0...100,step:1)
                        .accessibilityLabel("侧灯 HID 亮度")
                    Text("\(Int(lab.sideHIDBrightness))%").monospacedDigit().frame(width:44,alignment:.trailing)
                }
                HStack(spacing:14) {
                    Text("周期").frame(width:45,alignment:.leading)
                    Slider(value:Binding(get:{lab.sideHIDPeriod},set:lab.setSideHIDPeriod),in:0.5...10,step:0.1)
                        .accessibilityLabel("彩虹周期秒")
                    Text(String(format:"%.1f s",lab.sideHIDPeriod)).monospacedDigit().frame(width:44,alignment:.trailing)
                }
                HStack(spacing:10) {
                    Button { lab.controlSideHID("rainbow") } label: { Label("播放彩虹",systemImage:"play.fill") }
                        .buttonStyle(.borderedProminent)
                    Button("释放控制") { lab.controlSideHID("release") }
                    Button("停止发送 · 测试超时") { lab.controlSideHID("stop") }
                        .help("停止发送帧，继续虚拟时钟，约 2 秒后固件自动恢复原侧灯状态。")
                }
                DisclosureGroup("逐段调色") {
                    LazyVGrid(columns:Array(repeating:GridItem(.flexible()),count:3),alignment:.leading,spacing:12) {
                        ForEach(0..<9) { i in
                            ColorPicker("第 \(i+1) 段",selection:lab.sideHIDColorBinding(i),supportsOpacity:false)
                        }
                    }.padding(.top,10)
                }
            }.controlSize(.small).padding(10)
        }
    }
}
