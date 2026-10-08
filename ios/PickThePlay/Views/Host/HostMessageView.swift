import SwiftUI

// The host console's "Message" tab: a banner for every player's screen. Ports the "Message to Players" card of the
// website console (static/js/admin-players.js, paintMessage / send / clearBanner).
//
// The server keeps one banner at a time. Sending replaces it, sending nothing clears it, and the state the host
// console receives says what is showing (`announcement`).

struct HostMessageView: View {
    @EnvironmentObject var host: HostState
    @State private var text = ""
    /// A message is on its way to the server.
    @State private var sending = false
    @FocusState private var editing: Bool

    var body: some View {
        VStack(spacing: 14) {
            messageCard
        }
    }

    // MARK: What is typed

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var length: Int { trimmed.count }
    private var isOver: Bool { length > HostText.maxMessage }
    private var canSend: Bool { !trimmed.isEmpty && !isOver && !sending && !host.isBusy }
    private var banner: HostBanner? { host.snapshot?.announcement }

    // MARK: The card

    private var messageCard: some View {
        HostCard("Message to players", systemImage: "megaphone.fill") {
            HostHint("Shows as a banner at the top of every player's screen, in the app and on the website. It stays until they close it or you clear it. It never moves the pick buttons.")
            quickMessages
            editor
            counter
            HostButton("Send to everyone", systemImage: "paperplane.fill", kind: .primary, busy: sending) { send() }
                .disabled(!canSend)
            HostButton("Clear banner", systemImage: "xmark.circle", kind: .secondary) { clearBanner() }
                .disabled(banner == nil || sending || host.isBusy)
            status
        }
    }

    // MARK: Quick messages

    private var quickMessages: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Quick messages")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Theme.muted)
            let items = HostText.quickMessages
            VStack(spacing: 8) {
                ForEach(Array(stride(from: 0, to: items.count, by: 2)), id: \.self) { start in
                    HStack(spacing: 8) {
                        quickButton(items[start])
                        if start + 1 < items.count { quickButton(items[start + 1]) }
                    }
                }
            }
            HostHint("Tap one to fill the box. You can change the words before you send.")
        }
    }

    private func quickButton(_ item: (title: String, text: String)) -> some View {
        Button {
            text = item.text
        } label: {
            Text(item.title)
                .font(.system(size: 15, weight: .bold))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered)
        .tint(Theme.text)
        .disabled(sending)
        .accessibilityLabel("Quick message: \(item.title)")
        .accessibilityHint(item.text)
    }

    // MARK: The box and its counter

    private var editor: some View {
        ZStack(alignment: .topLeading) {
            TextEditor(text: $text)
                .font(.system(size: 17))
                .foregroundStyle(Theme.text)
                .scrollContentBackground(.hidden)
                .focused($editing)
                .frame(minHeight: 110)
                .padding(6)
                .accessibilityLabel("Message to every player")
            if text.isEmpty {
                Text("Type a short message for every player…")
                    .font(.system(size: 17))
                    .foregroundStyle(Theme.dim)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 14)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .stroke(isOver ? Theme.danger : (editing ? Theme.accent : Theme.border), lineWidth: 1))
    }

    /// "42 / 200", or "212 / 200 · 12 too many" in red when it is too long to send.
    private var counter: some View {
        let over = length - HostText.maxMessage
        let words = isOver ? "\(length) / \(HostText.maxMessage) · \(HostText.count(over, "letter")) too many"
            : "\(length) / \(HostText.maxMessage)"
        return Text(verbatim: words)
            .font(.system(size: 13, weight: .bold).monospacedDigit())
            .foregroundStyle(isOver ? Theme.danger : Theme.muted)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityLabel(isOver
                ? "\(length) of \(HostText.maxMessage) letters. \(HostText.count(over, "letter")) too many. Shorten the message to send it."
                : "\(length) of \(HostText.maxMessage) letters")
    }

    // MARK: What is showing now

    private var status: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: banner == nil ? "minus.circle" : "megaphone.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(banner == nil ? Theme.muted : Theme.accent)
                .accessibilityHidden(true)
            Text(verbatim: banner.map { "Showing now: “\($0.text)”" } ?? "No banner is showing.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(banner == nil ? Theme.muted : Theme.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    // MARK: Actions

    private func send() {
        let value = trimmed
        guard canSend else { return }
        editing = false
        sending = true
        let host = self.host
        Task { @MainActor in
            let sent = await host.announce(value)
            sending = false
            guard let sent else { return }   // the server's reason is already on screen
            if sent == 0 {
                host.show("Message set. No screens are open right now, so players will see it when they join.")
            } else {
                host.show("Message sent to \(sent) \(sent == 1 ? "screen" : "screens").")
            }
            // Empty the box, unless the host has already started on something else while it was sending.
            if trimmed == value { text = "" }
        }
    }

    private func clearBanner() {
        guard banner != nil, !host.isBusy else { return }
        editing = false
        let host = self.host
        Task { @MainActor in
            if await host.clearBanner() { host.show("Banner cleared.") }
        }
    }
}
