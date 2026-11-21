import SwiftUI
import UIKit

@main
struct TrainRecorderApp: App {
    @UIApplicationDelegateAdaptor private var delegate: AppDelegate

    init() {
        AppSettings.registerDefaults()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    /// iOS woke the app because uploads in the background session finished.
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        MainActor.assumeIsolated { Uploader.shared.backgroundCompletion = completionHandler }
    }
}
