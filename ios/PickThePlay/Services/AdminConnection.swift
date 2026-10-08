import Foundation

/// What `HostState` needs from the admin channel. The phone uses `AdminConnection`; the Linux end-to-end test
/// substitutes a stand-in that carries the same bytes (Linux's networking can't do WebSockets).
@MainActor
protocol AdminLink: AnyObject {
    var onMessage: ((AdminServerMessage) -> Void)? { get set }
    var onStatus: ((AdminConnection.Status) -> Void)? { get set }
    var onRejected: (() -> Void)? { get set }
    var status: AdminConnection.Status { get }
    func start(url: URL, key: String)
    func stop()
    @discardableResult func send(_ object: [String: Any]) -> Bool
}

/// The host console's `/ws/admin` channel. Same shape as `LiveConnection`: sends `auth` on every (re)connect,
/// decodes server messages, pings every 25 s and reconnects with backoff. A key the server refuses (close code
/// 4401) ends the connection for good, since retrying can't help.
@MainActor
final class AdminConnection: AdminLink {
    enum Status: Equatable {
        case offline, connecting, online
    }

    static let pingMessage: [String: Any] = ["type": "ping"]

    /// The first message on every connection.
    static func authMessage(key: String) -> [String: Any] { ["type": "auth", "key": key] }

    var onMessage: ((AdminServerMessage) -> Void)?
    var onStatus: ((Status) -> Void)?
    /// Called when the server refuses the key (or the first message) for good.
    var onRejected: (() -> Void)?

    private let session = URLSession(configuration: .default)
    private var task: URLSessionWebSocketTask?
    private var url: URL?
    private var key: String?
    private var generation = 0
    private var retry = 0
    private var running = false
    private var pingTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?

    private(set) var status: Status = .offline {
        didSet { if status != oldValue { onStatus?(status) } }
    }

    func start(url: URL, key: String) {
        self.url = url
        self.key = key
        running = true
        retry = 0
        open()
    }

    func stop() {
        running = false
        generation += 1
        reconnectTask?.cancel()
        pingTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        status = .offline
    }

    /// Sends one JSON object. Returns false when the socket isn't usable.
    @discardableResult
    func send(_ object: [String: Any]) -> Bool {
        guard status == .online, let task, JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object),
              let text = String(data: data, encoding: .utf8) else { return false }
        task.send(.string(text)) { _ in }
        return true
    }

    private func open() {
        guard running, let url, let key else { return }
        generation += 1
        let gen = generation
        reconnectTask?.cancel()
        pingTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        status = .connecting

        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()

        Task { [weak self] in
            do {
                let data = try JSONSerialization.data(withJSONObject: AdminConnection.authMessage(key: key))
                try await task.send(.string(String(decoding: data, as: UTF8.self)))
                guard let self, gen == self.generation else { return }
                self.retry = 0
                self.status = .online
                self.startPinging(gen: gen)
                await self.receive(task, gen: gen)
            } catch {
                guard let self, gen == self.generation else { return }
                self.dropped(task, gen: gen)
            }
        }
    }

    private func receive(_ task: URLSessionWebSocketTask, gen: Int) async {
        while gen == generation {
            do {
                let message = try await task.receive()
                guard gen == generation else { return }
                let data: Data
                switch message {
                case .string(let text): data = Data(text.utf8)
                case .data(let bytes): data = bytes
                @unknown default: continue
                }
                if let decoded = try? AdminServerMessage.decode(data) { onMessage?(decoded) }
            } catch {
                guard gen == generation else { return }
                dropped(task, gen: gen)
                return
            }
        }
    }

    private func dropped(_ task: URLSessionWebSocketTask, gen: Int) {
        pingTask?.cancel()
        status = .offline
        let code = task.closeCode.rawValue
        if code == 4401 || code == 4400 {  // refused: wrong key or bad first message; retrying won't help
            running = false
            onRejected?()
            return
        }
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        guard running else { return }
        let delay = min(10.0, 0.5 * pow(2.0, Double(retry))) + Double.random(in: 0...0.3)
        retry += 1
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.open()
        }
    }

    private func startPinging(gen: Int) {
        pingTask?.cancel()
        pingTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 25_000_000_000)
                guard !Task.isCancelled, let self, gen == self.generation else { return }
                self.send(AdminConnection.pingMessage)
            }
        }
    }
}
