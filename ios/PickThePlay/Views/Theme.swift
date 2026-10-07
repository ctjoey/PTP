import SwiftUI

import UIKit

/// The player's choice of look, kept in UserDefaults (and settable at launch with `-ptp-appearance light`).
/// Dark is the default; "Match iPhone" follows the system setting.
enum Appearance: String, CaseIterable, Identifiable {
    case dark, light, system

    static let storageKey = "ptp-appearance"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dark: return "Dark"
        case .light: return "Light"
        case .system: return "Match iPhone"
        }
    }

    /// nil lets the system decide.
    var colorScheme: ColorScheme? {
        switch self {
        case .dark: return .dark
        case .light: return .light
        case .system: return nil
        }
    }
}

/// The web app's palette (static/css/style.css), dark and light, so both clients look like one product.
/// Every colour adapts to the current appearance, so no view needs to know which one is showing.
enum Theme {
    static let bg = adaptive(dark: "#070B12", light: "#F2F5FA")
    static let surface = adaptive(dark: "#101826", light: "#FFFFFF")
    static let surface2 = adaptive(dark: "#162133", light: "#EEF2F8")
    static let surface3 = adaptive(dark: "#1D2A40", light: "#E0E7F1")
    static let border = adaptive(dark: "#24334C", light: "#C5CFDE")
    static let text = adaptive(dark: "#EEF3FB", light: "#0F1A2B")
    static let muted = adaptive(dark: "#8D9BB4", light: "#475569")
    static let dim = adaptive(dark: "#5F6D86", light: "#5B697E")
    static let accent = adaptive(dark: "#2FD98B", light: "#0A6E41")
    static let accentInk = adaptive(dark: "#03140B", light: "#FFFFFF")
    static let warn = adaptive(dark: "#FFB547", light: "#A85800")
    static let danger = adaptive(dark: "#FF5D6C", light: "#C0142E")
    static let gold = adaptive(dark: "#FFD25E", light: "#8A6200")
    static let blue = adaptive(dark: "#4EA4FF", light: "#1B5BC9")
    /// Error text in the top banner.
    static let noticeError = adaptive(dark: "#FFD6DA", light: "#8F1021")
    /// The grass in the pass diagram.
    static let field = adaptive(dark: "#0E2A1E", light: "#E3F2E8")

    /// A colour that switches with the appearance (anything that isn't explicitly light is treated as dark).
    private static func adaptive(dark: String, light: String) -> Color {
        Color(uiColor: UIColor { traits in
            let rgb = Color.rgb(traits.userInterfaceStyle == .light ? light : dark)
            return UIColor(red: rgb.r, green: rgb.g, blue: rgb.b, alpha: 1)
        })
    }

    /// Usable height (inside the safe area, between the bars) below which the pick and result screens
    /// go compact. iPhone SE (667 pt tall) has about 554 pt under the tab bar; every other iPhone has more.
    static let compactBelow: CGFloat = 620

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

/// Tighter pick and result layouts for short screens (iPhone SE: 375×667 pt), set by the screens that
/// measure their height. Everything else gets the roomier default.
private struct CompactLayoutKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var compactLayout: Bool {
        get { self[CompactLayoutKey.self] }
        set { self[CompactLayoutKey.self] = newValue }
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
