import Foundation

/// Where the game server lives. The build bakes in `PTPServerURL` (from the PTP_SERVER_URL build
/// setting); a player can override it in Settings, e.g. to join a game on a computer on their Wi-Fi.
enum ServerConfig {
    static let overrideKey = "ptp.serverOverride"

    static var bundled: URL? {
        guard let raw = Bundle.main.object(forInfoDictionaryKey: "PTPServerURL") as? String else { return nil }
        return normalize(raw)
    }

    static var current: URL? {
        if let raw = UserDefaults.standard.string(forKey: overrideKey), let url = normalize(raw) { return url }
        return bundled
    }

    /// Accepts "pick-the-play.onrender.com", "192.168.1.20:8000" or a full URL, and returns the
    /// origin (scheme + host + port) with no path. Bare IP addresses default to http, names to https.
    static func normalize(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !text.contains("$(") else { return nil }
        if !text.contains("://") {
            let host = text.split(separator: ":").first.map(String.init) ?? text
            let looksLocal = host.allSatisfy { $0.isNumber || $0 == "." } || host.hasSuffix(".local") || host == "localhost"
            text = (looksLocal ? "http://" : "https://") + text
        }
        guard var parts = URLComponents(string: text), let scheme = parts.scheme?.lowercased(),
              scheme == "http" || scheme == "https", let host = parts.host, !host.isEmpty else { return nil }
        parts.scheme = scheme
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        return parts.url
    }

    static func socketURL(for server: URL) -> URL? {
        guard var parts = URLComponents(url: server, resolvingAgainstBaseURL: false) else { return nil }
        parts.scheme = parts.scheme == "https" ? "wss" : "ws"
        parts.path = "/ws"
        return parts.url
    }
}

struct APIError: LocalizedError {
    var status: Int
    var message: String
    var errorDescription: String? { message }
    var isUnauthorized: Bool { status == 401 }
}

/// Thin async wrapper over the server's REST API.
struct APIClient {
    var server: URL
    var token: String?
    var session: URLSession = .shared

    func createUser(username: String) async throws -> UserAccount {
        try await request("POST", "/api/users", body: ["username": username])
    }

    func me() async throws -> MeResponse {
        try await request("GET", "/api/me")
    }

    func createLounge(name: String) async throws -> Lounge {
        try await request("POST", "/api/lounges", body: ["name": name])
    }

    func joinLounge(code: String) async throws -> Lounge {
        try await request("POST", "/api/lounges/\(code)/join")
    }

    /// HTTP fallback for the socket's `predict`; same body minus the `type` field.
    func predict(_ pick: PredictMessage) async throws -> Prediction {
        try await request("POST", "/api/predictions", body: pick.body)
    }

    func deleteAccount() async throws {
        _ = try await send("DELETE", "/api/me", body: nil)
    }

    // MARK: - Plumbing

    private func request<T: Decodable>(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> T {
        let data = try await send(method, path, body: body)
        return try JSON.decoder.decode(T.self, from: data)
    }

    private func send(_ method: String, _ path: String, body: [String: Any]?) async throws -> Data {
        guard let url = URL(string: path, relativeTo: server) else {
            throw APIError(status: 0, message: "The server address looks wrong. Check it in Settings.")
        }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: req)
        } catch {
            throw APIError(status: 0, message: "Can't reach the game server. Check your connection and try again.")
        }
        let (data, response) = result
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw APIError(status: status, message: Self.detail(from: data) ?? "Request failed (\(status)).")
        }
        return data
    }

    /// FastAPI errors are {"detail": "..."} or {"detail": [{"msg": "..."}]}.
    static func detail(from data: Data) -> String? {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let text = obj["detail"] as? String { return text }
        if let list = obj["detail"] as? [[String: Any]] {
            let msgs = list.compactMap { $0["msg"] as? String }
            return msgs.isEmpty ? nil : msgs.joined(separator: "; ")
        }
        return nil
    }
}
