import Foundation
import Security

/// The host's admin key, kept in the iPhone's Keychain (not in UserDefaults) so it never sits in a readable backup.
struct KeychainKeyStore: KeyStore {
    var service = "com.ptp.host"
    var account = "adminKey"

    func read() -> String? {
        var query = base()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func write(_ key: String) {
        delete()
        var query = base()
        query[kSecValueData as String] = Data(key.utf8)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        _ = SecItemAdd(query as CFDictionary, nil)
    }

    func delete() {
        _ = SecItemDelete(base() as CFDictionary)
    }

    private func base() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
