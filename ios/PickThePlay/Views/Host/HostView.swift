import SwiftUI
import UIKit

/// The full-screen host console: the key screen until the server accepts the key, then the four tabs.
struct HostConsoleCover: View {
    @EnvironmentObject var host: HostState

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            if host.isSignedIn {
                // The drafts belong to the app's HostState, so they survive tab switches and a closed console.
                HostConsoleView()
                    .environmentObject(host.drafts)
            } else {
                HostSignInView()
            }
        }
        .overlay(alignment: .top) {
            HostNoticeBanner()
                .animation(.easeInOut(duration: 0.25), value: host.notice)
        }
        // The phone must not lock itself in the middle of a game.
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
    }
}

// MARK: - Sign in

struct HostSignInView: View {
    @EnvironmentObject var host: HostState
    @State private var key = ""
    @State private var remember = true
    @FocusState private var focused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Button {
                        host.dismiss()
                    } label: {
                        Label("Close", systemImage: "xmark")
                            .font(.system(size: 15, weight: .bold))
                            .frame(minHeight: 44)
                    }
                    .tint(Theme.muted)
                    Spacer()
                }
                Text("Host console").kicker()
                Text("Run the game from this iPhone")
                    .font(.system(size: 28, weight: .black)).foregroundStyle(Theme.text)
                Text("For whoever is running the game. You open each play, and score it or let the live feed do it. Players don't need this.")
                    .foregroundStyle(Theme.muted)

                if host.isOtherServer {
                    wrongServer
                } else if connectingWithSavedKey {
                    connecting
                } else {
                    keyForm
                }
            }
            .padding(20)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    /// A key from last time is being used, or a key just typed is being checked.
    private var connectingWithSavedKey: Bool { host.signingIn || host.hasSavedKey || host.isConnecting }

    /// The app is pointed at some other server (Settings): the key is neither asked for nor sent.
    private var wrongServer: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(HostText.wrongServer)
                .font(.system(size: 16, weight: .bold)).foregroundStyle(Theme.warn)
                .fixedSize(horizontal: false, vertical: true)
            HostHint("Nothing was sent. Your admin key stays on this iPhone.")
        }
        .card()
        .accessibilityElement(children: .combine)
    }

    private var connecting: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                ProgressView()
                Text("Connecting to the game server… the first time can take up to a minute.")
                    .font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.muted)
            }
            .card()
            HostButton("Use a different key", systemImage: "key.fill", kind: .secondary) { host.forgetKey() }
        }
    }

    private var keyForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Admin key").kicker()
                SecureField("Admin key", text: $key)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focused)
                    .submitLabel(.go)
                    .onSubmit { signIn() }
                    .padding(14)
                    .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(focused ? Theme.accent : Theme.border, lineWidth: 1))
                Toggle("Remember it on this iPhone", isOn: $remember)
                    .font(.system(size: 15, weight: .semibold))
                    .tint(Theme.accent)
                HostHint("The person who set up the game has the key. It is kept in this iPhone's secure storage, never in the app.")
            }
            if let error = host.authError {
                Text(error).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.danger)
            }
            HostButton("Open the console", systemImage: "arrow.right.circle.fill", kind: .primary) { signIn() }
                .disabled(key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    private func signIn() {
        let entered = key
        guard !entered.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        focused = false
        Task { await host.signIn(key: entered, remember: remember) }
    }
}

// MARK: - The console

private struct HostConsoleView: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    @State private var confirmSignOut = false

    var body: some View {
        VStack(spacing: 0) {
            header
            if host.connection != .online {
                Text(host.connection == .connecting ? "Reconnecting…" : "Offline. Trying again…")
                    .font(.system(size: 13, weight: .bold)).foregroundStyle(Theme.accentInk)
                    .frame(maxWidth: .infinity, minHeight: 30)
                    .background(Theme.warn)
            }
            Picker("Section", selection: $host.tab) {
                Text("Run").tag(HostState.Tab.run)
                Text("Log").tag(HostState.Tab.log)
                Text("Players").tag(HostState.Tab.players)
                Text("Message").tag(HostState.Tab.message)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16).padding(.vertical, 10)
            ScrollView {
                Group {
                    switch host.tab {
                    case .run: HostRunView()
                    case .log: HostLogView()
                    case .players: HostPlayersView(search: drafts.playerSearch)
                    case .message: HostMessageView()
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .onAppear { if let state = host.snapshot { drafts.update(from: state) } }
        .onChange(of: host.snapshot) { _, state in if let state { drafts.update(from: state) } }
        .sheet(item: $drafts.fix) { _ in
            FixResultSheet()
                .environmentObject(host)
                .environmentObject(drafts)
        }
        .confirmationDialog("Sign out of the host console?", isPresented: $confirmSignOut, titleVisibility: .visible) {
            Button("Sign out and forget the key", role: .destructive) { host.signOut() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("You'll need the admin key to open it again.")
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Button {
                host.dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .bold))
                    .frame(width: 44, height: 44)
            }
            .tint(Theme.muted)
            .accessibilityLabel("Close the host console")
            VStack(alignment: .leading, spacing: 1) {
                Text("Host console").kicker()
                Text(host.game.map(HostText.gameLine) ?? "No game yet")
                    .font(.system(size: 17, weight: .black)).foregroundStyle(Theme.text)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
            Spacer()
            Circle()
                .fill(host.connection == .online ? Theme.accent : (host.connection == .connecting ? Theme.warn : Theme.danger))
                .frame(width: 10, height: 10)
                .accessibilityLabel(host.connection == .online ? "Connected" : "Not connected")
            Menu {
                Button("Sign out and forget the key", role: .destructive) { confirmSignOut = true }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.system(size: 20, weight: .semibold))
                    .frame(width: 44, height: 44)
            }
            .tint(Theme.muted)
            .accessibilityLabel("More")
        }
        .padding(.horizontal, 8)
    }
}
