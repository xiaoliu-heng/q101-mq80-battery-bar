import AppKit
import SwiftUI

// Physical ANSI positions, independent of the imported firmware's remapped keycodes.
// macOS virtual keycodes follow HIToolbox/Events.h; targets are the Q101/MQ80 matrix.
struct PhysicalKeyboardState {
    private var held: [UInt16:String] = [:]
    private var suppressed: Set<UInt16> = []
    var matrixKeys: Set<String> { Set(held.values) }
    static let positions: [UInt16:String] = [
        0x00:"3,1",0x01:"3,2",0x02:"3,3",0x03:"3,4",0x04:"3,6",0x05:"3,5",
        0x06:"4,2",0x07:"4,3",0x08:"4,4",0x09:"4,5",0x0b:"4,6",
        0x0c:"2,1",0x0d:"2,2",0x0e:"2,3",0x0f:"2,4",0x10:"2,6",0x11:"2,5",
        0x12:"1,1",0x13:"1,2",0x14:"1,3",0x15:"1,4",0x16:"1,6",0x17:"1,5",
        0x18:"1,12",0x19:"1,9",0x1a:"1,7",0x1b:"1,11",0x1c:"1,8",0x1d:"1,10",
        0x1e:"2,12",0x1f:"2,9",0x20:"2,7",0x21:"2,11",0x22:"2,8",0x23:"2,10",
        0x24:"3,13",0x25:"3,9",0x26:"3,7",0x27:"3,11",0x28:"3,8",0x29:"3,10",
        0x2a:"2,13",0x2b:"4,9",0x2c:"4,11",0x2d:"4,7",0x2e:"4,8",0x2f:"4,10",
        0x30:"2,0",0x31:"5,5",0x32:"1,0",0x33:"1,13",0x35:"6,14",
        0x36:"5,1",0x37:"5,1",0x38:"4,0",0x39:"3,0",0x3a:"5,2",0x3b:"5,0",
        0x3c:"4,13",0x3d:"5,9",0x3e:"5,12",0x3f:"7,14",
        0x60:"0,5",0x61:"0,6",0x62:"0,7",0x63:"0,3",0x64:"0,8",0x65:"0,9",
        0x67:"0,11",0x69:"0,13",0x6d:"0,10",0x6f:"0,12",0x72:"1,14",
        0x73:"1,15",0x74:"3,14",0x75:"2,14",0x76:"0,4",0x77:"2,15",
        0x78:"0,2",0x79:"3,15",0x7a:"0,1",0x7b:"5,13",0x7c:"5,15",0x7d:"5,14",0x7e:"4,14"
    ]
    private static let modifiers: [UInt16:(NSEvent.ModifierFlags,UInt,UInt)] = [
        0x37:(.command,0x08,0x18),0x36:(.command,0x10,0x18),
        0x38:(.shift,0x02,0x06),0x3c:(.shift,0x04,0x06),
        0x3a:(.option,0x20,0x60),0x3d:(.option,0x40,0x60),
        0x3b:(.control,0x01,0x2001),0x3e:(.control,0x2000,0x2001),0x3f:(.function,0,0)
    ]
    mutating func releaseAll() { suppressed.formUnion(held.keys); held.removeAll() }
    private func position(_ code:UInt16,rightCommandIsFn:Bool) -> String? {
        code == 0x36 && rightCommandIsFn ? "7,14" : Self.positions[code]
    }
    private mutating func reconcileModifiers(_ flags:NSEvent.ModifierFlags,rightCommandIsFn:Bool) {
        // Also honor flags on ordinary key events: a modifier may already be held
        // when capture starts, and synthesized AppKit input may omit flagsChanged.
        for (flag,codes) in [(NSEvent.ModifierFlags.shift,[UInt16(0x38),0x3c]),(.command,[0x37,0x36]),(.option,[0x3a,0x3d]),(.control,[0x3b,0x3e])] {
            if !flags.contains(flag) {
                for code in codes { held.removeValue(forKey:code); suppressed.remove(code) }
            } else if let first = Self.modifiers[codes[0]], flags.rawValue & first.2 != 0 {
                for code in codes {
                    if flags.rawValue & Self.modifiers[code]!.1 == 0 { held.removeValue(forKey:code); suppressed.remove(code) }
                    else if !suppressed.contains(code) { held[code] = position(code,rightCommandIsFn:rightCommandIsFn) }
                }
            } else if !codes.contains(where:{held[$0] != nil || suppressed.contains($0)}) {
                held[codes[0]] = position(codes[0],rightCommandIsFn:rightCommandIsFn)
            }
        }
    }
    @discardableResult mutating func consume(_ event:NSEvent,rightCommandIsFn:Bool) -> Bool {
        let code = event.keyCode
        guard let position = position(code,rightCommandIsFn:rightCommandIsFn) else { return false }
        let previous = matrixKeys
        let down:Bool
        switch event.type {
        case .keyDown:
            reconcileModifiers(event.modifierFlags,rightCommandIsFn:rightCommandIsFn)
            guard !event.isARepeat else { return matrixKeys != previous }; down = true
        case .keyUp:
            reconcileModifiers(event.modifierFlags,rightCommandIsFn:rightCommandIsFn); down = false
        case .flagsChanged:
            guard let (flag,sideMask,bothMask) = Self.modifiers[code] else { return false }
            let raw = event.modifierFlags.rawValue
            if !event.modifierFlags.contains(flag) { down = false }
            else if sideMask == 0 { down = true }
            else if raw & bothMask != 0 { down = raw & sideMask != 0 }
            else { down = held[code] == nil && !suppressed.contains(code) }
        default: return false
        }
        if down {
            guard !suppressed.contains(code) else { return matrixKeys != previous }
            held[code] = position
        } else { held.removeValue(forKey:code); suppressed.remove(code) }
        return matrixKeys != previous
    }
}

struct PhysicalInputFocus: NSViewRepresentable {
    @ObservedObject var lab: LabModel
    func makeNSView(context:Context) -> KeyboardInputFocusView {
        let view = KeyboardInputFocusView(); view.lab = lab; return view
    }
    func updateNSView(_ view:KeyboardInputFocusView,context:Context) { view.synchronizeFocus() }
    static func dismantleNSView(_ view:KeyboardInputFocusView,coordinator:()) { view.detach() }
}

// Local events only: no global keyboard hook, HID access, or Input Monitoring permission.
@MainActor final class KeyboardInputFocusView: NSView {
    weak var lab: LabModel?
    private var monitor: Any?
    private var observers: [NSObjectProtocol] = []
    override var acceptsFirstResponder:Bool { true }
    override func isAccessibilityElement() -> Bool { false }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil { detach(); return }
        if monitor == nil {
            monitor = NSEvent.addLocalMonitorForEvents(matching:[.keyDown,.keyUp,.flagsChanged]) { [weak self] event in
                guard let self else { return event }
                return self.route(event)
            }
            for name in [NSWindow.didResignKeyNotification, NSWindow.willBeginSheetNotification, NSApplication.didResignActiveNotification] {
                observers.append(NotificationCenter.default.addObserver(forName:name,object:nil,queue:.main) { [weak self] note in
                    MainActor.assumeIsolated {
                        guard let self else { return }
                        if name == NSApplication.didResignActiveNotification || (note.object as? NSWindow) === self.window {
                            self.lab?.setKeyboardCapture(false)
                        }
                    }
                })
            }
        }
        synchronizeFocus()
    }
    func synchronizeFocus() {
        guard let window else { return }
        if lab?.keyboardCaptureActive == true && window.firstResponder !== self {
            window.makeFirstResponder(self)
        } else if lab?.keyboardCaptureActive == false && window.firstResponder === self {
            window.makeFirstResponder(nil)
        }
    }
    override func resignFirstResponder() -> Bool { lab?.setKeyboardCapture(false); return true }
    // Public to the AppKit integration harness; called directly by the shipping local monitor.
    func route(_ event:NSEvent) -> NSEvent? {
        guard let window, window.isKeyWindow, window.firstResponder === self,
              window.attachedSheet == nil, NSApp.isActive, lab?.keyboardCaptureActive == true else { return event }
        lab?.handlePhysicalEvent(event)
        return nil
    }
    func detach() {
        if let monitor { NSEvent.removeMonitor(monitor); self.monitor = nil }
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers.removeAll(); lab?.setKeyboardCapture(false)
    }
}
