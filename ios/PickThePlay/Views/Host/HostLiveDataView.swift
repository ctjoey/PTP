import SwiftUI

// PLACEHOLDER: replaced by the live-data screens.

struct HostLiveDataCard: View {
    var body: some View { EmptyView() }
}

/// "Pick today's game": the day's schedule, plus the recorded practice game. Calls `onPick` and dismisses itself.
struct GamePickerSheet: View {
    var onPick: (FeedGame) -> Void
    var body: some View { EmptyView() }
}
