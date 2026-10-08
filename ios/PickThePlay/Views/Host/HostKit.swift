import SwiftUI

// Small building blocks shared by every host screen, in the same look as the player app.

/// A titled card, the building block of every host screen.
struct HostCard<Content: View>: View {
    var title: String?
    var systemImage: String?
    private let content: Content

    init(_ title: String? = nil, systemImage: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title {
                HStack(spacing: 6) {
                    if let systemImage {
                        Image(systemName: systemImage)
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(Theme.muted)
                            .accessibilityHidden(true)
                    }
                    Text(title).kicker()
                }
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isHeader)
            }
            content
        }
        .card()
    }
}

/// A big, thumb-sized button. `busy` swaps the icon for a spinner (the caller also disables it).
struct HostButton: View {
    enum Kind { case primary, secondary, danger, warn }

    var title: String
    var systemImage: String?
    var kind: Kind
    var busy: Bool
    var action: () -> Void

    init(_ title: String, systemImage: String? = nil, kind: Kind = .secondary, busy: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.systemImage = systemImage
        self.kind = kind
        self.busy = busy
        self.action = action
    }

    var body: some View {
        let label = Button(action: action) {
            HStack(spacing: 8) {
                if busy {
                    ProgressView()
                } else if let systemImage {
                    Image(systemName: systemImage)
                }
                Text(title).lineLimit(1).minimumScaleFactor(0.75)
            }
            .font(.system(size: 17, weight: .heavy))
            .frame(maxWidth: .infinity, minHeight: 50)
        }
        switch kind {
        case .primary:
            label.buttonStyle(.borderedProminent).tint(Theme.accent).foregroundStyle(Theme.accentInk)
        case .danger:
            label.buttonStyle(.borderedProminent).tint(Theme.danger).foregroundStyle(Color.white)
        case .warn:
            label.buttonStyle(.borderedProminent).tint(Theme.warn).foregroundStyle(Theme.accentInk)
        case .secondary:
            label.buttonStyle(.bordered).tint(Theme.text)
        }
    }
}

/// A small coloured light for the live-data state.
struct ToneDot: View {
    var tone: FeedState.Tone

    var body: some View {
        let color: Color = {
            switch tone {
            case .good: return Theme.accent
            case .wait: return Theme.warn
            case .bad: return Theme.danger
            case .off: return Theme.dim
            }
        }()
        Circle().fill(color).frame(width: 10, height: 10)
            .overlay(Circle().stroke(color.opacity(0.3), lineWidth: 6))
            .accessibilityHidden(true)
    }
}

/// Transient message at the top of the console (errors from the server, "Message sent", ...).
struct HostNoticeBanner: View {
    @EnvironmentObject var host: HostState

    var body: some View {
        if let notice = host.notice {
            Text(notice.text)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(notice.isError ? Theme.noticeError : Theme.text)
                .padding(.horizontal, 14).padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.surface3, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(notice.isError ? Theme.danger.opacity(0.6) : Theme.accent.opacity(0.6), lineWidth: 1))
                .shadow(color: .black.opacity(0.25), radius: 12, y: 6)
                .padding(.horizontal, 16)
                .padding(.top, 6)
                .transition(.move(edge: .top).combined(with: .opacity))
                .onTapGesture { host.notice = nil }
                .task(id: notice.id) {
                    try? await Task.sleep(nanoseconds: UInt64((notice.isError ? 6.0 : 3.5) * 1_000_000_000))
                    if host.notice?.id == notice.id { withAnimation { host.notice = nil } }
                }
                .accessibilityAddTraits(.isStaticText)
        }
    }
}

/// A one-line explanation under a control.
struct HostHint: View {
    var text: String
    var color: Color = Theme.muted

    init(_ text: String, color: Color = Theme.muted) {
        self.text = text
        self.color = color
    }

    var body: some View {
        Text(text)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(color)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}
