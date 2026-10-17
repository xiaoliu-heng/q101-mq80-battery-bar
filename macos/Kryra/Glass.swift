import AppKit
import SwiftUI

struct PanelBackdrop: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    var body: some View {
        ZStack {
            if reduceTransparency { Color(nsColor:.windowBackgroundColor) }
            else {
                WindowMaterial()
                (scheme == .dark ? Color.black : Color.white).opacity(scheme == .dark ? 0.15 : 0.28)
            }
            LinearGradient(colors:[Color.mint.opacity(scheme == .dark ? 0.07 : 0.035),.clear,Color.pink.opacity(0.025)],
                           startPoint:.topLeading,endPoint:.bottomTrailing)
        }
        .overlay(RoundedRectangle(cornerRadius:24,style:.continuous)
            .strokeBorder(.white.opacity(scheme == .dark ? 0.12 : 0.55),lineWidth:1))
    }
}

private struct WindowMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .popover; view.blendingMode = .behindWindow; view.state = .active
        return view
    }
    func updateNSView(_ view: NSVisualEffectView, context: Context) {}
}

/// One native container batches the glass controls without merging neighbours.
struct GlassControlGroup<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    @ViewBuilder var body: some View {
        #if KRYRA_GLASS_SDK
        if #available(macOS 26.0, *) { GlassEffectContainer(spacing:8) { content } }
        else { content }
        #else
        content
        #endif
    }
}

struct CapsuleSurface: ViewModifier {
    let prominent: Bool
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @ViewBuilder func body(content: Content) -> some View {
        #if KRYRA_GLASS_SDK
        if #available(macOS 26.0, *), !reduceTransparency {
            content.glassEffect(prominent ? .regular.tint(.mint.opacity(0.25)).interactive() : .regular.interactive(),in:.capsule)
        } else { standard(content) }
        #else
        standard(content)
        #endif
    }
    private func standard(_ content: Content) -> some View {
        content
            .background {
                if reduceTransparency { Capsule().fill(Color(nsColor:.controlBackgroundColor)) }
                else { Capsule().fill(.regularMaterial) }
                if prominent { Capsule().fill(Color.mint.opacity(0.14)) }
            }
            .overlay(Capsule().strokeBorder(LinearGradient(colors:[.white.opacity(scheme == .dark ? 0.3 : 0.9),
                                                                         .white.opacity(0.06),.white.opacity(0.2)],
                                                               startPoint:.topLeading,endPoint:.bottomTrailing),lineWidth:1))
            .shadow(color:.black.opacity(scheme == .dark ? 0.12 : 0.045),radius:5,y:2)
    }
}

struct GlassButtonStyle: ButtonStyle {
    var prominent = false
    @Environment(\.isEnabled) private var enabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(prominent ? Color.primary : Color.primary.opacity(0.8))
            .modifier(CapsuleSurface(prominent:prominent))
            .opacity(enabled ? 1 : 0.4)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration:0.12),value:configuration.isPressed)
    }
}

/// Nine firmware samples are interpolated through one diffuser, with no gaps.
struct LightStripPreview: View {
    let samples: [RGB]
    let intensity: Double
    @Environment(\.colorScheme) private var scheme
    private var gradient: LinearGradient {
        let colors = samples.isEmpty ? [Color.clear] : samples.map { Color(red:Double($0.r)/255,green:Double($0.g)/255,blue:Double($0.b)/255) }
        return LinearGradient(colors:colors,startPoint:.top,endPoint:.bottom)
    }
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius:25,style:.continuous)
                .fill(LinearGradient(colors:scheme == .dark ? [Color(white:0.22),Color(white:0.12)] : [Color(white:0.98),Color(white:0.86)],startPoint:.leading,endPoint:.trailing))
                .overlay(RoundedRectangle(cornerRadius:25,style:.continuous).strokeBorder(.white.opacity(scheme == .dark ? 0.13 : 0.9),lineWidth:1))
                .shadow(color:.black.opacity(scheme == .dark ? 0.28 : 0.12),radius:9,x:3,y:5)
                .frame(width:58,height:218)
            Capsule().fill(.black.opacity(scheme == .dark ? 0.5 : 0.13)).frame(width:22,height:192)
                .overlay(Capsule().strokeBorder(.black.opacity(0.1),lineWidth:1))
            Capsule().fill(gradient).frame(width:26,height:192).blur(radius:9)
                .opacity(intensity > 0 ? 0.32 + intensity*0.28 : 0)
            Capsule().fill(gradient).frame(width:15,height:184)
                .opacity(intensity > 0 ? 0.35 + intensity*0.65 : 0)
                .overlay(Capsule().strokeBorder(.white.opacity(0.55),lineWidth:0.75))
            Capsule().fill(LinearGradient(colors:[.white.opacity(0.48),.clear,.white.opacity(0.1)],startPoint:.leading,endPoint:.trailing))
                .frame(width:5,height:175).offset(x:-3)
        }
        .frame(width:88,height:234)
        .accessibilityElement(children:.ignore)
        .accessibilityLabel("连续渐变竖向灯带预览")
    }
}
