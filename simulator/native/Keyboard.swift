import AppKit
import SwiftUI

struct KeyboardSurface: NSViewRepresentable {
    let keys: [KeySpec]
    let main: [Pixel]
    let side: [Pixel]
    let selected: Set<String>
    let lighting: Bool
    let coordinates: Bool
    let onKey: (String) -> Void
    func makeNSView(context: Context) -> NativeKeyboardView { NativeKeyboardView() }
    func updateNSView(_ view: NativeKeyboardView, context: Context) {
        let geometryChanged = view.keys != keys
        let changed = geometryChanged || view.main != main || view.side != side || view.selected != selected || view.lighting != lighting || view.coordinates != coordinates
        view.keys = keys; view.main = main; view.side = side; view.selected = selected
        view.lighting = lighting; view.coordinates = coordinates; view.onKey = onKey
        if geometryChanged { view.rebuildAccessibility() }
        if changed { view.needsDisplay = true }
    }
}

final class NativeKeyboardView: NSView {
    var keys: [KeySpec] = []
    var main: [Pixel] = []
    var side: [Pixel] = []
    var selected: Set<String> = []
    var lighting = true
    var coordinates = false
    var onKey: ((String) -> Void)?
    private var pressed: String?
    private var keyButtons: [NSButton] = []
    override var isFlipped: Bool { true }
    override var intrinsicContentSize: NSSize { NSSize(width: 850, height: 315) }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { "Q101 虚拟键盘；主灯位置为推导映射" }
    override func accessibilityChildren() -> [Any]? { keyButtons }

    private func rect(_ key: KeySpec) -> NSRect {
        let width = bounds.width-42, height = bounds.height-28
        return NSRect(x:32+key.x/16.25*width, y:14+key.y/6*height,
                      width:key.w/16.25*width-4, height:height/6-5)
    }
    override func layout() { super.layout(); updateAccessibilityFrames() }
    func rebuildAccessibility() {
        keyButtons.forEach { $0.removeFromSuperview() }
        keyButtons = keys.enumerated().map { index,key in
            let button = NSButton(title:key.label,target:self,action:#selector(keyPressed(_:)))
            button.isTransparent = true; button.isBordered = false; button.tag = index
            button.setAccessibilityLabel("\(key.label)，矩阵 \(key.id)")
            button.toolTip = "\(key.label) · 矩阵 \(key.id)"
            addSubview(button)
            return button
        }
        updateAccessibilityFrames()
    }
    private func updateAccessibilityFrames() {
        for (index, key) in keys.enumerated() where index < keyButtons.count {
            keyButtons[index].frame = rect(key)
        }
    }
    @objc private func keyPressed(_ sender:NSButton) {
        guard sender.tag < keys.count else { return }
        activate(keys[sender.tag].id)
    }
    override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); updateAccessibilityFrames() }
    override func resetCursorRects() { for key in keys { addCursorRect(rect(key),cursor:.pointingHand) } }
    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow,from:nil)
        if let key = keys.first(where: { rect($0).contains(point) }) { activate(key.id) }
    }
    private func activate(_ id: String) {
        pressed = id; needsDisplay = true; onKey?(id)
        DispatchQueue.main.asyncAfter(deadline:.now()+0.12) { [weak self] in
            if self?.pressed == id { self?.pressed = nil; self?.needsDisplay = true }
        }
    }
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let plate = NSBezierPath(roundedRect:bounds.insetBy(dx:1,dy:1),xRadius:14,yRadius:14)
        NSColor(calibratedWhite:0.73,alpha:0.20).setFill(); plate.fill()
        NSColor.separatorColor.withAlphaComponent(0.45).setStroke(); plate.lineWidth = 1; plate.stroke()
        for (index,key) in keys.enumerated() {
            let area = rect(key)
            let orange = ["Esc","Caps","Backspace","Space"].contains(key.label)
            let pale = key.y == 0 || key.col == 15 || key.label.count > 4 || (key.y == 5 && [0,9,12].contains(key.col))
            let base = orange ? NSColor(srgbRed:0.97,green:0.40,blue:0.10,alpha:1) : pale ? NSColor(srgbRed:0.91,green:0.92,blue:0.90,alpha:1) : NSColor(srgbRed:0.17,green:0.28,blue:0.52,alpha:1)
            let selectedKey = selected.contains(key.id) || pressed == key.id
            let path = NSBezierPath(roundedRect:area,xRadius:5,yRadius:5)
            NSGraphicsContext.saveGraphicsState()
            let shadow = NSShadow(); shadow.shadowOffset = NSSize(width:0,height:2); shadow.shadowBlurRadius = 1
            shadow.shadowColor = NSColor.black.withAlphaComponent(0.20); shadow.set()
            (selectedKey ? NSColor.controlAccentColor : base).setFill(); path.fill()
            NSGraphicsContext.restoreGraphicsState()
            if lighting, index < main.count, main[index].isLit {
                let pixel = main[index]
                pixel.nsColor.withAlphaComponent(0.25).setFill(); path.fill()
                pixel.nsColor.setStroke(); path.lineWidth = 2; path.stroke()
                let strip = NSBezierPath(roundedRect:NSRect(x:area.minX+4,y:area.maxY-4,width:area.width-8,height:2),xRadius:1,yRadius:1)
                pixel.nsColor.setFill(); strip.fill()
            }
            if selectedKey { NSColor.controlAccentColor.setStroke(); path.lineWidth = 2; path.stroke() }
            var label = key.label
            let names = ["Command":"Cmd","Command R":"Cmd R","Option":"Opt","Option R":"Opt R","Backspace":"Back","Space":"SPACE"]
            if let shorter = names[label] { label = shorter }
            let font = NSFont.systemFont(ofSize:area.width < 34 ? 10 : 11.5,weight:.medium)
            let ink: NSColor = selectedKey || orange || !pale ? .white : NSColor(srgbRed:0.17,green:0.21,blue:0.28,alpha:1)
            let attr: [NSAttributedString.Key:Any] = [.font:font,.foregroundColor:ink]
            let size = (label as NSString).size(withAttributes:attr)
            (label as NSString).draw(at:NSPoint(x:area.midX-size.width/2,y:area.midY-size.height/2-(coordinates ? 4 : 0)),withAttributes:attr)
            if coordinates {
                let a: [NSAttributedString.Key:Any] = [.font:NSFont.monospacedSystemFont(ofSize:8,weight:.regular),.foregroundColor:ink.withAlphaComponent(0.8)]
                let s = (key.id as NSString).size(withAttributes:a)
                (key.id as NSString).draw(at:NSPoint(x:area.midX-s.width/2,y:area.maxY-12),withAttributes:a)
            }
        }
        let strip = NSRect(x:9,y:bounds.height*0.37,width:7,height:bounds.height*0.32)
        NSColor(calibratedWhite:0.10,alpha:1).setFill(); NSBezierPath(roundedRect:strip.insetBy(dx:-1,dy:-1),xRadius:4,yRadius:4).fill()
        for i in 0..<9 {
            let h = strip.height/9
            let slot = NSRect(x:strip.minX,y:strip.maxY-CGFloat(i+1)*h,width:strip.width,height:h-1)
            (i < side.count && side[i].isLit ? side[i].nsColor : NSColor(calibratedWhite:0.16,alpha:1)).setFill()
            NSBezierPath(roundedRect:slot,xRadius:1,yRadius:1).fill()
        }
        updateAccessibilityFrames()
    }
}
