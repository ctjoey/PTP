import SwiftUI

@main
struct PickThePlayApp: App {
    @StateObject private var state = AppState()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .preferredColorScheme(.dark)
                .tint(Theme.accent)
        }
        // iOS closes sockets in the background; reconnect (and get a fresh snapshot) on return.
        .onChange(of: phase, initial: true) { _, newPhase in
            switch newPhase {
            case .active:
                state.connect()
                Task { await state.refreshLounges() }
            case .background:
                state.disconnect()
            default:
                break
            }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var state: AppState

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
                        .tabItem { Label("Lounges", systemImage: "person.3.fill") }
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
        .overlay(alignment: .top) { NoticeBanner() }
        .fullScreenCover(isPresented: $state.showPractice) { PracticeView() }
    }
}

/// Transient message at the top of the screen (errors, "account deleted", ...).
struct NoticeBanner: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        if let notice = state.notice {
            Text(notice.text)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(notice.isError ? Color(hex: "#FFD6DA") : Theme.text)
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
                    try? await Task.sleep(nanoseconds: 3_500_000_000)
                    if state.notice?.id == notice.id { withAnimation { state.notice = nil } }
                }
                .accessibilityAddTraits(.isStaticText)
        }
    }
}
