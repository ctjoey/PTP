import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState
    @State private var serverText = ""
    @State private var serverError: String?
    @State private var confirmDelete = false
    @State private var deleting = false

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

                Section("How scoring works") {
                    LabeledContent("Correct play type", value: "+10")
                    LabeledContent("Correct direction", value: "+10")
                    LabeledContent("Exact match (both)", value: "+30")
                    Button("Practice mode") { state.showPractice = true }
                }

                if let server = state.server {
                    Section("About") {
                        Link("Privacy Policy", destination: server.appendingPathComponent("privacy"))
                        Link("Support", destination: server.appendingPathComponent("support"))
                        LabeledContent("Version", value: Self.version)
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
