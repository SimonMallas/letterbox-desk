import Foundation
import Network

/// Finds the listener's `_belldesk._tcp` service and resolves it to "address:port" for URLSession.
@MainActor
final class Discovery {
    var onChange: (String?) -> Void = { _ in }
    /// True when iOS refused Local Network access to this app.
    var onDenied: (Bool) -> Void = { _ in }

    private var browser: NWBrowser?
    private var service: NWEndpoint?
    private var resolver: NWConnection?

    /// The first browse is what makes iOS ask for Local Network permission.
    func start() {
        guard browser == nil else { return }
        let browser = NWBrowser(for: .bonjour(type: "_belldesk._tcp", domain: nil), using: .tcp)
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in self?.pick(from: results) }
        }
        browser.stateUpdateHandler = { [weak self] state in
            let denied: Bool?
            switch state {
            case .waiting(let error):
                // kDNSServiceErr_PolicyDenied: the person said no to Local Network.
                if case .dns(let code) = error, code == -65570 { denied = true } else { denied = nil }
            case .ready:
                denied = false
            default:
                denied = nil
            }
            if let denied {
                Task { @MainActor in self?.onDenied(denied) }
            }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    /// Resolve again, e.g. after the link drops in case the Mac's address changed.
    func refresh() {
        if let service { resolve(service) }
    }

    private func pick(from results: Set<NWBrowser.Result>) {
        let preferred = results.first { result in
            if case .service(let name, _, _, _) = result.endpoint { return name == "Bell on the Desk" }
            return false
        } ?? results.first
        service = preferred?.endpoint
        if let service {
            resolve(service)
        } else {
            onChange(nil)
        }
    }

    private func resolve(_ endpoint: NWEndpoint) {
        resolver?.cancel()
        // IPv4 keeps the URL simple: no brackets or interface scope ids.
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            ip.version = .v4
        }
        let connection = NWConnection(to: endpoint, using: params)
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                let remote = connection.currentPath?.remoteEndpoint
                connection.cancel()
                Task { @MainActor in self?.resolved(remote) }
            case .failed, .waiting:
                connection.cancel()
            default:
                break
            }
        }
        connection.start(queue: .main)
        resolver = connection
    }

    private func resolved(_ remote: NWEndpoint?) {
        guard case .hostPort(let host, let port) = remote else { return }
        let address: String
        switch host {
        case .ipv4(let v4):
            address = "\(v4)"
        case .name(let name, _):
            address = name
        default:
            return
        }
        // Drop any "%en0" interface suffix.
        let bare = address.split(separator: "%").first.map(String.init) ?? address
        onChange("\(bare):\(port.rawValue)")
    }
}
