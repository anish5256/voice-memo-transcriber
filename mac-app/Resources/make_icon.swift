import AppKit
let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext
// macOS icon grid: 824 pt squircle centred, soft drop shadow
let rect = NSRect(x: 100, y: 100, width: 824, height: 824)
let shape = NSBezierPath(roundedRect: rect, xRadius: 185, yRadius: 185)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: NSColor.black.withAlphaComponent(0.35).cgColor)
NSColor.black.setFill(); shape.fill()
ctx.restoreGState()
shape.addClip()
NSGradient(colors: [NSColor(red: 1.00, green: 0.42, blue: 0.36, alpha: 1), NSColor(red: 0.82, green: 0.13, blue: 0.24, alpha: 1)])!
    .draw(in: rect, angle: -90)
// subtle top highlight
NSGradient(colors: [NSColor.white.withAlphaComponent(0.18), NSColor.white.withAlphaComponent(0)])!
    .draw(in: NSRect(x: 100, y: 560, width: 824, height: 364), angle: -90)
// waveform bars (symmetric, rounded)
let heights: [CGFloat] = [70, 150, 250, 360, 250, 420, 300, 190, 330, 220, 120, 60]
let barW: CGFloat = 34, gap: CGFloat = 22
let total = CGFloat(heights.count) * barW + CGFloat(heights.count - 1) * gap
var x = (size - total) / 2
let mid: CGFloat = 600
NSColor.white.setFill()
for h in heights {
    NSBezierPath(roundedRect: NSRect(x: x, y: mid - h / 2, width: barW, height: h), xRadius: barW / 2, yRadius: barW / 2).fill()
    x += barW + gap
}
// note lines (the transcript / summary)
NSColor.white.withAlphaComponent(0.9).setFill()
for (i, w) in [CGFloat(520), 440, 300].enumerated() {
    let y = 300 - CGFloat(i) * 62
    NSBezierPath(roundedRect: NSRect(x: (size - 520) / 2, y: y, width: w, height: 30), xRadius: 15, yRadius: 15).fill()
}
image.unlockFocus()
let png = NSBitmapImageRep(data: image.tiffRepresentation!)!.representation(using: .png, properties: [:])!
try! png.write(to: URL(fileURLWithPath: "icon_1024.png"))
print("ok")
