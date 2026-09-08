// Engine.swift — the bridge between the GUI and more_claude.py.
//
// All of the actual bundle-building logic lives in the Python script; this
// app is a front end for it. Keeping one implementation means the CLI and
// the GUI can never drift apart, and the script stays usable on its own.

import Foundation
import AppKit

// MARK: - Model

struct Profile: Identifiable, Hashable {
    let id: String
    var name: String
    var icon: String?
    var bundlePath: String
    var dataPath: String
    var status: Status

    enum Status: String {
        case notBuilt = "not_built"
        case built
        case running

        var label: String {
            switch self {
            case .notBuilt: return "Not built"
            case .built: return "Ready"
            case .running: return "Running"
            }
        }

        var color: NSColor {
            switch self {
            case .notBuilt: return .systemGray
            case .built: return .systemBlue
            case .running: return .systemGreen
            }
        }
    }

    /// The best available image for this profile: the icon the user chose,
    /// else whatever Finder shows for the built bundle, else nothing.
    var image: NSImage? {
        if let icon, let img = NSImage(contentsOfFile: icon) { return img }
        if FileManager.default.fileExists(atPath: bundlePath) {
            return NSWorkspace.shared.icon(forFile: bundlePath)
        }
        return nil
    }
}

struct Snapshot {
    var masterInstalled = false
    var masterVersion: String?
    var masterApp = ""
    var profilesDir = ""
    var dataDir = ""
    var profiles: [Profile] = []
}

// MARK: - Locating the pieces

enum Paths {
    static let supportDir = NSString(string: "~/Library/Application Support/MoreClaude")
        .expandingTildeInPath

    /// The script ships inside the app bundle, but a development checkout or
    /// an install.sh run may have put one in Application Support — prefer the
    /// bundled copy so the GUI and its engine always match.
    static var script: String? {
        if let bundled = Bundle.main.path(forResource: "more_claude", ofType: "py") {
            return bundled
        }
        let installed = (supportDir as NSString).appendingPathComponent("more_claude.py")
        return FileManager.default.isReadableFile(atPath: installed) ? installed : nil
    }

    /// A GUI app inherits a minimal PATH, so `env python3` isn't reliable.
    /// install.sh records the interpreter it used; otherwise probe the usual
    /// locations, ending at the system one that always exists.
    static var python: String {
        let fm = FileManager.default
        let recorded = (supportDir as NSString).appendingPathComponent("python3-path")
        if let raw = try? String(contentsOfFile: recorded, encoding: .utf8) {
            let path = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if !path.isEmpty, fm.isExecutableFile(atPath: path) { return path }
        }
        for candidate in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"] {
            if fm.isExecutableFile(atPath: candidate) { return candidate }
        }
        return "/usr/bin/python3"
    }
}

// MARK: - Running the engine

final class Engine: ObservableObject {
    @Published var snapshot = Snapshot()
    @Published var log = ""
    @Published var isBusy = false
    /// Set when something needs the user's attention rather than the log.
    @Published var problem: String?

    private let queue = DispatchQueue(label: "com.moreclaude.engine")
    private var pollTimer: Timer?

    // MARK: Keeping the snapshot honest

    /// A profile can start or stop outside this app — the user quits it from
    /// its own Dock icon, or it dies on launch. Without polling, the status
    /// shown here is only ever as fresh as the last command we ran.
    func startPolling(every interval: TimeInterval = 4) {
        stopPolling()
        pollTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            guard let self, !self.isBusy else { return }
            self.refresh()
        }
    }

    func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    // MARK: Reading state

    func refresh() {
        guard let script = Paths.script else {
            problem = "more_claude.py is missing from the app bundle. Reinstall More Claude."
            return
        }
        queue.async {
            let result = Self.capture(script: script, args: ["list", "--json"])
            let parsed = Self.parse(result.stdout)
            DispatchQueue.main.async {
                if let parsed {
                    self.snapshot = parsed
                } else if !result.stderr.isEmpty {
                    self.problem = "Couldn't read the profile list:\n\(result.stderr)"
                }
            }
        }
    }

    // MARK: Running a command with streamed output

    /// Runs the engine, streaming its output into the log so a long rebuild
    /// shows progress instead of freezing. Refreshes state when it finishes.
    func perform(_ args: [String], describedAs description: String) {
        guard let script = Paths.script else {
            problem = "more_claude.py is missing from the app bundle. Reinstall More Claude."
            return
        }
        guard !isBusy else { return }

        isBusy = true
        append("\n$ \(description)\n")

        let process = Process()
        process.executableURL = URL(fileURLWithPath: Paths.python)
        // -u: unbuffered, so the log fills in as the build runs.
        process.arguments = ["-u", script] + args

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.append(text) }
        }

        process.terminationHandler = { [weak self] proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                self?.isBusy = false
                if proc.terminationStatus != 0 {
                    self?.append("(exited with status \(proc.terminationStatus))\n")
                }
                self?.refresh()
            }
        }

        do {
            try process.run()
        } catch {
            isBusy = false
            problem = "Couldn't run more_claude.py: \(error.localizedDescription)"
        }
    }

    func launch(_ profile: Profile) {
        perform(["launch", "--id", profile.id], describedAs: "launch \(profile.name)")
    }

    func build(_ profile: Profile, force: Bool = false) {
        perform(["build", "--id", profile.id] + (force ? ["--force"] : []),
                describedAs: "rebuild \(profile.name)")
    }

    func buildAll(force: Bool = false) {
        perform(["build", "--all"] + (force ? ["--force"] : []),
                describedAs: "rebuild all profiles")
    }

    func add(id: String, name: String, icon: String?) {
        var args = ["add", "--id", id, "--name", name]
        if let icon { args += ["--icon", icon] }
        perform(args, describedAs: "add \(name)")
    }

    func update(_ profile: Profile, name: String?, icon: String??) {
        var args = ["set", "--id", profile.id]
        if let name { args += ["--name", name] }
        if let icon { args += ["--icon", icon ?? ""] }
        args.append("--rebuild")
        perform(args, describedAs: "update \(profile.name)")
    }

    func remove(_ profile: Profile, purgeData: Bool) {
        perform(["remove", "--id", profile.id] + (purgeData ? ["--purge"] : []),
                describedAs: "remove \(profile.name)")
    }

    func clearLog() { log = "" }

    // MARK: Helpers

    private func append(_ text: String) {
        log += text
        // Keep the log from growing without bound over a long session.
        if log.count > 200_000 {
            log = String(log.suffix(120_000))
        }
    }

    private static func capture(script: String, args: [String]) -> (stdout: String, stderr: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: Paths.python)
        process.arguments = [script] + args
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        do {
            try process.run()
        } catch {
            return ("", error.localizedDescription)
        }
        let outData = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (String(data: outData, encoding: .utf8) ?? "",
                String(data: errData, encoding: .utf8) ?? "")
    }

    private static func parse(_ json: String) -> Snapshot? {
        guard
            let data = json.data(using: .utf8),
            let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }

        var snapshot = Snapshot()
        snapshot.masterInstalled = root["master_installed"] as? Bool ?? false
        snapshot.masterVersion = root["master_version"] as? String
        snapshot.masterApp = root["master_app"] as? String ?? ""
        snapshot.profilesDir = root["profiles_dir"] as? String ?? ""
        snapshot.dataDir = root["data_dir"] as? String ?? ""
        snapshot.profiles = (root["profiles"] as? [[String: Any]] ?? []).compactMap { entry in
            guard
                let id = entry["id"] as? String,
                let name = entry["name"] as? String,
                let bundlePath = entry["bundle_path"] as? String,
                let dataPath = entry["data_path"] as? String
            else { return nil }
            return Profile(
                id: id,
                name: name,
                icon: entry["icon"] as? String,
                bundlePath: bundlePath,
                dataPath: dataPath,
                status: Profile.Status(rawValue: entry["status"] as? String ?? "") ?? .notBuilt
            )
        }
        return snapshot
    }
}
