import SwiftUI

/// First run: explain the game, set the server if the build doesn't have one, pick a username.
struct OnboardingView: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var host: HostState
    @State private var username = ""
    @State private var serverText = ""
    @State private var working = false
    @State private var error: String?
    @State private var showRules = false
    @FocusState private var focused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                BrandMark().padding(.top, 24)

                Text("Call every snap before it happens.")
                    .font(.system(size: 30, weight: .black))
                    .foregroundStyle(Theme.text)
                Text("When a play opens you have 15 seconds to make a three-part call: **Run or Pass**, **Left, Middle or Right** as the QB looks downfield, and **how far**: Short (0–5 yds), Medium (6–10) or Long (11+). Points land the moment the play is scored.")
                    .foregroundStyle(Theme.muted)

                VStack(alignment: .leading, spacing: 4) {
                    PointsTable()
                    Text("A loss of yards scores no distance points, and so no bonus.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                    Button {
                        showRules = true
                    } label: {
                        Label("Read the full rules", systemImage: "book.fill")
                            .font(.system(size: 15, weight: .bold))
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    .tint(Theme.accent)
                    .padding(.top, 8)
                }
                .card(padding: 14)

                if showsServerField {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Game server").kicker()
                        TextField("pick-the-play.onrender.com", text: $serverText)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .padding(14)
                            .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
                        Text(ServerConfig.bundled == nil ? "Ask whoever runs the game for its address."
                                                         : "Clear this to use the built-in server.")
                            .font(.footnote).foregroundStyle(Theme.muted)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Choose a username").kicker()
                    TextField("Your name", text: $username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textContentType(.nickname)
                        .focused($focused)
                        .submitLabel(.go)
                        .onSubmit { Task { await signUp() } }
                        .padding(14)
                        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(focused ? Theme.accent : Theme.border, lineWidth: 1))
                }

                if let error {
                    Text(error).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.danger)
                }
                if state.waking {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Connecting to the game server… the first time can take up to a minute.")
                    }
                    .font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.muted)
                }

                Button {
                    Task { await signUp() }
                } label: {
                    Group {
                        if working { ProgressView().tint(Theme.accentInk) } else { Text("Let's Play") }
                    }
                    .font(.system(size: 18, weight: .black))
                    .frame(maxWidth: .infinity, minHeight: 52)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .foregroundStyle(Theme.accentInk)
                .disabled(working)

                Button {
                    state.showPractice = true
                } label: {
                    Text("Just practice first").font(.system(size: 16, weight: .bold)).frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .tint(Theme.muted)

                Button {
                    host.present()
                } label: {
                    Text("Running the game? Host sign-in")
                        .font(.system(size: 15, weight: .bold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .tint(Theme.muted)

                Disclaimer().padding(.top, 8)
            }
            .padding(20)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Theme.bg.ignoresSafeArea())
        .onAppear { serverText = state.server?.absoluteString ?? "" }
        .sheet(isPresented: $showRules) { RulesView(showsDone: true) }
    }

    /// Shown when the build has no server, or a saved address replaced the built-in one (so a typo
    /// or a finished Wi-Fi game can always be corrected from here).
    private var showsServerField: Bool { ServerConfig.bundled == nil || state.server != ServerConfig.bundled }

    private func signUp() async {
        error = nil
        let name = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard name.count >= 2 else {
            error = "Pick a username with at least 2 characters."
            return
        }
        if showsServerField {
            guard state.setServer(serverText) else {
                error = "Enter the game server address."
                return
            }
        }
        working = true
        defer { working = false }
        do {
            try await state.signUp(username: name)
        } catch {
            self.error = error.localizedDescription
        }
    }
}
