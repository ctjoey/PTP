import SwiftUI

/// The web app's dark palette (static/css/style.css), so both clients look like one product.
enum Theme {
    static let bg = Color(hex: "#070B12")
    static let surface = Color(hex: "#101826")
    static let surface2 = Color(hex: "#162133")
    static let surface3 = Color(hex: "#1D2A40")
    static let border = Color(hex: "#24334C")
    static let text = Color(hex: "#EEF3FB")
    static let muted = Color(hex: "#8D9BB4")
    static let dim = Color(hex: "#5F6D86")
    static let accent = Color(hex: "#2FD98B")
    static let accentInk = Color(hex: "#03140B")
    static let warn = Color(hex: "#FFB547")
    static let danger = Color(hex: "#FF5D6C")
    static let gold = Color(hex: "#FFD25E")
    static let blue = Color(hex: "#4EA4FF")

    /// Black or white text, whichever reads better on a team colour (WCAG relative luminance).
    static func ink(on hex: String) -> Color {
        let rgb = Color.rgb(hex)
        func linear(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        let luminance = 0.2126 * linear(rgb.r) + 0.7152 * linear(rgb.g) + 0.0722 * linear(rgb.b)
        return luminance > 0.4 ? Color(hex: "#0A0F18") : .white
    }
}

extension Color {
    init(hex: String) {
        let rgb = Color.rgb(hex)
        self.init(.sRGB, red: rgb.r, green: rgb.g, blue: rgb.b, opacity: 1)
    }

    static func rgb(_ hex: String) -> (r: Double, g: Double, b: Double) {
        var text = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("#") { text.removeFirst() }
        let value = UInt64(text, radix: 16) ?? 0
        return (Double((value >> 16) & 0xFF) / 255, Double((value >> 8) & 0xFF) / 255, Double(value & 0xFF) / 255)
    }
}

struct CardStyle: ViewModifier {
    var padding: CGFloat = 16
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.border, lineWidth: 1))
    }
}

extension View {
    func card(padding: CGFloat = 16) -> some View { modifier(CardStyle(padding: padding)) }

    /// Small uppercase section heading used across screens.
    func kicker() -> some View {
        font(.system(size: 12, weight: .heavy)).tracking(1.6).foregroundStyle(Theme.muted).textCase(.uppercase)
    }
}
