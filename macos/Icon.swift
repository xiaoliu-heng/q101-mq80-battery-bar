import AppKit

let destination = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
try FileManager.default.createDirectory(at:destination,withIntermediateDirectories:true)
for size in [16,32,128,256,512] {
    for scale in [1,2] {
        let pixels = size*scale
        let image = NSImage(size:NSSize(width:pixels,height:pixels))
        image.lockFocus()
        let context = NSGraphicsContext.current!.cgContext
        context.scaleBy(x:CGFloat(pixels)/1024,y:CGFloat(pixels)/1024)
        let background = NSBezierPath(roundedRect:NSRect(x:52,y:52,width:920,height:920),xRadius:210,yRadius:210)
        NSColor(calibratedRed:0.08,green:0.14,blue:0.16,alpha:1).setFill();background.fill()
        let keyboard = NSBezierPath(roundedRect:NSRect(x:170,y:330,width:684,height:370),xRadius:65,yRadius:65)
        NSColor(calibratedRed:0.17,green:0.25,blue:0.27,alpha:1).setFill();keyboard.fill()
        for row in 0..<3 {
            for col in 0..<7 {
                let rect=NSRect(x:215+col*87,y:400+row*88,width:66,height:61)
                NSColor(calibratedWhite:0.92,alpha:row==2 ? 0.9 : 0.72).setFill()
                NSBezierPath(roundedRect:rect,xRadius:13,yRadius:13).fill()
            }
        }
        for index in 0..<9 {
            let hue=CGFloat(index)/18+0.32
            NSColor(calibratedHue:hue,saturation:0.75,brightness:0.95,alpha:1).setFill()
            NSBezierPath(roundedRect:NSRect(x:190+index*73,y:243,width:60,height:35),xRadius:11,yRadius:11).fill()
        }
        image.unlockFocus()
        guard let tiff=image.tiffRepresentation,let bitmap=NSBitmapImageRep(data:tiff),let data=bitmap.representation(using:.png,properties:[:]) else { fatalError("Icon rendering failed") }
        let suffix=scale == 2 ? "@2x" : ""
        try data.write(to:destination.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
