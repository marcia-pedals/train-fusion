import Foundation

/// The rider's settings (UserDefaults). docs/accounts-and-uploads.md says what they mean.
enum AppSettings {
    /// Ask after each ride whether to upload its recording.
    static let askToUploadKey = "askToUpload"
    /// How many recordings to keep on the phone (`KeepRecordings`).
    static let keepRecordingsKey = "keepRecordings"
    /// Share each ride's Train Measurements when it ends (linked to the account if signed in).
    static let shareMeasurementsKey = "shareMeasurements"
    /// Whether the rider has been asked about sharing Train Measurements (before their first
    /// recording).
    static let askedShareMeasurementsKey = "askedShareMeasurements"

    static func registerDefaults() {
        UserDefaults.standard.register(defaults: [
            askToUploadKey: true,
            keepRecordingsKey: KeepRecordings.last3.rawValue,
            shareMeasurementsKey: false,
            askedShareMeasurementsKey: false,
        ])
    }

    static var askToUpload: Bool {
        get { UserDefaults.standard.bool(forKey: askToUploadKey) }
        set { UserDefaults.standard.set(newValue, forKey: askToUploadKey) }
    }

    static var keepRecordings: KeepRecordings {
        KeepRecordings(rawValue: UserDefaults.standard.string(forKey: keepRecordingsKey) ?? "") ?? .last3
    }

    static var shareMeasurements: Bool {
        get { UserDefaults.standard.bool(forKey: shareMeasurementsKey) }
        set { UserDefaults.standard.set(newValue, forKey: shareMeasurementsKey) }
    }

    /// Recordings whose Train Measurements are to be shared when they end although the
    /// setting is off ("Share just this ride").
    private static let shareOnceKey = "shareMeasurementsOnce"

    static func shareOnce(_ id: String) {
        UserDefaults.standard.set(Array(Set(UserDefaults.standard.stringArray(forKey: shareOnceKey) ?? []).union([id])),
                                  forKey: shareOnceKey)
    }

    /// Whether `id` was to be shared once; forgets it.
    static func takeShareOnce(_ id: String) -> Bool {
        var ids = UserDefaults.standard.stringArray(forKey: shareOnceKey) ?? []
        guard let i = ids.firstIndex(of: id) else { return false }
        ids.remove(at: i)
        UserDefaults.standard.set(ids, forKey: shareOnceKey)
        return true
    }

    static var askedShareMeasurements: Bool {
        get { UserDefaults.standard.bool(forKey: askedShareMeasurementsKey) }
        set { UserDefaults.standard.set(newValue, forKey: askedShareMeasurementsKey) }
    }

    /// The most an upload may be, all its files together (the server enforces it too).
    static let maxUploadBytes: Int64 = 200_000_000
}

enum KeepRecordings: String, CaseIterable, Identifiable {
    case none, last3, all

    var id: Self { self }

    var label: String {
        switch self {
        case .none: return "None"
        case .last3: return "Last 3"
        case .all: return "All"
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
