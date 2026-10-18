// Runs the shipping Swift controller against the bundled engine, without a GUI.
import AppKit
import Foundation
import SwiftUI

@MainActor final class AppDelegate { static var shutdown:(()->Void)? }

@main struct NativeControllerTests {
    @MainActor static func main() async {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("mq80-controller-"+UUID().uuidString)
        var checks:[JSONValue] = []
        let output = URL(fileURLWithPath:CommandLine.arguments[2])
        func check(_ label:String,_ value:Bool) throws {
            checks.append(["name":.string(label),"status":.string(value ? "PASS" : "FAIL")])
            if !value { throw LabError(message:label) }
        }
        func key(_ type:NSEvent.EventType,_ code:UInt16,_ flags:NSEvent.ModifierFlags = [],repeatKey:Bool = false) -> NSEvent {
            NSEvent.keyEvent(with:type,location:.zero,modifierFlags:flags,timestamp:0,windowNumber:0,
                             context:nil,characters:"",charactersIgnoringModifiers:"",isARepeat:repeatKey,keyCode:code)!
        }
        do {
            let lab = LabModel(engineResources:URL(fileURLWithPath:CommandLine.arguments[1]),dataDirectory:folder)
            lab.start()
            let bootDeadline = Date().addingTimeInterval(10)
            while !lab.ready && Date()<bootDeadline { try await Task.sleep(nanoseconds:20_000_000) }
            try check("bundled engine starts through Swift transport",lab.ready)
            try check("both immutable bundled firmware images are selectable",lab.images.contains{$0["name"].text == "MQ80 官方 V0109" && $0["size"].int == 126292} && lab.images.contains{$0["name"].text == "Fn+B 侧灯电量条 v2" && $0["size"].int == 126756})

            // No yield between user intents: this caught the snapshot ordering bug.
            lab.queryBattery(); lab.pause()
            let snapshot = try await lab.capture("snapshot")
            let report = try await lab.capture("report")
            try check("immediate Fn+B then snapshot includes completed input",report["state"]["battery"]["query_active"].flag && snapshot["runtime"]["now_us"].double == report["state"]["time_ms"].double*1000)
            try await Task.sleep(nanoseconds:300_000_000)
            let frozen = try await lab.capture("report")
            try check("pause prevents queued query from restarting clock",!lab.playing && frozen["state"]["time_ms"] == report["state"]["time_ms"])

            lab.setEnvironment("battery_percent",21,delayed:true)
            lab.queryBattery();lab.pause()
            let battery = try await lab.capture("report")
            try check("query and export flush debounced battery input",battery["state"]["environment"]["battery_percent"].int == 21 && battery["state"]["battery"]["cache"].int == 21 && battery["state"]["battery"]["query_active"].flag)
            lab.play();try await Task.sleep(nanoseconds:3_400_000_000);lab.pause()
            let expired = try await lab.capture("report")
            try check("native clock expires overlay and restores side off",!expired["state"]["battery"]["query_active"].flag && expired["state"]["rgb"]["side"].values.allSatisfy{$0.values.allSatisfy{$0.int == 0}})

            lab.reset();lab.tap("3,1");lab.tap("3,1");lab.tap("3,1")
            let typing = try await lab.capture("report")
            try check("rapid key intents preserve order and release",typing["state"]["host_text"].text == "aaa" && typing["state"]["hid"].text == "0000000000000000")
            lab.reset();lab.command(["action":"sleep","gate":"fast"],pauseFirst:true);lab.tap("3,1")
            let wake = try await lab.capture("report")
            try check("native controller preserves first key after PM",wake["state"]["host_text"].text == "a" && wake["trace"].values.contains{$0["kind"].text == "wake"})

            lab.reset();lab.combo = true;lab.tap("4,0");lab.tap("3,1");lab.sendCombo()
            let combo = try await lab.capture("report")
            try check("mouse chord sends modifiers and releases after tap",combo["state"]["host_text"].text == "A" && combo["state"]["hid"].text == "0000000000000000" && lab.selectedKeys.count == 2)
            lab.releaseKeys()
            try check("release button immediately clears visible chord selection",lab.selectedKeys.isEmpty)
            let cleared = try await lab.capture("report")
            try check("release button clears matrix and HID with visible feedback",cleared["state"]["keys"].values.isEmpty && cleared["state"]["hid"].text == "0000000000000000" && lab.status.contains("已松开"))
            lab.tap("3,1");lab.command(["action":"keys","keys":.array(["3,1"])]);lab.command(["action":"advance","ms":50])
            _ = try await lab.capture("report")
            lab.combo = false
            let unchecked = try await lab.capture("report")
            try check("unchecking chord mode clears selection and releases an actual held key",lab.selectedKeys.isEmpty && unchecked["state"]["keys"].values.isEmpty && unchecked["state"]["hid"].text == "0000000000000000")

            lab.reset();lab.setKeyboardCapture(true)
            lab.handlePhysicalEvent(key(.flagsChanged,0x38,.shift))
            lab.handlePhysicalEvent(key(.keyDown,0x00,.shift))
            let held = try await lab.capture("report")
            try check("physical Shift+A reaches real firmware as a held chord",lab.physicalKeys == ["4,0","3,1"] && held["state"]["host_text"].text == "A" && held["state"]["hid"].text == "0200040000000000")
            lab.handlePhysicalEvent(key(.keyDown,0x00,.shift,repeatKey:true))
            let repeatReport = try await lab.capture("report")
            try check("macOS repeat does not duplicate firmware keydown",repeatReport["state"]["host_text"].text == "A" && repeatReport["state"]["time_ms"] == held["state"]["time_ms"])
            lab.handlePhysicalEvent(key(.keyUp,0x00,.shift));lab.handlePhysicalEvent(key(.flagsChanged,0x38))
            let up = try await lab.capture("report")
            try check("physical keyup and modifier release produce zero HID",lab.physicalKeys.isEmpty && up["state"]["hid"].text == "0000000000000000")
            lab.handlePhysicalEvent(key(.keyDown,0x00,.shift));lab.handlePhysicalEvent(key(.keyUp,0x00))
            let inherited = try await lab.capture("report")
            try check("modifier flags on first key work without prior flagsChanged",inherited["state"]["host_text"].text == "AA" && inherited["state"]["hid"].text == "0000000000000000" && lab.physicalKeys.isEmpty)

            lab.handlePhysicalEvent(key(.flagsChanged,0x38,NSEvent.ModifierFlags(rawValue:NSEvent.ModifierFlags.shift.rawValue|2)))
            lab.handlePhysicalEvent(key(.flagsChanged,0x3c,NSEvent.ModifierFlags(rawValue:NSEvent.ModifierFlags.shift.rawValue|6)))
            lab.handlePhysicalEvent(key(.flagsChanged,0x38,NSEvent.ModifierFlags(rawValue:NSEvent.ModifierFlags.shift.rawValue|4)))
            let rightShift = try await lab.capture("report")
            try check("releasing left Shift preserves held right Shift",lab.physicalKeys == ["4,13"] && rightShift["state"]["hid"].text == "2000000000000000")
            lab.handlePhysicalEvent(key(.flagsChanged,0x3c))
            _ = try await lab.capture("report")

            lab.handlePhysicalEvent(key(.keyDown,0x00));lab.releaseKeys()
            let released = try await lab.capture("report")
            lab.handlePhysicalEvent(key(.keyDown,0x00,repeatKey:true));lab.handlePhysicalEvent(key(.keyDown,0x00))
            let suppressed = try await lab.capture("report")
            try check("release-all suppresses still-held physical key until keyup",lab.physicalKeys.isEmpty && suppressed["state"]["hid"].text == "0000000000000000" && suppressed["state"]["host_text"] == released["state"]["host_text"])
            lab.handlePhysicalEvent(key(.keyUp,0x00));lab.handlePhysicalEvent(key(.keyDown,0x00));lab.handlePhysicalEvent(key(.keyUp,0x00))
            let repressed = try await lab.capture("report")
            try check("released physical key can be pressed normally again",repressed["state"]["host_text"].text == released["state"]["host_text"].text+"a" && repressed["state"]["hid"].text == "0000000000000000")

            lab.handlePhysicalEvent(key(.flagsChanged,0x36,.command));lab.handlePhysicalEvent(key(.keyDown,0x0b,.command))
            lab.handlePhysicalEvent(key(.keyUp,0x0b,.command));lab.handlePhysicalEvent(key(.flagsChanged,0x36));lab.pause()
            let fn = try await lab.capture("report")
            try check("right Command+B invokes real Fn+B without typing b",fn["state"]["battery"]["query_active"].flag && fn["state"]["host_text"] == repressed["state"]["host_text"] && lab.physicalKeys.isEmpty)

            lab.reset();lab.setVirtualFn(true);lab.handlePhysicalEvent(key(.keyDown,0x0b));lab.handlePhysicalEvent(key(.keyUp,0x0b));lab.pause()
            let latch = try await lab.capture("report")
            try check("virtual Fn latch permits battery query using an ordinary B key",latch["state"]["battery"]["query_active"].flag && latch["state"]["host_text"].text.isEmpty && lab.virtualFnHeld)
            lab.releaseKeys();_ = try await lab.capture("report")
            try check("release-all clears virtual Fn latch",!lab.virtualFnHeld && lab.physicalKeys.isEmpty)

            lab.handlePhysicalEvent(key(.keyDown,0x00));lab.setKeyboardCapture(false)
            let stopped = try await lab.capture("report")
            lab.handlePhysicalEvent(key(.keyDown,0x0b))
            let disabled = try await lab.capture("report")
            try check("capture stop releases held input and ignores later events",!lab.keyboardCaptureActive && lab.physicalKeys.isEmpty && stopped["state"]["hid"].text == "0000000000000000" && stopped["state"]["host_text"] == disabled["state"]["host_text"])
            lab.setKeyboardCapture(true);lab.handlePhysicalEvent(key(.keyDown,0x00));lab.page = .inspect
            let pageChanged = try await lab.capture("report")
            try check("leaving the workbench automatically releases keyboard capture",!lab.keyboardCaptureActive && lab.physicalKeys.isEmpty && pageChanged["state"]["hid"].text == "0000000000000000")
            lab.page = .workbench;lab.setKeyboardCapture(true);lab.handlePhysicalEvent(key(.flagsChanged,0x37,.command));lab.handlePhysicalEvent(key(.keyDown,0x2f,.command))
            let escaped = try await lab.capture("report")
            try check("Command-period exits capture and releases the modifier",!lab.keyboardCaptureActive && escaped["state"]["hid"].text == "0000000000000000")

            lab.setKeyboardCapture(true);lab.handlePhysicalEvent(key(.keyDown,0x00));lab.reset()
            let reset = try await lab.capture("report")
            try check("reset cancels keyboard capture and pending held state",!lab.keyboardCaptureActive && lab.physicalKeys.isEmpty && reset["state"]["keys"].values.isEmpty && reset["state"]["host_text"].text.isEmpty)

            // Exercise the shipping notification observers with an unattached-to-screen
            // AppKit window, without synthesizing OS input or changing the user's focus.
            _ = NSApplication.shared
            let focusWindow = NSWindow(contentRect:NSRect(x:0,y:0,width:100,height:100),styleMask:[],backing:.buffered,defer:false)
            let focusView = KeyboardInputFocusView();focusView.lab = lab;focusWindow.contentView = focusView
            for notification in [NSWindow.didResignKeyNotification,NSWindow.willBeginSheetNotification,NSApplication.didResignActiveNotification] {
                lab.setKeyboardCapture(true);lab.handlePhysicalEvent(key(.keyDown,0x00))
                NotificationCenter.default.post(name:notification,object:notification == NSApplication.didResignActiveNotification ? NSApp : focusWindow)
                let unfocused = try await lab.capture("report")
                try check("AppKit observer releases input for "+notification.rawValue,!lab.keyboardCaptureActive && lab.physicalKeys.isEmpty && unfocused["state"]["hid"].text == "0000000000000000")
            }
            lab.setKeyboardCapture(true);lab.handlePhysicalEvent(key(.keyDown,0x00));focusView.detach()
            let detached = try await lab.capture("report")
            try check("removing AppKit capture view releases input and observers",!lab.keyboardCaptureActive && detached["state"]["hid"].text == "0000000000000000")
            lab.reset();_ = try await lab.capture("report")

            lab.command(["action":"environment","values":["battery_percent":101]])
            _ = try await lab.capture("report")
            try check("rejected input is surfaced",lab.issue != nil && !lab.playing)
            lab.reset();_ = try await lab.capture("report")
            try check("reset recovers controller after rejected input",lab.issue == nil && lab.runtime["error"].isNull)

            lab.startJob("suite")
            let suiteDeadline = Date().addingTimeInterval(40)
            while lab.job["status"].text != "complete" && lab.job["status"].text != "error" && Date()<suiteDeadline { try await Task.sleep(nanoseconds:100_000_000) }
            try check("native job controller completes 10/10 suite",lab.job["result"]["passed"].int == 10 && lab.job["result"]["status"].text == "PASS")
            lab.prepareSideHID();lab.controlSideHID("rainbow");lab.pause()
            let sideStart = try await lab.capture("report")
            try check("one-click HID setup selects bundled image and virtual USB",lab.sideHIDReady && sideStart["state"]["environment"]["connection"].text == "usb" && sideStart["state"]["side_hid"]["mode"].text == "rainbow")
            try check("immediate pause freezes a queued rainbow start",!lab.playing && sideStart["state"]["side_hid"]["frames_sent"].int == 1)
            try await Task.sleep(nanoseconds:180_000_000)
            let sideFrozen = try await lab.capture("report")
            try check("paused HID animation sends no background frames",sideFrozen["state"]["side_hid"] == sideStart["state"]["side_hid"] && sideFrozen["state"]["time_ms"] == sideStart["state"]["time_ms"])

            lab.setSideHIDColor(Color(red:1,green:0,blue:0));lab.setSideHIDColor(Color(red:0,green:0,blue:1),segment:0);lab.pause()
            let sideColors = try await lab.capture("report")
            try check("coalesced color edits drive nine independent real firmware pixels",sideColors["state"]["rgb"]["side"].values[0].values.map(\.int) == [0,0,162] && sideColors["state"]["rgb"]["side"].values[1].values.map(\.int) == [162,0,0] && !lab.playing)
            lab.setSideHIDBrightness(25);lab.pause()
            let dimmed = try await lab.capture("report")
            try check("live brightness is scaled by host and firmware",dimmed["state"]["rgb"]["side"].values[0].values.map(\.int) == [0,0,40] && dimmed["state"]["side_hid"]["brightness"].int == 25)

            lab.controlSideHID("rainbow");lab.setSideHIDPeriod(1);lab.pause()
            _ = try await lab.capture("report")
            lab.controlSideHID("rainbow");lab.play()
            try await Task.sleep(nanoseconds:350_000_000);lab.pause()
            let sideAnimated = try await lab.capture("report")
            try check("native clock streams changing rainbow frames",sideAnimated["state"]["side_hid"]["frames_sent"].int > dimmed["state"]["side_hid"]["frames_sent"].int+3 && sideAnimated["state"]["side_hid"]["period_ms"].int == 1000)

            lab.setEnvironment("battery_percent",50,delayed:true);lab.queryBattery();lab.pause()
            let sideQuery = try await lab.capture("report")
            try check("Fn+B has priority over native HID animation",sideQuery["state"]["battery"]["query_active"].flag && sideQuery["state"]["rgb"]["side"].values[0].values.map(\.int) == [162,162,0] && sideQuery["state"]["side_hid"]["mode"].text == "rainbow")
            lab.play();try await Task.sleep(nanoseconds:3_400_000_000);lab.pause()
            let sideResumed = try await lab.capture("report")
            try check("native rainbow survives the full battery override",!sideResumed["state"]["battery"]["query_active"].flag && sideResumed["state"]["side_hid"]["active"].flag && sideResumed["state"]["side_hid"]["mode"].text == "rainbow")

            lab.setSideHIDColor(.red);lab.controlSideHID("release")
            let sideReleased = try await lab.capture("report")
            try check("release cancels pending edits and restores original off state",sideReleased["state"]["side_hid"]["mode"].text == "off" && !sideReleased["state"]["side_hid"]["active"].flag && sideReleased["state"]["rgb"]["side"].values.allSatisfy{$0.values.allSatisfy{$0.int == 0}})
            lab.controlSideHID("rainbow");lab.pause();_ = try await lab.capture("report")
            lab.controlSideHID("stop");lab.command(["action":"preview","ms":2100])
            let sideTimeout = try await lab.capture("report")
            try check("stop-sending action lets the firmware watchdog expire",!sideTimeout["state"]["side_hid"]["active"].flag && sideTimeout["state"]["rgb"]["side"].values.allSatisfy{$0.values.allSatisfy{$0.int == 0}})
            try check("native HID controls never write Flash",sideTimeout["state"]["stats"]["flash_operations"].int == 0)
            lab.setSideHIDColor(.green);lab.reset()
            let sideReset = try await lab.capture("report")
            try check("reset cancels pending HID edits and animation",sideReset["state"]["side_hid"]["mode"].text == "off" && !lab.playing && lab.issue == nil)

            lab.prepareSideHID(connection:"rf");lab.controlSideHID("rainbow");lab.pause()
            let rfStart = try await lab.capture("report")
            try check("firmware version selects v3.1.0 instead of guessing a legacy version",rfStart["state"]["firmware"]["firmware_version"].text == "3.1.0" && rfStart["state"]["firmware"]["firmware_build"].int == 3)
            try check("version caption lists the firmware feature flags",lab.firmwareVersionCaption.contains("v3.1.0") && lab.firmwareVersionCaption.contains("电量查询") && lab.firmwareVersionCaption.contains("2.4G 控灯") && lab.firmwareVersionCaption.contains("本地灯效"))
            try check("2.4G setup selects capable firmware and receiver without USB cable",lab.sideHIDReady && rfStart["state"]["firmware"]["side_hid_rf"].flag && rfStart["state"]["environment"]["connection"].text == "rf" && !rfStart["state"]["environment"]["cable"].flag)
            try check("wireless rainbow defaults to ten frames per second",rfStart["state"]["side_hid"]["fps"].int == 10 && rfStart["state"]["side_hid"]["transport"].text == "virtual-rf")
            lab.setSideHIDColor(Color(red:0,green:0,blue:1));lab.pause()
            let rfColor = try await lab.capture("report")
            try check("wireless color runs real receive reply and closing ACKs",rfColor["state"]["rgb"]["side"].values[0].values.map(\.int) == [0,0,162] && rfColor["state"]["stats"]["calls"]["rf_configuration_closing_ack"].int >= 9)
            lab.controlSideHID("rainbow");lab.play()
            try await Task.sleep(nanoseconds:450_000_000);lab.pause()
            let rfLive = try await lab.capture("report")
            try check("native clock streams wireless animation and holds stock idle gate",rfLive["state"]["side_hid"]["frames_sent"].int >= rfColor["state"]["side_hid"]["frames_sent"].int+4 && rfLive["state"]["power"]["fast_idle"].int == 0)
            lab.queryBattery();lab.pause()
            let rfQuery = try await lab.capture("report")
            try check("Fn+B retains priority during wireless control",rfQuery["state"]["battery"]["query_active"].flag && rfQuery["state"]["side_hid"]["active"].flag)
            lab.controlSideHID("stop");lab.command(["action":"preview","ms":3100]);lab.command(["action":"sleep","gate":"fast"],pauseFirst:true)
            let rfSleep = try await lab.capture("report")
            try check("wireless sender timeout permits original sleep",!rfSleep["state"]["side_hid"]["active"].flag && rfSleep["state"]["status"].text == "sleeping")
            lab.tap("3,1")
            let rfWake = try await lab.capture("report")
            try check("first key still works after wireless lighting timeout and sleep",rfWake["state"]["host_text"].text == "a" && rfWake["state"]["hid"].text == "0000000000000000")
            lab.setSideHIDColor(.red);lab.prepareSideHID(connection:"usb");lab.prepareSideHID(connection:"rf");lab.controlSideHID("rainbow");lab.pause()
            let rfSwitch = try await lab.capture("report")
            try check("rapid transport switches cancel stale edits and preserve intent order",lab.issue == nil && rfSwitch["state"]["environment"]["connection"].text == "rf" && rfSwitch["state"]["side_hid"]["mode"].text == "rainbow" && rfSwitch["state"]["side_hid"]["frames_sent"].int == 1)
            try check("wireless controls never write Flash",rfSwitch["state"]["stats"]["flash_operations"].int == 0)

            let result:JSONValue = ["version":"2.4.1","status":"PASS","no_device_access":true,"checks":.array(checks),"suite":lab.job]
            try result.data(pretty:true).write(to:output)
            AppDelegate.shutdown?()
            print("PASS: \(checks.count) Swift controller checks; native suite 10/10")
        } catch {
            let result:JSONValue = ["status":"FAIL","checks":.array(checks),"error":.string(error.localizedDescription)]
            try? result.data(pretty:true).write(to:output)
            AppDelegate.shutdown?()
            print("FAIL: \(error.localizedDescription)")
            exit(1)
        }
        try? FileManager.default.removeItem(at:folder)
    }
}
