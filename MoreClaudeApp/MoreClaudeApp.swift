// MoreClaudeApp.swift — app entry point.
//
// One app, two front doors: a management window, and a menu bar item for
// launching a profile without opening the window at all.

import SwiftUI

@main
struct MoreClaudeApp: App {
    @StateObject private var engine = Engine()

    var body: some Scene {
        Window("More Claude", id: "manager") {
            ContentView()
                .environmentObject(engine)
        }
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(after: .toolbar) {
                Button("Refresh") { engine.refresh() }
                    .keyboardShortcut("r", modifiers: [.command])
                Button("Rebuild All Profiles") { engine.buildAll() }
                    .keyboardShortcut("r", modifiers: [.command, .shift])
                    .disabled(engine.isBusy)
            }
        }

        MenuBarExtra("More Claude", systemImage: "person.2.circle") {
            MenuBarContent()
                .environmentObject(engine)
                // The menu is rebuilt each time it opens, so this keeps the
                // status marks current without polling while it's closed.
                .onAppear { engine.refresh() }
        }
    }
}

struct MenuBarContent: View {
    @EnvironmentObject var engine: Engine
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if engine.snapshot.profiles.isEmpty {
            Text("No profiles yet")
        } else {
            ForEach(engine.snapshot.profiles) { profile in
                Button {
                    engine.launch(profile)
                } label: {
                    Text(profile.status == .running ? "\(profile.name) ✓" : profile.name)
                }
            }
        }

        Divider()

        Button("Open More Claude…") {
            openWindow(id: "manager")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("Rebuild All") { engine.buildAll() }
            .disabled(engine.isBusy)

        Divider()

        Button("Quit More Claude") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}
