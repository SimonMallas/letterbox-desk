import SwiftUI

@main
struct BellDeskApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .statusBarHidden(true)
                .hidingHomeIndicator()
                .onAppear {
                    model.start()
                    model.scenePhaseChanged(scenePhase)
                }
                .onChange(of: scenePhase) { phase in
                    model.scenePhaseChanged(phase)
                }
        }
    }
}

private extension View {
    /// iOS 15 has no SwiftUI way to auto-hide the home indicator; there it stays visible.
    @ViewBuilder func hidingHomeIndicator() -> some View {
        if #available(iOS 16.0, *) {
            persistentSystemOverlays(.hidden)
        } else {
            self
        }
    }
}
