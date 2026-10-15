import AppKit
import SwiftUI

struct LightingView: View {
    @ObservedObject var lab: LabModel
    var body: some View {
        ScrollView {
            VStack(alignment:.leading, spacing:20) {
                ClockBar(lab:lab)
                HStack {
                    VStack(alignment:.leading,spacing:4) {
                        Text("固件灯效，连续预览").font(.title2.weight(.semibold))
                        Text("执行镜像中的初始化、动画计数与帧函数，显示实际 RGB 输出。").font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Fn＋B 查询电量",action:lab.queryBattery)
                }
                HStack(spacing:8) {
                    Button("播放波浪") { lab.previewLighting(1) }
                    Button("播放彩云") { lab.previewLighting(2) }
                    Button("播放漩涡") { lab.previewLighting(3) }
                    Button("常亮") { lab.previewLighting(6) }
                    Spacer()
                    Toggle("键帽灯光",isOn:$lab.showKeyLighting).toggleStyle(.checkbox)
                }.controlSize(.small)
                GroupBox {
                    KeyboardSurface(keys:lab.keys,main:lab.mainPixels,side:lab.sidePixels,selected:lab.selectedKeys,lighting:lab.showKeyLighting,coordinates:false,onKey:lab.tap)
                        .aspectRatio(17.1/6.35,contentMode:.fit).frame(maxHeight:360).padding(8)
                }
                Text("键帽位置按固件几何表与官方布局推导，尚未逐灯实物校准。颜色、亮灭与下方原始灯珠帧来自固件。").font(.caption).foregroundStyle(.secondary)
                HStack(alignment:.top,spacing:20) {
                    GroupBox("主灯 · 原始索引 0–83") {
                        LazyVGrid(columns:Array(repeating:GridItem(.flexible(),spacing:5),count:14),spacing:6) {
                            ForEach(lab.mainPixels.indices,id:\.self) { i in pixelTile(lab.mainPixels[i],label:"\(i)") }
                        }.padding(10)
                    }
                    GroupBox("侧灯 · 84–92") {
                        VStack(spacing:5) {
                            ForEach(Array(lab.sidePixels.indices.reversed()),id:\.self) { i in
                                HStack(spacing:7) {
                                    RoundedRectangle(cornerRadius:3).fill(lab.sidePixels[i].isLit ? lab.sidePixels[i].color : Color.secondary.opacity(0.12)).frame(width:40,height:13)
                                    Text("\(84+i)").font(.system(size:10,design:.monospaced)).foregroundStyle(.secondary)
                                }.help(lab.sidePixels[i].description)
                            }
                            Text(lab.queryCaption).font(.caption).monospacedDigit().padding(.top,3)
                        }.padding(10)
                    }.fixedSize(horizontal:true,vertical:false)
                }
                Text("效果编号 0–18 可在右侧选择。部分按键触发效果仍需补齐运行时状态；10 ms 动画节拍是模型设定，预览不代表实物帧率。").font(.caption).foregroundStyle(.secondary)
            }.padding(20)
        }
    }
    private func pixelTile(_ pixel:Pixel,label:String) -> some View {
        VStack(spacing:3) {
            RoundedRectangle(cornerRadius:4).fill(pixel.isLit ? pixel.color : Color.secondary.opacity(0.12)).frame(height:19)
            Text(label).font(.system(size:9,design:.monospaced)).foregroundStyle(.secondary)
        }.help("LED \(label) · \(pixel.description)")
    }
}

struct ResultRow: Identifiable {
    let id:Int
    let value:JSONValue
    var title:String { value["name"].text.isEmpty ? "步骤 \(id+1) · \(value["step"]["type"].text)" : value["name"].text }
    var result:String { value["status"].text }
    var detail:String { !value["error"].isNull ? value["error"].text : value["message"].text }
}

struct ScenariosView: View {
    @ObservedObject var lab:LabModel
    @State private var selected:Int?
    @State private var resultDetail = false
    private var result:JSONValue { lab.job["result"] }
    private var rows:[ResultRow] {
        let values = result["rows"].isNull ? result["steps"].values : result["rows"].values
        return values.enumerated().map { ResultRow(id:$0.offset,value:$0.element) }
    }
    var body: some View {
        VStack(alignment:.leading,spacing:16) {
            HStack {
                Text("场景与回归").font(.title2.weight(.semibold))
                Spacer()
                Button("运行全部回归") { lab.startJob("suite") }.disabled(lab.jobRunning)
            }
            HStack(alignment:.top,spacing:20) {
                scenarioEditor
                VStack(alignment:.leading,spacing:18) { comparePanel; otaPanel }.frame(width:245)
            }
            Divider()
            jobView
            Spacer(minLength:0)
        }.padding(22)
    }
    private var scenarioEditor:some View {
        VStack(alignment:.leading,spacing:10) {
            HStack {
                Picker("场景",selection:Binding(get:{lab.selectedScenario},set:lab.selectScenario)) {
                    ForEach(lab.scenarios,id:\.self) { item in Text(item["name"].text).tag(item["id"].text) }
                }.labelsHidden()
                Button { lab.importJSON(snapshot:false) } label:{ Image(systemName:"square.and.arrow.down") }.help("导入场景 JSON…")
                Button(action:lab.exportScenario) { Image(systemName:"square.and.arrow.up") }.help("导出编辑的场景…")
                Button("运行场景") { lab.startJob("scenario") }.buttonStyle(.borderedProminent).disabled(lab.jobRunning)
            }
            TextEditor(text:$lab.scenarioJSON).font(.system(size:11,design:.monospaced)).disableAutocorrection(true)
                .frame(minHeight:210,maxHeight:290).border(Color.secondary.opacity(0.22)).accessibilityLabel("场景 JSON 编辑器")
            Text("运行使用独立虚拟机；完成后可载入末态，在工作台继续调试。").font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth:.infinity)
    }
    private var comparePanel:some View {
        GroupBox("双镜像对比") {
            VStack(alignment:.leading,spacing:12) {
                Text("当前镜像与对照镜像执行左侧同一场景。").font(.caption).foregroundStyle(.secondary)
                comparisonPicker
                Button("运行对比") { lab.startJob("compare") }.disabled(lab.jobRunning)
            }.padding(8)
        }
    }
    private var otaPanel:some View {
        GroupBox("OTA / CRC 离线测试") {
            VStack(alignment:.leading,spacing:10) {
                Text("对照镜像作为候选固件，仅操作虚拟 NOR。").font(.caption).foregroundStyle(.secondary)
                Picker("测试分支",selection:$lab.otaCase) {
                    Text("正常镜像").tag("valid_image")
                    Text("传输不完整").tag("incomplete")
                    Text("CRC 错误").tag("crc_error")
                    Text("镜像头回读错误").tag("header_readback_error")
                }.labelsHidden()
                Button("运行 OTA 测试") { lab.startJob("ota") }.disabled(lab.jobRunning)
            }.padding(8)
        }
    }
    private var comparisonPicker:some View {
        Picker("对照镜像",selection:$lab.comparisonFirmware) {
            ForEach(lab.images,id:\.self) { image in Text(image["name"].text).tag(image["id"].text) }
        }.labelsHidden()
    }
    @ViewBuilder private var jobView:some View {
        if lab.job.isNull {
            Label("选择场景，或运行 10 项内置回归。",systemImage:"checklist").foregroundStyle(.secondary).padding(.vertical,30)
        } else {
            HStack {
                if lab.jobRunning { ProgressView().controlSize(.small) }
                Text(lab.jobRunning ? lab.job["current"].text : (lab.job["status"].text == "error" ? "测试未完成" : result["status"].text)).font(.headline)
                    .foregroundStyle(result["status"].text == "FAIL" || lab.job["status"].text == "error" ? Color.red : Color.primary)
                if !result["total"].isNull { Text("\(result["passed"].int) / \(result["total"].int) 通过").foregroundStyle(.secondary) }
                Spacer()
                if lab.job["kind"].text == "scenario" && lab.job["status"].text == "complete" { Button("载入末态",action:lab.loadJob) }
                Button("导出结果…",action:lab.exportJob).disabled(lab.jobRunning)
            }
            if lab.jobRunning { ProgressView(value:lab.job["completed"].double,total:max(1,lab.job["total"].double)) }
            if !rows.isEmpty {
                Table(rows,selection:$selected) {
                    TableColumn("场景 / 步骤",value:\.title)
                    TableColumn("结果") { row in Text(row.result).foregroundStyle(row.result == "PASS" ? Color.green : Color.red) }.width(70)
                    TableColumn("说明",value:\.detail)
                }.frame(minHeight:130,maxHeight:230).border(Color.secondary.opacity(0.15))
            }
            DisclosureGroup("结果详情",isExpanded:$resultDetail) {
                ScrollView {
                    Text((rows.first(where:{$0.id == selected})?.value ?? lab.job).pretty)
                        .font(.system(size:10,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading).padding(8)
                }.frame(minHeight:90,maxHeight:180)
            }
        }
    }
}

struct InspectView:View {
    @ObservedObject var lab:LabModel
    var body:some View {
        ScrollView {
            VStack(alignment:.leading,spacing:20) {
                Text("运行时检查").font(.title2.weight(.semibold))
                GroupBox("虚拟内存与 CPU 寄存器") {
                    VStack(alignment:.leading,spacing:12) {
                        HStack {
                            TextField("地址，如 0x80b80",text:$lab.memoryAddress).font(monoFont).frame(width:170)
                            Stepper("\(lab.memorySize) 字节",value:$lab.memorySize,in:1...256).frame(width:170)
                            Button("读取",action:lab.readMemory)
                            Spacer()
                            Button("保存…") { lab.save(Data(lab.memoryText.utf8),name:"MQ80-memory.txt") }
                        }
                        ScrollView([.horizontal,.vertical]) {
                            Text(lab.memoryText).font(.system(size:11,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                        }.frame(height:210)
                    }.padding(10)
                }
                GroupBox("执行统计") {
                    VStack(spacing:8) {
                        LabeledContent("真实指令",value:"\(lab.runtime["stats"]["instructions"].int)")
                        LabeledContent("Andes 扩展",value:"\(lab.runtime["stats"]["andes_extensions"].int)")
                        LabeledContent("已覆盖地址",value:"\(lab.runtime["stats"]["covered_addresses"].int)")
                        LabeledContent("Flash 操作",value:"\(lab.runtime["stats"]["flash_operations"].int)")
                        LabeledContent("丢弃的追踪条目",value:"\(lab.runtime["stats"]["trace_dropped"].int)")
                    }.font(.callout).padding(10)
                }
                DisclosureGroup("完整状态 JSON") {
                    Text(lab.runtime.pretty).font(.system(size:10,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                }
                Text("只读虚拟内存；报告包含寄存器访问、包、断言与模型边界。").font(.caption).foregroundStyle(.secondary)
            }.padding(22)
        }
    }
}

struct CoverageView:View {
    @ObservedObject var lab:LabModel
    var body:some View {
        ScrollView {
            VStack(alignment:.leading,spacing:20) {
                Text("建模覆盖与验证边界").font(.title2.weight(.semibold))
                Text("原生界面通过进程管道连接 Python / Unicorn 引擎。运行镜像中的真实机器码，外部设备由确定性模型响应。").foregroundStyle(.secondary)
                ForEach(lab.bootstrap["coverage"].values,id:\.self) { item in
                    GroupBox {
                        VStack(alignment:.leading,spacing:7) {
                            HStack {
                                Text(item["name"].text).font(.headline)
                                Spacer()
                                Text(item["level"].text == "real" ? "真实代码" : item["level"].text == "partial" ? "部分建模" : "环境模型")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Text(item["detail"].text).font(.callout).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                        }.padding(8)
                    }
                }
                GroupBox("当前固件") { Text(lab.runtime["firmware"].pretty).font(.system(size:11,design:.monospaced)).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading).padding(10) }
            }.padding(22)
        }
    }
}
