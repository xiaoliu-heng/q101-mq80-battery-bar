import AppKit
let destination = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
for size in [16,32,64,128,256,512,1024] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:size,pixelsHigh:size,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:bitmap)
    let s=CGFloat(size)
    NSColor(calibratedRed:0.12,green:0.23,blue:0.43,alpha:1).setFill()
    NSBezierPath(roundedRect:NSRect(x:s*0.04,y:s*0.04,width:s*0.92,height:s*0.92),xRadius:s*0.2,yRadius:s*0.2).fill()
    for row in 0..<3 { for col in 0..<3 {
        (row==0 && col==0 ? NSColor(calibratedRed:0.98,green:0.47,blue:0.17,alpha:1) : NSColor(calibratedWhite:0.94,alpha:1)).setFill()
        NSBezierPath(roundedRect:NSRect(x:s*(0.25+CGFloat(col)*0.19),y:s*(0.29+CGFloat(row)*0.18),width:s*0.15,height:s*0.13),xRadius:s*0.025,yRadius:s*0.025).fill()
    } }
    for i in 0..<9 {
        (i<7 ? NSColor(calibratedRed:0.4,green:0.88,blue:0.42,alpha:1) : NSColor(calibratedWhite:0.36,alpha:1)).setFill()
        NSBezierPath(roundedRect:NSRect(x:s*0.14,y:s*(0.29+CGFloat(i)*0.055),width:s*0.04,height:s*0.043),xRadius:s*0.01,yRadius:s*0.01).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    let data=bitmap.representation(using:.png,properties:[:])!
    let names: [String]
    switch size {
    case 16:names=["icon_16x16.png"]
    case 32:names=["icon_16x16@2x.png","icon_32x32.png"]
    case 64:names=["icon_32x32@2x.png"]
    case 128:names=["icon_128x128.png"]
    case 256:names=["icon_128x128@2x.png","icon_256x256.png"]
    case 512:names=["icon_256x256@2x.png","icon_512x512.png"]
    default:names=["icon_512x512@2x.png"]
    }
    for name in names {try data.write(to:destination.appendingPathComponent(name))}
}
