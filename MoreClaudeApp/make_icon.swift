// make_icon.swift — draws the More Claude app icon at build time, so the
// repo doesn't have to carry a binary .icns and the icon can be tweaked by
// editing code. Run by build_app.sh:
//     swiftc make_icon.swift -o make_icon && ./make_icon out.png

import AppKit

let outputPath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let side: CGFloat = 1024

// macOS icons sit in a rounded square inset from the canvas edge.
let inset: CGFloat = side * 0.09
let plate = NSRect(x: inset, y: inset, width: side - inset * 2, height: side - inset * 2)
let corner = plate.width * 0.2237  // Apple's "squircle" corner ratio

let image = NSImage(size: NSSize(width: side, height: side))
image.lockFocus()

// Warm gradient plate.
let gradient = NSGradient(
    colors: [
        NSColor(srgbRed: 0.98, green: 0.55, blue: 0.33, alpha: 1),
        NSColor(srgbRed: 0.85, green: 0.32, blue: 0.24, alpha: 1),
    ]
)
let plateShape = NSBezierPath(roundedRect: plate, xRadius: corner, yRadius: corner)
gradient?.draw(in: plateShape, angle: -90)

// Two overlapping rounded squares: "more than one Claude".
func card(_ rect: NSRect, fill: NSColor, shadow: Bool) {
    NSGraphicsContext.saveGraphicsState()
    if shadow {
        let dropShadow = NSShadow()
        dropShadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
        dropShadow.shadowBlurRadius = side * 0.035
        dropShadow.shadowOffset = NSSize(width: 0, height: -side * 0.012)
        dropShadow.set()
    }
    fill.set()
    NSBezierPath(roundedRect: rect, xRadius: rect.width * 0.235, yRadius: rect.width * 0.235).fill()
    NSGraphicsContext.restoreGraphicsState()
}

let cardSide = plate.width * 0.44
let offset = cardSide * 0.30

card(NSRect(x: plate.midX - cardSide / 2 - offset,
            y: plate.midY - cardSide / 2 + offset,
            width: cardSide, height: cardSide),
     fill: NSColor.white.withAlphaComponent(0.55), shadow: false)

card(NSRect(x: plate.midX - cardSide / 2 + offset,
            y: plate.midY - cardSide / 2 - offset,
            width: cardSide, height: cardSide),
     fill: .white, shadow: true)

image.unlockFocus()

guard
    let tiff = image.tiffRepresentation,
    let bitmap = NSBitmapImageRep(data: tiff),
    let png = bitmap.representation(using: .png, properties: [:])
else {
    FileHandle.standardError.write(Data("make_icon: could not render the icon\n".utf8))
    exit(1)
}

do {
    try png.write(to: URL(fileURLWithPath: outputPath))
    print("wrote \(outputPath)")
} catch {
    FileHandle.standardError.write(Data("make_icon: \(error.localizedDescription)\n".utf8))
    exit(1)
}
