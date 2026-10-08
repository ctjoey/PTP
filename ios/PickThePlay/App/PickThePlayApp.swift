import SwiftUI

@main
struct PickThePlayApp: App {
    @StateObject private var state = AppState()
    @StateObject private var host = HostState(keys: KeychainKeyStore())
    @Environment(\.scenePhase) private var phase
    @AppStorage(Appearance.storageKey) private var appearance = Appearance.dark

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .environmentObject(host)
                .preferredColorScheme(appearance.colorScheme)
                .tint(Theme.accent)
        }
        // iOS closes sockets in the background; reconnect (and get a fresh snapshot) on return.
        .onChange(of: phase, initial: true) { _, newPhase in
            switch newPhase {
            case .active:
                state.connect()
                host.appBecameActive()
                // A sleeping server needs up to a minute to answer, so wake it before the first real request.
                Task {
                    await state.wakeServer()
                    await state.refreshLounges()
                }
            case .background:
                state.disconnect()
                host.appEnteredBackground()
            default:
                break
            }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var host: HostState
    @AppStorage(Appearance.storageKey) private var appearance = Appearance.dark

    var body: some View {
        Group {
            if state.isSignedIn {
                TabView(selection: $state.tab) {
                    LiveView()
                        .tabItem { Label("Live", systemImage: "football.fill") }
                        .tag(AppState.Tab.live)
                    LeaderboardView()
                        .tabItem { Label("Leaderboard", systemImage: "list.number") }
                        .tag(AppState.Tab.board)
                    LoungesView()
                        .tabItem { Label("Head to Head", systemImage: "person.3.fill") }
                        .tag(AppState.Tab.lounges)
                    RulesView()
                        .tabItem { Label("Rules", systemImage: "book.fill") }
                        .tag(AppState.Tab.rules)
                    SettingsView()
                        .tabItem { Label("Settings", systemImage: "gearshape.fill") }
                        .tag(AppState.Tab.settings)
                }
            } else {
                OnboardingView()
            }
        }
        .background(Theme.bg.ignoresSafeArea())
        .overlay(alignment: .top) {
            VStack(spacing: 8) {
                AnnouncementBanner()
                NoticeBanner()
            }
            .animation(.easeInOut(duration: 0.25), value: state.announcement)
        }
        .fullScreenCover(isPresented: $state.showPractice) { PracticeView() }
        .fullScreenCover(isPresented: $host.isPresented) {
            HostConsoleCover()
                .environmentObject(host)
                .preferredColorScheme(appearance.colorScheme)
                .tint(Theme.accent)
        }
    }
}

/// Transient message at the top of the screen (errors, "account deleted", ...).
struct NoticeBanner: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        if let notice = state.notice {
            Text(notice.text)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(notice.isError ? Theme.noticeError : Theme.text)
                .padding(.horizontal, 14).padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.surface3, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(notice.isError ? Theme.danger.opacity(0.6) : Theme.accent.opacity(0.6), lineWidth: 1))
                .padding(.horizontal, 16)
                .padding(.top, 6)
                .transition(.move(edge: .top).combined(with: .opacity))
                .onTapGesture { state.notice = nil }
                .task(id: notice.id) {
                    try? await Task.sleep(nanoseconds: UInt64(notice.seconds * 1_000_000_000))
                    if state.notice?.id == notice.id { withAnimation { state.notice = nil } }
                }
                .accessibilityAddTraits(.isStaticText)
        }
    }
}

/// The host's message to the players (the same banner as on the website): floats over the top of the screen so it never
/// moves the pick buttons, and stays until the player dismisses it or the host clears it.
struct AnnouncementBanner: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        if let message = state.announcement {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "megaphone.fill")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(Theme.warn)
                    .padding(.top, 2)
                Text(message.text)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Theme.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button {
                    withAnimation { state.dismissAnnouncement() }
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(Theme.muted)
                        .frame(width: 34, height: 34)
                }
                .accessibilityLabel("Dismiss message")
            }
            .padding(.leading, 14).padding(.trailing, 4).padding(.vertical, 8)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.warn.opacity(0.18)))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.warn.opacity(0.6), lineWidth: 1))
            .shadow(color: .black.opacity(0.25), radius: 12, y: 6)
            .padding(.horizontal, 16)
            .padding(.top, 6)
            .transition(.move(edge: .top).combined(with: .opacity))
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Message from the host: \(message.text)")
        }
    }
}
