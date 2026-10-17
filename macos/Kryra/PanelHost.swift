import AppKit
import SwiftUI

enum PanelMetrics {
    static let width: CGFloat = 368
    static let height: CGFloat = 584
    static var size: NSSize { NSSize(width:width,height:height) }
}

/// Neither a status message nor a SwiftUI ideal size may resize the host.
@MainActor final class FixedPanelHostingController<Content: View>: NSHostingController<Content> {
    init(content: Content) {
        super.init(rootView:content)
        sizingOptions = []
        preferredContentSize = PanelMetrics.size
        view.setFrameSize(PanelMetrics.size)
    }
    @objc required dynamic init?(coder: NSCoder) { fatalError("Use init(content:)") }
}

@MainActor func fixedPanelView<Content: View>(_ content: Content) -> NSHostingView<Content> {
    let host = NSHostingView(rootView:content)
    host.sizingOptions = []
    host.frame = NSRect(origin:.zero,size:PanelMetrics.size)
    return host
}
