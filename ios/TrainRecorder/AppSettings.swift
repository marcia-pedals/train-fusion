import Foundation

/// The rider's settings (UserDefaults). docs/accounts-and-uploads.md says what they mean.
enum AppSettings {
    /// Ask after each ride whether to upload its recording.
    static let askToUploadKey = "askToUpload"
    /// How many recordings to keep on the phone (`KeepRecordings`).
    static let keepRecordingsKey = "keepRecordings"
    /// Sync each ride's timings to the account when it ends (if signed in).
    static let syncRidesKey = "syncRides"

    static func registerDefaults() {
        UserDefaults.standard.register(defaults: [
            askToUploadKey: true,
            keepRecordingsKey: KeepRecordings.last3.rawValue,
            syncRidesKey: true,
        ])
    }

    static var askToUpload: Bool {
        get { UserDefaults.standard.bool(forKey: askToUploadKey) }
        set { UserDefaults.standard.set(newValue, forKey: askToUploadKey) }
    }

    static var keepRecordings: KeepRecordings {
        KeepRecordings(rawValue: UserDefaults.standard.string(forKey: keepRecordingsKey) ?? "") ?? .last3
    }

    static var syncRides: Bool { UserDefaults.standard.bool(forKey: syncRidesKey) }

    /// The most an upload may be, all its files together (the server enforces it too).
    static let maxUploadBytes: Int64 = 200_000_000
}

enum KeepRecordings: String, CaseIterable, Identifiable {
    case none, last3, all

    var id: Self { self }

    var label: String {
        switch self {
        case .none: return "Don't keep"
        case .last3: return "Keep last 3"
        case .all: return "Keep all"
        }
    }

    /// How many of the newest recordings are kept whatever their state; nil: all of them.
    var count: Int? {
        switch self {
        case .none: return 0
        case .last3: return 3
        case .all: return nil
        }
    }
}
