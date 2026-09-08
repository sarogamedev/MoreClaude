// Views.swift — the More Claude window: a profile list, a detail pane for
// the selected profile, and a collapsible log of what the engine is doing.

import SwiftUI
import AppKit

// MARK: - Root

struct ContentView: View {
    @EnvironmentObject var engine: Engine
    @State private var selection: Profile.ID?
    @State private var showingAdd = false
    @State private var editing: Profile?
    @State private var removing: Profile?
    @State private var purgeData = false
    @State private var showLog = false

    private var selected: Profile? {
        engine.snapshot.profiles.first { $0.id == selection }
    }

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 220, ideal: 260, max: 340)
        } detail: {
            Group {
                if let selected {
                    ProfileDetail(
                        profile: selected,
                        onEdit: { editing = selected },
                        onRemove: { purgeData = false; removing = selected }
                    )
                } else {
                    EmptyDetail(hasProfiles: !engine.snapshot.profiles.isEmpty) {
                        showingAdd = true
                    }
                }
            }
            .frame(minWidth: 380)
        }
        .toolbar { toolbarItems }
        .safeAreaInset(edge: .bottom) { footer }
        .frame(minWidth: 720, minHeight: 460)
        .onAppear {
            engine.refresh()
            engine.startPolling()
        }
        .onDisappear { engine.stopPolling() }
        .onReceive(NotificationCenter.default.publisher(
            for: NSApplication.didBecomeActiveNotification)) { _ in
            engine.refresh()
        }
        .sheet(isPresented: $showingAdd) {
            ProfileSheet(title: "Add a Profile", profile: nil) { name, id, icon in
                engine.add(id: id, name: name, icon: icon)
            }
        }
        .sheet(item: $editing) { profile in
            ProfileSheet(title: "Edit Profile", profile: profile) { name, _, icon in
                engine.update(profile, name: name, icon: .some(icon))
            }
        }
        .alert("Remove “\(removing?.name ?? "")”?",
               isPresented: Binding(get: { removing != nil },
                                    set: { if !$0 { removing = nil } })) {
            Button("Cancel", role: .cancel) { removing = nil }
            Button("Remove", role: .destructive) {
                if let removing { engine.remove(removing, purgeData: purgeData) }
                selection = nil
                removing = nil
            }
        } message: {
            Text(purgeData
                 ? "The app bundle and this profile's login data will both be deleted."
                 : "The app bundle will be deleted. The login data is kept, so re-adding this profile with the same ID restores the session.")
        }
        .alert("Something went wrong",
               isPresented: Binding(get: { engine.problem != nil },
                                    set: { if !$0 { engine.problem = nil } })) {
            Button("OK") { engine.problem = nil }
        } message: {
            Text(engine.problem ?? "")
        }
    }

    // MARK: Sidebar

    private var sidebar: some View {
        List(selection: $selection) {
            Section("Profiles") {
                ForEach(engine.snapshot.profiles) { profile in
                    ProfileRow(profile: profile)
                        .tag(profile.id)
                        .contextMenu {
                            Button("Launch") { engine.launch(profile) }
                            Button("Rebuild") { engine.build(profile) }
                            Divider()
                            Button("Edit…") { editing = profile }
                            Button("Remove…", role: .destructive) {
                                purgeData = false
                                removing = profile
                            }
                        }
                }
            }
        }
        .listStyle(.sidebar)
        .overlay {
            if engine.snapshot.profiles.isEmpty {
                ContentUnavailableView(
                    "No Profiles",
                    systemImage: "person.2.slash",
                    description: Text("Add one to get a second Claude Desktop account.")
                )
            }
        }
    }

    // MARK: Toolbar

    @ToolbarContentBuilder
    private var toolbarItems: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            Button {
                showingAdd = true
            } label: {
                Label("Add Profile", systemImage: "plus")
            }
            .help("Add a new Claude account profile")
        }
        ToolbarItemGroup {
            if engine.isBusy { ProgressView().controlSize(.small) }
            Button {
                engine.buildAll()
            } label: {
                Label("Rebuild All", systemImage: "arrow.triangle.2.circlepath")
            }
            .disabled(engine.isBusy || engine.snapshot.profiles.isEmpty)
            .help("Rebuild every profile from the current Claude.app")

            Button {
                withAnimation { showLog.toggle() }
            } label: {
                Label("Log", systemImage: "text.alignleft")
            }
            .help("Show what the engine is doing")
        }
    }

    // MARK: Footer + log

    private var footer: some View {
        VStack(spacing: 0) {
            if showLog {
                Divider()
                LogView(text: engine.log) { engine.clearLog() }
                    .frame(height: 170)
            }
            Divider()
            HStack(spacing: 6) {
                if engine.snapshot.masterInstalled {
                    Image(systemName: "checkmark.seal.fill").foregroundStyle(.secondary)
                    Text("Claude \(engine.snapshot.masterVersion ?? "—")")
                } else {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text("Claude Desktop not found at \(engine.snapshot.masterApp)")
                }
                Spacer()
                Text("\(engine.snapshot.profiles.count) profile\(engine.snapshot.profiles.count == 1 ? "" : "s")")
            }
            .font(.callout)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
        }
        .background(.bar)
    }
}

// MARK: - Sidebar row

struct ProfileRow: View {
    let profile: Profile

    var body: some View {
        HStack(spacing: 9) {
            ProfileIcon(profile: profile, size: 26)
            VStack(alignment: .leading, spacing: 1) {
                Text(profile.name).lineLimit(1)
                Text(profile.status.label)
                    .font(.caption)
                    .foregroundStyle(Color(profile.status.color))
            }
        }
        .padding(.vertical, 2)
    }
}

struct ProfileIcon: View {
    let profile: Profile
    var size: CGFloat

    var body: some View {
        Group {
            if let image = profile.image {
                Image(nsImage: image).resizable()
            } else {
                Image(systemName: "app.dashed")
                    .resizable()
                    .foregroundStyle(.tertiary)
                    .padding(size * 0.1)
            }
        }
        .frame(width: size, height: size)
    }
}

// MARK: - Detail

struct ProfileDetail: View {
    @EnvironmentObject var engine: Engine
    let profile: Profile
    var onEdit: () -> Void
    var onRemove: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 14) {
                    ProfileIcon(profile: profile, size: 64)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(profile.name).font(.title2).bold()
                        HStack(spacing: 6) {
                            Circle()
                                .fill(Color(profile.status.color))
                                .frame(width: 7, height: 7)
                            Text(profile.status.label)
                            Text("·").foregroundStyle(.tertiary)
                            Text(profile.id).monospaced()
                        }
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                HStack(spacing: 10) {
                    Button {
                        engine.launch(profile)
                    } label: {
                        Label("Launch", systemImage: "arrow.up.forward.app")
                            .frame(maxWidth: .infinity)
                    }
                    .keyboardShortcut(.defaultAction)
                    .controlSize(.large)

                    Button {
                        engine.build(profile, force: profile.status == .running)
                    } label: {
                        Label(profile.status == .notBuilt ? "Build" : "Rebuild",
                              systemImage: "hammer")
                            .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)
                }
                .disabled(engine.isBusy)

                if profile.status == .running {
                    Label("This profile is open. Rebuilding it will quit and replace the running app.",
                          systemImage: "info.circle")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                GroupBox {
                    VStack(spacing: 0) {
                        PathRow(label: "App bundle", path: profile.bundlePath,
                                exists: FileManager.default.fileExists(atPath: profile.bundlePath))
                        Divider()
                        PathRow(label: "Login data", path: profile.dataPath,
                                exists: FileManager.default.fileExists(atPath: profile.dataPath))
                        Divider()
                        PathRow(label: "Icon", path: profile.icon ?? "Claude's default icon",
                                exists: profile.icon.map {
                                    FileManager.default.fileExists(atPath: $0)
                                } ?? false)
                    }
                }

                Text("Login data lives outside the app bundle, so rebuilding after a Claude Desktop update keeps you signed in.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack {
                    Button("Edit…", action: onEdit)
                    Spacer()
                    Button("Remove…", role: .destructive, action: onRemove)
                }
                .disabled(engine.isBusy)
            }
            .padding(20)
        }
    }
}

struct PathRow: View {
    let label: String
    let path: String
    let exists: Bool

    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 88, alignment: .leading)
            Text(path)
                .font(.callout.monospaced())
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            Spacer(minLength: 4)
            if exists {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                } label: {
                    Image(systemName: "arrow.forward.circle")
                }
                .buttonStyle(.borderless)
                .help("Reveal in Finder")
            }
        }
        .padding(.vertical, 6)
    }
}

struct EmptyDetail: View {
    let hasProfiles: Bool
    var onAdd: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "person.2.circle")
                .font(.system(size: 52))
                .foregroundStyle(.tertiary)
            Text(hasProfiles ? "Select a profile" : "Add your first profile")
                .font(.title3)
            Text("Each profile is a separate Claude Desktop app with its own Dock icon and its own login.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 340)
            if !hasProfiles {
                Button("Add Profile…", action: onAdd)
                    .controlSize(.large)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Add / edit sheet

struct ProfileSheet: View {
    @Environment(\.dismiss) private var dismiss

    let title: String
    /// nil when adding; the ID is fixed once a profile exists because it names
    /// the login-data folder.
    let profile: Profile?
    var onSubmit: (_ name: String, _ id: String, _ icon: String?) -> Void

    @State private var name = ""
    @State private var id = ""
    @State private var iconPath: String?
    @State private var idEditedByHand = false

    private var isEditing: Bool { profile != nil }

    private var idIsValid: Bool {
        !id.isEmpty && id.count <= 64
            && id.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
    }

    /// The name becomes the .app filename, so it has to stay a single path
    /// component — the engine rejects anything else, and this mirrors that
    /// so the user finds out here rather than in the log.
    private var nameIsValid: Bool {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        return !trimmed.isEmpty
            && trimmed.count <= 128
            && !name.contains("/")
            && !name.contains(#"\"#)
            && trimmed != "."
            && trimmed != ".."
    }

    private var canSubmit: Bool {
        nameIsValid && (isEditing || idIsValid)
    }

    private var nameHelpText: String {
        if name.isEmpty || nameIsValid {
            return "Shown in the Dock and the app switcher."
        }
        return #"A name can't contain / or \, and must be 128 characters or fewer."#
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.headline)

            Form {
                TextField("Name", text: $name, prompt: Text("Claude Work"))
                    .onChange(of: name) { _, newValue in
                        if !isEditing && !idEditedByHand { id = Self.slug(newValue) }
                    }
                // verbatim: a String literal here would be treated as a
                // LocalizedStringKey, which eats the backslash as an escape.
                Text(verbatim: nameHelpText)
                    .font(.caption)
                    .foregroundStyle(name.isEmpty || nameIsValid ? .secondary : Color.red)

                if !isEditing {
                    TextField("ID", text: $id, prompt: Text("work"))
                        .onChange(of: id) { _, _ in idEditedByHand = true }
                    Text("Names the login-data folder. Letters, numbers, - and _ only; it can't be changed later.")
                        .font(.caption)
                        .foregroundStyle(id.isEmpty || idIsValid ? .secondary : Color.red)
                }

                LabeledContent("Icon") {
                    HStack {
                        if let iconPath, let image = NSImage(contentsOfFile: iconPath) {
                            Image(nsImage: image).resizable().frame(width: 32, height: 32)
                        } else {
                            Image(systemName: "app.dashed")
                                .resizable().frame(width: 32, height: 32)
                                .foregroundStyle(.tertiary)
                        }
                        Button("Choose…") { chooseIcon() }
                        if iconPath != nil {
                            Button("Clear") { iconPath = nil }
                        }
                        Spacer()
                    }
                }
                Text("Optional. .icns, .png or .jpg — anything but .icns is converted for you.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(isEditing ? "Save & Rebuild" : "Add") {
                    onSubmit(name.trimmingCharacters(in: .whitespaces), id, iconPath)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!canSubmit)
            }
        }
        .padding(18)
        .frame(width: 460)
        .onAppear {
            if let profile {
                name = profile.name
                id = profile.id
                iconPath = profile.icon
            }
        }
    }

    private func chooseIcon() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .icns]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Choose an icon for this profile"
        if panel.runModal() == .OK, let url = panel.url {
            iconPath = url.path
        }
    }

    /// "Claude Work" -> "claude-work", so the ID field fills itself in.
    private static func slug(_ text: String) -> String {
        let lowered = text.lowercased()
        var out = ""
        var lastWasDash = false
        for character in lowered {
            if character.isLetter || character.isNumber {
                out.append(character)
                lastWasDash = false
            } else if !out.isEmpty && !lastWasDash {
                out.append("-")
                lastWasDash = true
            }
        }
        while out.hasSuffix("-") { out.removeLast() }
        return out
    }
}

// MARK: - Log

struct LogView: View {
    let text: String
    var onClear: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Engine Log").font(.caption.bold()).foregroundStyle(.secondary)
                Spacer()
                Button("Clear", action: onClear)
                    .buttonStyle(.borderless)
                    .font(.caption)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 4)

            ScrollViewReader { proxy in
                ScrollView {
                    Text(text.isEmpty ? "Nothing yet." : text)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 12)
                        .padding(.bottom, 8)
                        .id("log-bottom")
                }
                .onChange(of: text) { _, _ in
                    proxy.scrollTo("log-bottom", anchor: .bottom)
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }
}
