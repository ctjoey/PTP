import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @State private var serverText = ""
    @State private var serverError: String?
    @State private var confirmDelete = false
    @State private var deleting = false
    @AppStorage(Appearance.storageKey) private var appearance = Appearance.dark
    /// The game server box is for testing (a game on a computer on your Wi-Fi), so players don't see it
    /// unless they press and hold the version number.
    @State private var showServerSettings = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Account") {
                    LabeledContent("Username", value: state.username ?? "—")
                    if let me = state.snapshot?.me {
                        LabeledContent("Season points", value: "\(me.totalScore)")
                    }
                }

                Section {
                    Picker("Look", selection: $appearance) {
                        ForEach(Appearance.allCases) { option in Text(option.title).tag(option) }
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Appearance")
                } footer: {
                    Text("Dark is easiest on the eyes in the evening; Light is easier to read in bright sun. \"Match iPhone\" follows your phone's setting.")
                }

                if showsServerSection {
                    Section {
                        TextField("pick-the-play.onrender.com", text: $serverText)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .onSubmit(saveServer)
                        Button("Save server address", action: saveServer)
                        if ServerConfig.bundled != nil {
                            Button("Use the built-in server") {
                                serverText = ""
                                saveServer()
                            }
                        }
                        if let serverError {
                            Text(serverError).foregroundStyle(Theme.danger).font(.footnote)
                        }
                    } header: {
                        Text("Game server")
                    } footer: {
                        Text("Current: \(state.server?.absoluteString ?? "not set"). To join a game running on a computer on your Wi-Fi, enter the address it prints, like 192.168.1.20:8000.")
                    }
                }

                Section {
                    let scoring = state.snapshot?.scoring ?? .standard
                    LabeledContent("Play type right (Run / Pass)", value: "+\(scoring.type)")
                    LabeledContent("Direction right (Left / Middle / Right)", value: "+\(scoring.direction)")
                    LabeledContent("Distance right (Short / Medium / Long)", value: "+\(scoring.yardage)")
                    LabeledContent("Bonus: all three right", value: "+\(scoring.bonus)")
                    LabeledContent("Perfect call", value: "\(scoring.exact)")
                    Button("Rules of the Game") { state.tab = .rules }
                    Button("Practice mode") { state.showPractice = true }
                } header: {
                    Text("How scoring works")
                } footer: {
                    Text("Directions are as the QB looks downfield. Distance is total yards gained on the play: Short 0–5 yds (an incomplete pass is 0), Medium 6–10, Long 11+. A loss of yards scores no distance points, and so no bonus.")
                }

                if let server = state.server {
                    Section("About") {
                        Link("Privacy Policy", destination: server.appendingPathComponent("privacy"))
                        Link("Support", destination: server.appendingPathComponent("support"))
                        LabeledContent("Version", value: Self.version)
                            .contentShape(Rectangle())
                            .onLongPressGesture(minimumDuration: 1.5) {
                                withAnimation { showServerSettings = true }
                            }
                    }
                }

                if state.isSignedIn {
                    Section {
                        Button(role: .destructive) {
                            confirmDelete = true
                        } label: {
                            if deleting { ProgressView() } else { Text("Delete account") }
                        }
                        .disabled(deleting)
                    } footer: {
                        Text("Permanently deletes your username, picks, scores and the lounges you host from the game server.")
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Settings")
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .onAppear { serverText = state.server?.absoluteString ?? "" }
            .confirmationDialog("Delete your account?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete account", role: .destructive) { Task { await deleteAccount() } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This can't be undone. Your username, picks and scores will be removed.")
            }
        }
    }

    /// Shown when asked for, and whenever the built-in server isn't the one in use (or there is none),
    /// so a changed address can always be seen and put back.
    private var showsServerSection: Bool {
        showServerSettings || ServerConfig.bundled == nil || state.server != ServerConfig.bundled
    }

    private func saveServer() {
        serverError = nil
        if state.setServer(serverText) {
            serverText = state.server?.absoluteString ?? ""
            state.connect()
        } else {
            serverError = "That doesn't look like a web address."
        }
    }

    private func deleteAccount() async {
        deleting = true
        defer { deleting = false }
        do {
            try await state.deleteAccount()
        } catch {
            state.notice = AppState.Notice(text: error.localizedDescription, isError: true)
        }
    }

    private static var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = info?["CFBundleVersion"] as? String ?? "1"
        return "\(short) (\(build))"
    }
}
