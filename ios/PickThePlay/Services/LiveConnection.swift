import Foundation

/// The live `/ws` channel: sends `hello` on every (re)connect, decodes server messages, pings every
/// 25 s (keeps sleepy hosts awake and notices dead links) and reconnects with backoff.
@MainActor
final class LiveConnection {
    enum Status: Equatable {
        case offline, connecting, online
    }

    var onMessage: ((ServerMessage) -> Void)?
    var onStatus: ((Status) -> Void)?
    /// Called with the close code when the server refuses us for good (e.g. deleted account).
    var onRejected: ((Int) -> Void)?

    private let session = URLSession(configuration: .default)
    private var task: URLSessionWebSocketTask?
    private var url: URL?
    private var hello: HelloMessage?
    private var generation = 0
    private var retry = 0
    private var running = false
    private var pingTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?

    private(set) var status: Status = .offline {
        didSet { if status != oldValue { onStatus?(status) } }
    }

    func start(url: URL, hello: HelloMessage) {
        self.url = url
        self.hello = hello
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

    /// Returns false when the socket isn't usable, so callers can fall back to HTTP.
    @discardableResult
    func send<T: Encodable>(_ message: T) -> Bool {
        guard status == .online, let task, let data = try? JSON.encoder.encode(message),
              let text = String(data: data, encoding: .utf8) else { return false }
        task.send(.string(text)) { _ in }
        return true
    }

    private func open() {
        guard running, let url, let hello else { return }
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
                let data = try JSON.encoder.encode(hello)
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
                if let decoded = try? ServerMessage.decode(data) { onMessage?(decoded) }
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
        if code == 4401 || code == 4400 {  // refused: bad credentials or bad hello; retrying won't help
            running = false
            onRejected?(code)
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
                self.send(PingMessage())
            }
        }
    }
}
