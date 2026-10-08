import SwiftUI

struct LoungesView: View {
    @EnvironmentObject var state: AppState
    @State private var joinCode = ""
    @State private var newName = ""
    @State private var working = false
    @State private var error: String?
    @State private var created: Lounge?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Play the same live game against your friends in a private room with its own leaderboard.")
                        .font(.system(size: 14)).foregroundStyle(Theme.muted)

                    if !state.lounges.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Your lounges").kicker()
                            ForEach(state.lounges) { lounge in loungeRow(lounge) }
                            if state.activeLoungeID != nil {
                                Button("Hide lounge leaderboard") { state.activate(lounge: nil) }
                                    .font(.system(size: 13, weight: .semibold)).tint(Theme.muted)
                            }
                        }
                        .card()
                        howTo("Tap a lounge to show its leaderboard. Tap the share arrow to invite more friends.")
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Join with a 4-digit code").kicker()
                        HStack(spacing: 8) {
                            TextField("0000", text: $joinCode)
                                .keyboardType(.numberPad)
                                .font(.system(size: 28, weight: .black).monospacedDigit())
                                .multilineTextAlignment(.center)
                                .padding(.vertical, 8)
                                .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
                                .onChange(of: joinCode) { _, value in
                                    let digits = String(value.filter(\.isNumber).prefix(4))
                                    if digits != value { joinCode = digits }
                                }
                            Button("Join") { Task { await join() } }
                                .buttonStyle(.borderedProminent).tint(Theme.accent).foregroundStyle(Theme.accentInk)
                                .disabled(joinCode.count != 4 || working)
                        }
                    }
                    .card()
                    howTo("Got a code from a friend? Type it in and tap Join. Your group's leaderboard opens.")

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Create a lounge").kicker()
                        HStack(spacing: 8) {
                            TextField("e.g. Sunday Crew", text: $newName)
                                .textInputAutocapitalization(.words)
                                .padding(12)
                                .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
                            Button("Create") { Task { await create() } }
                                .buttonStyle(.borderedProminent).tint(Theme.accent).foregroundStyle(Theme.accentInk)
                                .disabled(newName.trimmingCharacters(in: .whitespaces).count < 2 || working)
                        }
                        if let created {
                            VStack(spacing: 8) {
                                Text("Share this code with friends").font(.system(size: 12)).foregroundStyle(Theme.muted)
                                Text(created.id).font(.system(size: 44, weight: .black).monospacedDigit())
                                    .tracking(10).foregroundStyle(Theme.accent)
                                ShareLink(item: state.inviteText(for: created)) {
                                    Label("Invite friends", systemImage: "square.and.arrow.up")
                                }
                                .buttonStyle(.bordered)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(14)
                            .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 14))
                        }
                    }
                    .card()
                    howTo("Name your group and tap Create, then send the code to friends. They enter it above to join.")

                    if let error {
                        Text(error).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.danger)
                    }
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Head to Head Lounge")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .refreshable { await state.refreshLounges() }
            .task { await state.refreshLounges() }
        }
    }

    /// One short line of instructions under a box.
    private func howTo(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 13))
            .foregroundStyle(Theme.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 6)
            .padding(.top, -8)
    }

    private func loungeRow(_ lounge: Lounge) -> some View {
        let active = lounge.id == state.activeLoungeID
        return HStack(spacing: 10) {
            Button {
                state.activate(lounge: lounge.id)
            } label: {
                HStack {
                    Image(systemName: active ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(active ? Theme.accent : Theme.dim)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(lounge.name).font(.system(size: 16, weight: .bold)).foregroundStyle(Theme.text)
                        Text("Code \(lounge.id) · \(lounge.memberCount) \(lounge.memberCount == 1 ? "player" : "players")")
                            .font(.system(size: 12)).foregroundStyle(Theme.muted)
                    }
                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(lounge.name), code \(lounge.id)\(active ? ", showing" : "")")
            ShareLink(item: state.inviteText(for: lounge)) {
                Image(systemName: "square.and.arrow.up").foregroundStyle(Theme.muted)
            }
            .accessibilityLabel("Invite to \(lounge.name)")
        }
        .padding(10)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
    }

    private func join() async {
        working = true
        error = nil
        defer { working = false }
        do {
            _ = try await state.joinLounge(code: joinCode)
            joinCode = ""
            state.tab = .board
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func create() async {
        working = true
        error = nil
        defer { working = false }
        do {
            created = try await state.createLounge(name: newName.trimmingCharacters(in: .whitespaces))
            newName = ""
        } catch {
            self.error = error.localizedDescription
        }
    }
}
