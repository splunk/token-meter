import Cocoa
import Carbon.HIToolbox
import Foundation

private let tokenMeterMenubarURL = URL(string: "http://127.0.0.1:8722/menubar")!
private let tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/#sessions")!
private let tokenMeterBudgetSettingsURL = URL(string: "http://127.0.0.1:8722/#settings-budgets")!
private let tokenMeterUpdateSettingsURL = URL(string: "http://127.0.0.1:8722/#settings-updates")!
private let tokenMeterInstallUpdateURL = URL(string: "http://127.0.0.1:8722/updates/install")!
private let tokenMeterEnterpriseTokenomicsURL = URL(string: "https://www.splunk.com/en_us/products/tokenomics.html")!
private let pinnedSessionDefaultsKey = "TokenMeterPinnedSessionID"
private let titleModeDefaultsKey = "TokenMeterTitleMode"
private let titleMetricsDefaultsKey = "TokenMeterTitleMetrics"
private let todaySpendTitlePreferenceDefaultsKey = "TokenMeterTodaySpendTitlePreference"
private let quotaAlertsEnabledDefaultsKey = "TokenMeterQuotaAlertsEnabled"
private let quotaAlertThresholdDefaultsKey = "TokenMeterQuotaAlertThreshold"
private let quotaNotificationStatesDefaultsKey = "TokenMeterQuotaNotificationStates"
private let budgetNotificationStatesDefaultsKey = "TokenMeterBudgetNotificationStates"
private let budgetExceededMonthsDefaultsKey = "TokenMeterBudgetExceededNotificationMonths"
private let statusDisplayModeDefaultsKey = "TokenMeterStatusDisplayMode"
private let globalShortcutDefaultsKey = "TokenMeterGlobalShortcut"
private let customShortcutKeyCodeDefaultsKey = "TokenMeterCustomShortcutKeyCode"
private let customShortcutModifiersDefaultsKey = "TokenMeterCustomShortcutModifiers"
private let tokenMeterMenubarBundleIdentifier = "com.token-meter.menubar"
private let statusItemAutosaveName = "TokenMeterPrimaryStatusItem"
private let statusItemPreferredPositionDefaultsKey = "NSStatusItem Preferred Position \(statusItemAutosaveName)"
private let statusItemInitialPreferredPosition = 50
private let tokenMeterDefaults: UserDefaults = {
    if Bundle.main.bundleIdentifier == tokenMeterMenubarBundleIdentifier { return .standard }
    return UserDefaults(suiteName: tokenMeterMenubarBundleIdentifier) ?? .standard
}()

// Token Meter accent blue (#00BCEB)
extension NSColor {
    static let tokenMeterBlue = NSColor(srgbRed: 0.0, green: 0.737, blue: 0.922, alpha: 1.0)
}

private func splunkChevronImage(accessibilityDescription: String = "Splunk Token Meter") -> NSImage {
    let size = NSSize(width: 18, height: 18)
    let image = NSImage(size: size, flipped: false) { rect in
        let sourceWidth: CGFloat = 37.03
        let sourceHeight: CGFloat = 44.58
        let inset: CGFloat = 2
        let scale = min(
            (rect.width - inset * 2) / sourceWidth,
            (rect.height - inset * 2) / sourceHeight
        )
        let origin = NSPoint(
            x: rect.midX - sourceWidth * scale / 2,
            y: rect.midY - sourceHeight * scale / 2
        )
        func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint {
            NSPoint(x: origin.x + x * scale, y: origin.y + (sourceHeight - y) * scale)
        }

        let chevron = NSBezierPath()
        chevron.move(to: point(37.03, 26.16))
        chevron.line(to: point(37.03, 18.58))
        chevron.line(to: point(0, 0))
        chevron.line(to: point(0, 8.32))
        chevron.line(to: point(28.70, 22.29))
        chevron.line(to: point(0, 36.44))
        chevron.line(to: point(0, 44.58))
        chevron.line(to: point(37.03, 26.18))
        chevron.close()
        NSColor.black.setFill()
        chevron.fill()
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = accessibilityDescription
    return image
}

private struct StatusTitleSegment {
    var text: String
    var symbol: String?
    var accessibilityText: String
}

private struct StatusTitlePresentation {
    var providerSymbol: String
    var providerAccessibilityText: String
    var segments: [StatusTitleSegment]
    var warning: Bool

    var accessibilityTitle: String {
        let base = segments.map(\.accessibilityText).joined(separator: "  ")
        let described = providerAccessibilityText.isEmpty
            ? base
            : "\(providerAccessibilityText), \(base)"
        return warning ? "⚠︎ \(described)" : described
    }
}

private func runtimeMarkSVG(symbol: String) -> String? {
    let pathData: String
    switch symbol {
    case "runtime.codex":
        pathData = #"M83.7733 42.8087C84.6678 40.1149 84.9771 37.2613 84.6807 34.4385C84.3843 31.6156 83.489 28.8885 82.0544 26.4394C77.6908 18.8436 68.9203 14.9365 60.3548 16.7725C57.9831 14.1344 54.9591 12.1668 51.5864 11.0673C48.2137 9.96772 44.611 9.77498 41.1402 10.5084C37.6694 11.2418 34.4527 12.8755 31.8132 15.2455C29.1736 17.6155 27.204 20.6383 26.1024 24.0103C23.3212 24.5806 20.6938 25.738 18.3958 27.405C16.0977 29.0721 14.1819 31.2104 12.7765 33.6772C8.36538 41.2609 9.3669 50.8267 15.2527 57.3327C14.3549 60.0251 14.0424 62.8782 14.3361 65.7012C14.6298 68.5241 15.523 71.2518 16.9558 73.7017C21.325 81.3002 30.1011 85.207 38.6712 83.3686C40.5554 85.4904 42.8707 87.1858 45.4623 88.3416C48.0539 89.4975 50.8622 90.0871 53.6999 90.0713C62.4793 90.079 70.2575 84.4114 72.9393 76.0515C75.7201 75.4802 78.347 74.3225 80.6449 72.6555C82.9427 70.9886 84.8587 68.8507 86.2649 66.3846C90.6227 58.8145 89.6172 49.3005 83.7733 42.8087ZM53.6999 84.8356C50.1955 84.8411 46.801 83.6129 44.1116 81.3661L44.5848 81.098L60.5123 71.9043C60.9087 71.6718 61.2379 71.3402 61.4674 70.942C61.6969 70.5439 61.8189 70.0929 61.8215 69.6333V47.1769L68.5553 51.072C68.6225 51.1063 68.6694 51.1707 68.6814 51.2456V69.854C68.6641 78.1208 61.9667 84.8183 53.6999 84.8356ZM21.4977 71.0843C19.7402 68.0497 19.1092 64.4925 19.7156 61.0386L20.1885 61.3225L36.1321 70.5165C36.5266 70.748 36.9757 70.87 37.4331 70.87C37.8905 70.87 38.3396 70.748 38.7341 70.5165L58.21 59.2883V67.0628C58.2081 67.1031 58.1973 67.1424 58.1782 67.1779C58.1591 67.2134 58.1322 67.2441 58.0996 67.2678L41.9671 76.5722C34.798 80.7022 25.6388 78.2463 21.4977 71.0843ZM17.3026 36.3898C19.0723 33.3357 21.8655 31.0062 25.1878 29.8138V48.7376C25.1818 49.1949 25.2986 49.6453 25.5261 50.042C25.7535 50.4387 26.0833 50.7671 26.4809 50.9928L45.8622 62.1739L39.1283 66.069C39.0919 66.0883 39.0513 66.0984 39.0101 66.0984C38.9689 66.0984 38.9283 66.0883 38.8919 66.069L22.7908 56.7809C15.6359 52.6337 13.1822 43.4816 17.3026 36.3112V36.3898ZM72.624 49.2426L53.1792 37.9512L59.8976 34.0718C59.9341 34.0524 59.9747 34.0423 60.016 34.0423C60.0573 34.0423 60.0979 34.0524 60.1344 34.0718L76.2355 43.3761C78.6973 44.7966 80.7043 46.8882 82.0221 49.4065C83.3398 51.9249 83.914 54.7661 83.6775 57.5985C83.4411 60.431 82.4038 63.1377 80.6867 65.4027C78.9696 67.6677 76.6436 69.3975 73.9803 70.3901V51.466C73.9663 51.0096 73.834 50.5647 73.5962 50.1749C73.3584 49.7851 73.0234 49.4638 72.624 49.2426ZM79.3261 39.1657L78.8529 38.8815L62.9411 29.6089C62.5442 29.376 62.0924 29.2532 61.6322 29.2532C61.172 29.2532 60.7202 29.376 60.3233 29.6089L40.8629 40.8374V33.0628C40.8587 33.0233 40.8654 32.9834 40.882 32.9473C40.8987 32.9113 40.9248 32.8803 40.9575 32.8579L57.0586 23.5692C59.5263 22.1476 62.3478 21.458 65.193 21.5811C68.0382 21.7042 70.7896 22.6348 73.1253 24.2642C75.461 25.8936 77.2845 28.1543 78.3825 30.782C79.4806 33.4097 79.8077 36.2957 79.3257 39.1025V39.1657H79.3261ZM37.1888 52.9484L30.455 49.069C30.4213 49.0487 30.3925 49.0212 30.3707 48.9884C30.3488 48.9557 30.3345 48.9186 30.3286 48.8797V30.3188C30.3323 27.4714 31.1466 24.6839 32.6761 22.2822C34.2057 19.8805 36.3874 17.9639 38.9661 16.7564C41.5448 15.549 44.4139 15.1005 47.2381 15.4636C50.0622 15.8267 52.7247 16.9862 54.9141 18.8067L54.4409 19.0748L38.5134 28.2686C38.117 28.5011 37.7879 28.8327 37.5584 29.2308C37.329 29.629 37.207 30.0799 37.2045 30.5395L37.1888 52.9487V52.9484ZM40.8472 45.0632L49.5209 40.0643L58.21 45.0635V55.0615L49.5523 60.0608L40.8632 55.0615L40.8472 45.0632Z"#
    case "runtime.claude":
        pathData = #"M25.7146 63.2153L41.4393 54.3917L41.7025 53.6226L41.4393 53.1976H40.6705L38.0394 53.0359L29.054 52.7929L21.2624 52.4691L13.7134 52.0644L11.8111 51.6594L10.0303 49.3118L10.2123 48.138L11.8111 47.0657L14.0981 47.2681L19.1574 47.6119L26.7467 48.138L32.2516 48.4618L40.4073 49.3118H41.7025L41.8846 48.7857L41.4393 48.4618L41.0955 48.138L33.243 42.8155L24.7432 37.1894L20.2909 33.9513L17.8824 32.3119L16.6684 30.774L16.1422 27.4147L18.328 25.0062L21.2624 25.2088L22.0112 25.4112L24.9861 27.6979L31.3407 32.616L39.6381 38.7273L40.8525 39.7391L41.3381 39.395L41.399 39.1523L40.8525 38.2415L36.3394 30.0858L31.5227 21.7883L29.3775 18.3478L28.811 16.2837C28.6087 15.4334 28.4669 14.7252 28.4669 13.8549L30.9563 10.4753L32.3321 10.0303L35.6515 10.4756L37.0479 11.6897L39.112 16.4052L42.4513 23.8327L47.6321 33.9313L49.15 36.9265L49.9594 39.6991L50.2632 40.5491H50.7894V40.0632L51.2141 34.3766L52.0035 27.3944L52.7726 18.4087L53.0358 15.8793L54.2905 12.8435L56.7795 11.2041L58.7224 12.135L60.3212 14.422L60.0986 15.899L59.1474 22.0718L57.2857 31.7458L56.0713 38.2218H56.7795L57.5892 37.4121L60.8677 33.061L66.3723 26.18L68.801 23.448L71.6342 20.4325L73.4556 18.9957H76.8962L79.4255 22.7601L78.2926 26.6456L74.7509 31.1384L71.8163 34.943L67.607 40.6097L64.9758 45.1431L65.2188 45.5072L65.8464 45.4466L75.358 43.4228L80.4984 42.4917L86.6304 41.4393L89.4033 42.7346L89.7065 44.0502L88.6135 46.7419L82.0566 48.3607L74.3662 49.8989L62.9118 52.6109L62.77 52.7121L62.9321 52.9144L68.0925 53.4L70.2987 53.5214H75.7021L85.7601 54.2702L88.3912 56.0108L89.9697 58.1358L89.7065 59.7545L85.6589 61.8189L80.1949 60.5236L67.4452 57.4881L63.0735 56.3952H62.4665V56.7596L66.1093 60.3213L72.7877 66.3523L81.1461 74.1236L81.5707 76.0462L80.4984 77.5638L79.3649 77.4021L72.0186 71.8772L69.1854 69.3879L62.77 63.9844H62.3453V64.5509L63.8223 66.7164L71.6342 78.4544L72.0389 82.0567L71.4725 83.2308L69.4487 83.939L67.2222 83.534L62.6485 77.1189L57.9333 69.8937L54.1284 63.4177L53.6631 63.6809L51.4167 87.8651L50.3644 89.0995L47.9356 90.0303L45.9121 88.4924L44.8392 86.0031L45.9118 81.0852L47.2071 74.6701L48.2594 69.5699L49.2106 63.2356L49.7773 61.131L49.7367 60.9892L49.2715 61.0498L44.4954 67.607L37.23 77.4224L31.4825 83.5746L30.1063 84.1211L27.7181 82.8864L27.9408 80.6805L29.2763 78.7177L37.2297 68.5988L42.026 62.3248L45.1227 58.7025L45.1024 58.176H44.9204L23.7917 71.8975L20.0274 72.3831L18.4083 70.8655L18.6106 68.3761L19.3798 67.5664L25.7343 63.195L25.7146 63.2153Z"#
    case "runtime.cursor":
        pathData = #"M84.0704 28.9353L51.9066 10.4454C50.8738 9.85153 49.5994 9.85153 48.5666 10.4454L16.4043 28.9353C15.536 29.4345 15 30.3576 15 31.3575V68.6425C15 69.6424 15.536 70.5655 16.4043 71.0647L48.5681 89.5546C49.6009 90.1485 50.8753 90.1485 51.9081 89.5546L84.0719 71.0647C84.9402 70.5655 85.4762 69.6424 85.4762 68.6425V31.3575C85.4762 30.3576 84.9402 29.4345 84.0719 28.9353H84.0704ZM82.0501 32.8519L51.0006 86.4003C50.7907 86.7611 50.2366 86.6138 50.2366 86.1958V51.1329C50.2366 50.4322 49.8606 49.7842 49.2506 49.4324L18.7553 31.9017C18.3929 31.6927 18.5409 31.141 18.9606 31.141H81.0595C81.9414 31.141 82.4925 32.0927 82.0516 32.8534H82.0501V32.8519Z"#
    default:
        return nil
    }
    return #"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="\#(pathData)" fill="black"/></svg>"#
}

private func runtimeMarkImage(symbol: String, accessibilityDescription: String) -> NSImage {
    let size = NSSize(width: 16, height: 16)
    if let svg = runtimeMarkSVG(symbol: symbol),
       let image = NSImage(data: Data(svg.utf8))
    {
        image.size = size
        image.isTemplate = true
        image.accessibilityDescription = accessibilityDescription
        return image
    }
    let image = NSImage(size: size, flipped: false) { rect in
        let inset = rect.insetBy(dx: 1, dy: 1)
        NSColor.black.setStroke()
        NSColor.black.setFill()

        func path(_ points: [NSPoint], closed: Bool = false) -> NSBezierPath {
            let value = NSBezierPath()
            guard let first = points.first else { return value }
            value.move(to: first)
            for point in points.dropFirst() { value.line(to: point) }
            if closed { value.close() }
            value.lineWidth = 1.45
            value.lineCapStyle = .round
            value.lineJoinStyle = .round
            return value
        }

        switch symbol {
        case "runtime.opencode":
            path([
                NSPoint(x: 5.7, y: 2.2), NSPoint(x: 1.8, y: 7),
                NSPoint(x: 5.7, y: 11.8),
            ]).stroke()
            path([
                NSPoint(x: 8.3, y: 2.2), NSPoint(x: 12.2, y: 7),
                NSPoint(x: 8.3, y: 11.8),
            ]).stroke()
        default:
            let ring = NSBezierPath(ovalIn: inset)
            ring.lineWidth = 1.45
            ring.stroke()
            NSBezierPath(ovalIn: NSRect(x: 5.4, y: 5.4, width: 3.2, height: 3.2)).fill()
        }
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = accessibilityDescription
    return image
}

private func statusTitleImage(_ presentation: StatusTitlePresentation) -> NSImage {
    let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
    let attributes: [NSAttributedString.Key: Any] = [
        .foregroundColor: NSColor.black,
        .font: font,
    ]
    let gap = NSAttributedString(string: "  ", attributes: attributes).size().width
    let prefix = presentation.warning ? NSAttributedString(string: "⚠︎ ", attributes: attributes) : nil
    let providerMarkWidth: CGFloat = 20
    let layouts = presentation.segments.map { segment -> (StatusTitleSegment, NSAttributedString, CGFloat) in
        let title = NSAttributedString(string: segment.text, attributes: attributes)
        let markWidth: CGFloat = segment.symbol == nil ? 0 : 18
        return (segment, title, markWidth + title.size().width)
    }
    let contentWidth = (prefix?.size().width ?? 0)
        + providerMarkWidth
        + layouts.reduce(CGFloat(0)) { $0 + $1.2 }
        + gap * CGFloat(max(0, layouts.count - 1))
    let imageSize = NSSize(width: ceil(contentWidth), height: 18)
    let image = NSImage(size: imageSize, flipped: false) { rect in
        var x: CGFloat = 0
        if let prefix = prefix {
            let size = prefix.size()
            prefix.draw(at: NSPoint(x: x, y: floor((rect.height - size.height) / 2)))
            x += size.width
        }
        runtimeMarkImage(
            symbol: presentation.providerSymbol,
            accessibilityDescription: presentation.providerAccessibilityText
        ).draw(in: NSRect(x: x, y: 1, width: 16, height: 16))
        x += providerMarkWidth
        for (index, layout) in layouts.enumerated() {
            let (segment, title, _) = layout
            if index > 0 { x += gap }
            if let symbol = segment.symbol {
                runtimeMarkImage(symbol: symbol, accessibilityDescription: segment.accessibilityText)
                    .draw(in: NSRect(x: x, y: 2, width: 14, height: 14))
                x += 18
            }
            let size = title.size()
            title.draw(at: NSPoint(x: x, y: floor((rect.height - size.height) / 2)))
            x += size.width
        }
        return true
    }
    // The glyphs are a mask. macOS owns the final black/white tint, using the
    // actual menu-bar material rather than this process's effective appearance.
    image.isTemplate = true
    image.accessibilityDescription = presentation.accessibilityTitle
    return image
}

enum Verdict {
    case healthy
    case watch
    case intervene
    case idle
    case disconnected

    var label: String {
        switch self {
        case .healthy: return "Healthy"
        case .watch: return "Watch closely"
        case .intervene: return "Intervene now"
        case .idle: return "Idle"
        case .disconnected: return "Server offline"
        }
    }

    var prefix: String {
        switch self {
        case .healthy: return "TM"
        case .watch: return "TM !"
        case .intervene: return "TM !!"
        case .idle: return "TM idle"
        case .disconnected: return "TM off"
        }
    }

    var color: NSColor {
        switch self {
        case .healthy: return .tokenMeterBlue
        case .watch: return .tokenMeterBlue
        case .intervene: return .tokenMeterBlue
        case .idle: return .tokenMeterBlue
        case .disconnected: return .secondaryLabelColor
        }
    }

    static func fromKey(_ key: String?) -> Verdict? {
        switch key {
        case "healthy": return .healthy
        case "watch": return .watch
        case "intervene": return .intervene
        case "idle": return .idle
        case "disconnected": return .disconnected
        default: return nil
        }
    }

    static func fromLabel(_ label: String?) -> Verdict? {
        switch label {
        case "Healthy": return .healthy
        case "Watch closely": return .watch
        case "Intervene now": return .intervene
        case "Idle": return .idle
        case "Server offline": return .disconnected
        default: return nil
        }
    }
}

enum TitleMetric: String, CaseIterable {
    case cost
    case speed
    case context
    case model
    case limits
    case todaySpend

    var title: String {
        switch self {
        case .cost: return "Cost"
        case .speed: return "Output speed"
        case .context: return "Context"
        case .model: return "Model"
        case .limits: return "Limits"
        case .todaySpend: return "Today spend"
        }
    }
}

enum StatusDisplayMode: String, CaseIterable {
    case automatic
    case text
    case icon

    var title: String {
        switch self {
        case .automatic: return "Automatic"
        case .text: return "Cost + speed text"
        case .icon: return "Icon only"
        }
    }
}

enum MenuBarShortcut: String, CaseIterable {
    case controlOptionT
    case commandOptionT
    case controlOptionM
    case custom
    case off

    var title: String {
        switch self {
        case .controlOptionT: return "⌃⌥T"
        case .commandOptionT: return "⌥⌘T"
        case .controlOptionM: return "⌃⌥M"
        case .custom: return "Custom…"
        case .off: return "Off"
        }
    }

    var keyCode: UInt32? {
        switch self {
        case .controlOptionT, .commandOptionT: return 17 // T on the macOS US keyboard layout.
        case .controlOptionM: return 46 // M on the macOS US keyboard layout.
        case .custom: return nil
        case .off: return nil
        }
    }

    var modifiers: UInt32 {
        switch self {
        case .controlOptionT, .controlOptionM: return UInt32(controlKey | optionKey)
        case .commandOptionT: return UInt32(cmdKey | optionKey)
        case .custom: return 0
        case .off: return 0
        }
    }
}

private enum QuickAction: Int, CaseIterable {
    case dashboard
    case spend
    case tools
    case settings

    var title: String {
        switch self {
        case .dashboard: return "Dashboard"
        case .spend: return "Spend"
        case .tools: return "Tools"
        case .settings: return "Settings"
        }
    }

    var symbol: String {
        switch self {
        case .dashboard: return "rectangle.grid.2x2"
        case .spend: return "chart.bar.xaxis"
        case .tools: return "wrench.and.screwdriver"
        case .settings: return "gearshape"
        }
    }
}

struct QuotaPace {
    var state: String
    var summary: String

    static func fromJSON(_ dict: [String: Any]?) -> QuotaPace? {
        guard let dict = dict, let summary = string(dict["summary"]), !summary.isEmpty else { return nil }
        return QuotaPace(state: string(dict["state"]) ?? "on_pace", summary: summary)
    }
}

struct QuotaWindow {
    var id: String
    var kind: String
    var label: String
    var usedPercent: Double
    var windowSeconds: Int?
    var resetAt: Date?
    var pace: QuotaPace?

    static func fromJSON(_ dict: [String: Any]) -> QuotaWindow? {
        guard let id = string(dict["id"]), !id.isEmpty,
              let usedPercent = optionalDouble(dict["used_percent"])
        else { return nil }
        let duration = optionalDouble(dict["window_seconds"]).map(Int.init)
        let resetAt = optionalDouble(dict["reset_at"]).map(Date.init(timeIntervalSince1970:))
        return QuotaWindow(
            id: id,
            kind: string(dict["kind"]) ?? "extra",
            label: string(dict["label"]) ?? "Quota",
            usedPercent: max(0, min(100, usedPercent)),
            windowSeconds: duration,
            resetAt: resetAt,
            pace: QuotaPace.fromJSON(dict["pace"] as? [String: Any])
        )
    }

    var percentLabel: String { "\(Int(usedPercent.rounded()))%" }

    var resetLabel: String {
        guard let resetAt = resetAt else { return "reset time unavailable" }
        let remaining = resetAt.timeIntervalSinceNow
        if remaining <= 0 { return "reset pending" }
        return "resets in \(formatCompactDuration(remaining))"
    }
}

struct ProviderQuota {
    var id: String
    var label: String
    var status: String
    var plan: String
    var source: String
    var provenance: String
    var ageSeconds: Int?
    var stale: Bool
    var error: String
    var coverageNote: String
    var windows: [QuotaWindow]

    static func fromJSON(_ dict: [String: Any]) -> ProviderQuota? {
        guard let id = string(dict["id"]), !id.isEmpty else { return nil }
        return ProviderQuota(
            id: id,
            label: string(dict["label"]) ?? id.capitalized,
            status: string(dict["status"]) ?? "unavailable",
            plan: string(dict["plan"]) ?? "",
            source: string(dict["source"]) ?? "Provider account",
            provenance: string(dict["provenance"]) ?? "unavailable",
            ageSeconds: optionalDouble(dict["age_seconds"]).map(Int.init),
            stale: bool(dict["stale"]),
            error: string(dict["error"]) ?? "",
            coverageNote: string(dict["coverage_note"]) ?? "",
            windows: (dict["windows"] as? [[String: Any]] ?? []).compactMap(QuotaWindow.fromJSON)
        )
    }

    var fresh: Bool { status == "ok" && !stale }

    var highestWindow: QuotaWindow? {
        windows.max { $0.usedPercent < $1.usedPercent }
    }

    var freshnessLabel: String {
        if stale {
            return ageSeconds.map { "Stale · \(formatCompactDuration(Double($0))) old" } ?? "Stale"
        }
        guard let ageSeconds = ageSeconds else { return status == "loading" ? "Loading" : "Not refreshed" }
        if ageSeconds < 10 { return "Updated now" }
        return "Updated \(formatCompactDuration(Double(ageSeconds))) ago"
    }
}

struct QuotaNotificationState: Codable {
    var lastUsedPercent: Double
    var resetAt: TimeInterval?
    var firedThresholds: Set<Int>
}

struct BudgetScope {
    var id: String
    var label: String
    var spend: Double
    var budget: Double
    var percent: Double
}

struct MonthlyBudget {
    var month: String
    var configured: Bool
    var spend: Double
    var budget: Double
    var percent: Double
    var lowerBound: Bool
    var nativeNotifications: Bool
    var thresholds: [Int]
    var scopes: [BudgetScope]

    static func fromJSON(_ dict: [String: Any]?) -> MonthlyBudget? {
        guard let dict = dict else { return nil }
        let configured = bool(dict["configured"])
        let settings = dict["settings"] as? [String: Any] ?? [:]
        let thresholds = (settings["thresholds"] as? [Any] ?? [])
            .compactMap { optionalDouble($0).map(Int.init) }
        let runtimes = (dict["runtimes"] as? [[String: Any]] ?? []).compactMap { row -> BudgetScope? in
            guard let id = string(row["provider"]) else { return nil }
            let budget = double(row["allocation"])
            let percent = budget > 0 ? (optionalDouble(row["percent"]) ?? 0) * 100 : 0
            return BudgetScope(
                id: id,
                label: string(row["label"]) ?? id.capitalized,
                spend: double(row["spend"]),
                budget: budget,
                percent: percent
            )
        }
        var scopes = runtimes
        if configured {
            scopes.insert(BudgetScope(
                id: "overall",
                label: "Overall",
                spend: double(dict["spend"]),
                budget: double(dict["budget"]),
                percent: double(dict["percent"]) * 100
            ), at: 0)
        }
        return MonthlyBudget(
            month: string(dict["month"]) ?? "",
            configured: configured,
            spend: double(dict["spend"]),
            budget: double(dict["budget"]),
            percent: double(dict["percent"]) * 100,
            lowerBound: bool(dict["lower_bound"]),
            nativeNotifications: settings["native_notifications"] == nil
                ? true : bool(settings["native_notifications"]),
            thresholds: thresholds.isEmpty ? [80, 90, 100] : thresholds,
            scopes: scopes
        )
    }

    var compactLabel: String {
        guard configured else { return "Budget not set" }
        if let scope = exceededRuntimeScopes.first {
            return "\(scope.label) · \(Int(scope.percent.rounded()))%"
        }
        if exceeded { return "Overall · \(Int(percent.rounded()))%" }
        let prefix = lowerBound ? "≥" : ""
        return "\(prefix)\(Int(percent.rounded()))%"
    }

    var exceeded: Bool { configured && percent >= 100 }
    var exceededRuntimeScopes: [BudgetScope] {
        scopes.filter { $0.id != "overall" && $0.percent >= 100 }
    }
    var anyExceeded: Bool { exceeded || !exceededRuntimeScopes.isEmpty }

}

struct BudgetNotificationState: Codable {
    var month: String
    var lastPercent: Double
    var firedThresholds: Set<Int>
}

struct SoftwareUpdateSnapshot {
    var enabled: Bool
    var autoInstall: Bool
    var state: String
    var available: Bool
    var canUpdate: Bool
    var actionToken: String
    var installAllowed: Bool

    static func fromJSON(_ dict: [String: Any]?) -> SoftwareUpdateSnapshot {
        let dict = dict ?? [:]
        let actions = dict["actions"] as? [String: Any] ?? [:]
        return SoftwareUpdateSnapshot(
            enabled: bool(dict["enabled"]),
            autoInstall: bool(dict["auto_install"]),
            state: string(dict["state"]) ?? "waiting",
            available: bool(dict["available"]),
            canUpdate: bool(dict["can_update"]),
            actionToken: string(actions["token"]) ?? "",
            installAllowed: bool(actions["install"])
        )
    }

    static var waiting: SoftwareUpdateSnapshot {
        SoftwareUpdateSnapshot(
            enabled: true,
            autoInstall: true,
            state: "waiting",
            available: false,
            canUpdate: false,
            actionToken: "",
            installAllowed: false
        )
    }

    var isUpdating: Bool { state == "updating" }
    var shouldOfferInstall: Bool {
        enabled && !autoInstall && state == "available" && available && canUpdate
            && installAllowed && !actionToken.isEmpty
    }
    var needsAttention: Bool { enabled && state == "attention" }
}

struct RuntimePresentation {
    var label: String
    var symbol: String

    static func catalog(_ value: Any?) -> [String: RuntimePresentation] {
        guard let rows = value as? [String: [String: Any]] else { return [:] }
        var result: [String: RuntimePresentation] = [:]
        for (runtimeID, row) in rows {
            guard let label = string(row["label"]), !label.isEmpty else { continue }
            result[runtimeID] = RuntimePresentation(
                label: label,
                symbol: string(row["symbol"]) ?? "runtime.generic"
            )
        }
        return result
    }

    var menuSymbolName: String {
        switch symbol {
        case "runtime.codex": return "terminal"
        case "runtime.cursor": return "cursorarrow"
        case "runtime.opencode": return "gearshape.2"
        case "runtime.claude": return "sparkles"
        default: return "circle"
        }
    }
}

struct RecentSession {
    var id: String
    var provider: String
    var label: String
    var name: String
    var providerName: String
    var symbolName: String

    static func fromJSON(
        _ dict: [String: Any],
        catalog: [String: RuntimePresentation]
    ) -> RecentSession? {
        guard let id = string(dict["id"]), !id.isEmpty else { return nil }
        let provider = string(dict["provider"]) ?? "unknown-runtime"
        let rowLabel = string(dict["label"]) ?? ""
        let presentation = catalog[provider] ?? catalog["unknown-runtime"]
        return RecentSession(
            id: id,
            provider: provider,
            label: rowLabel,
            name: string(dict["name"]) ?? "",
            providerName: presentation?.label ?? (rowLabel.isEmpty ? "Unknown Runtime" : rowLabel),
            symbolName: presentation?.menuSymbolName ?? "circle"
        )
    }

    var identifier: String {
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanName.isEmpty { return cleanName }
        return String(id.prefix(12))
    }

    var menuTitle: String {
        let maximumNameLength = 36
        let cleanName = identifier
        let clippedName = cleanName.count > maximumNameLength
            ? String(cleanName.prefix(maximumNameLength - 1)) + "…"
            : cleanName
        return "\(providerName) · \(clippedName)"
    }

}

struct MeterSnapshot {
    var connected: Bool
    var error: String
    var verdict: Verdict
    var verdictDetail: String
    var provider: String
    var model: String
    var project: String
    var session: String
    var pricingNote: String
    var costAvailable: Bool
    var tokensAvailable: Bool
    var cacheAvailable: Bool
    var throughputAvailable: Bool
    var totalCost: Double
    var todaySpendAvailable: Bool
    var todaySpendTotalCost: Double
    var todaySpendEstimated: Bool
    var estimatedCost: Bool
    var estimatedTokens: Bool
    var totalTokens: Int
    var turns: Int
    var outputTokensPerSecond: Double?
    var outputSpeedLive: Bool
    var outputSpeedBasis: String
    var outputSpeedSamples: Int
    var outputSpeedCoverage: Double?
    var cacheInputShare: Double?
    var cacheTotalTokens: Int
    var contextPct: Double?
    var contextTokens: Int
    var contextWindow: Int
    var lastTurnCost: Double
    var idleSeconds: Int
    var ended: Bool
    var activityKind: String
    var activityTitle: String
    var activityDetail: String
    var activityTime: String
    var recommendationLabel: String
    var recommendationDetail: String
    var recommendationSeverity: String
    var topSignal: String
    var contextPulse: [Double]
    var selectedSessionID: String
    var pinnedSession: Bool

    static func disconnected(_ error: String) -> MeterSnapshot {
        MeterSnapshot(
            connected: false,
            error: error,
            verdict: .disconnected,
            verdictDetail: "Token Meter server is not reachable.",
            provider: "Token Meter",
            model: "unknown",
            project: "",
            session: "",
            pricingNote: "",
            costAvailable: false,
            tokensAvailable: false,
            cacheAvailable: false,
            throughputAvailable: false,
            totalCost: 0,
            todaySpendAvailable: false,
            todaySpendTotalCost: 0,
            todaySpendEstimated: false,
            estimatedCost: false,
            estimatedTokens: false,
            totalTokens: 0,
            turns: 0,
            outputTokensPerSecond: nil,
            outputSpeedLive: false,
            outputSpeedBasis: "unavailable",
            outputSpeedSamples: 0,
            outputSpeedCoverage: nil,
            cacheInputShare: nil,
            cacheTotalTokens: 0,
            contextPct: nil,
            contextTokens: 0,
            contextWindow: 0,
            lastTurnCost: 0,
            idleSeconds: 0,
            ended: false,
            activityKind: "offline",
            activityTitle: "Server offline",
            activityDetail: error,
            activityTime: "",
            recommendationLabel: "Start server",
            recommendationDetail: "Run Token Meter to load live log state.",
            recommendationSeverity: "idle",
            topSignal: error,
            contextPulse: [],
            selectedSessionID: "",
            pinnedSession: false
        )
    }

    static func fromJSON(_ dict: [String: Any]) -> MeterSnapshot {
        let source = dict["source"] as? [String: Any] ?? [:]
        let context = dict["context"] as? [String: Any] ?? [:]
        let cache = dict["cache"] as? [String: Any] ?? [:]
        let availability = dict["availability"] as? [String: Any] ?? [:]
        let insights = dict["insights"] as? [[String: Any]] ?? []

        let provider = string(source["label"]) ?? string(dict["provider"]) ?? "Token Meter"
        let model = string(dict["model"]) ?? string(source["model"]) ?? "unknown"
        let project = string(source["project"]) ?? string(dict["project"]) ?? ""
        let session = string(dict["session"]) ?? string(source["id"]) ?? ""
        let pricingNote = string(source["pricing_note"]) ?? ""
        let costAvailable = metricAvailable(availability, "cost")
        let tokensAvailable = metricAvailable(availability, "tokens")
        let cacheAvailable = metricAvailable(availability, "cache")
        let throughputAvailable = metricAvailable(availability, "throughput")
        let totalCost = double(dict["total_cost"])
        let todaySpend = dict["today_spend"] as? [String: Any] ?? [:]
        let todaySpendAvailable = bool(todaySpend["available"])
        let todaySpendTotalCost = double(todaySpend["total_cost"])
        let todaySpendEstimated = bool(todaySpend["estimated"])
        let estimatedCost = bool(dict["cost_approx"]) || bool(source["approximate_cost"])
        let estimatedTokens = bool(source["token_estimate"])
        let totalTokens = int(dict["total_tokens"])
        let turns = int(dict["turns"])
        let throughput = dict["throughput"] as? [String: Any] ?? [:]
        let liveThroughput = dict["live_throughput"] as? [String: Any] ?? [:]
        let outputSpeedLive = throughputAvailable && bool(liveThroughput["available"])
        let selectedThroughput = outputSpeedLive ? liveThroughput : throughput
        let outputTokensPerSecond = throughputAvailable && bool(selectedThroughput["available"])
            ? optionalDouble(selectedThroughput["output_tps"])
            : nil
        let outputSpeedBasis = string(selectedThroughput["basis"]) ?? "unavailable"
        let outputSpeedSamples = outputSpeedLive
            ? int(liveThroughput["completed_steps"])
            : int(throughput["sample_count"])
        let outputSpeedCoverage = throughputAvailable && !outputSpeedLive && bool(throughput["available"])
            ? optionalDouble(throughput["timing_coverage"])
            : nil
        let cacheInputShare = cacheAvailable ? optionalDouble(cache["input_share"]) : nil
        let cacheTotalTokens = int(cache["total"])
        let contextPct = optionalDouble(context["latest_pct"])
        let contextTokens = int(context["latest"])
        let contextWindow = int(context["window"])
        let lastTurnCost = double(dict["last_turn_cost"])
        let idleSeconds = int(dict["idle_s"])
        let ended = bool(dict["ended"])
        let activity = dict["activity"] as? [String: Any] ?? [:]
        let activityKind = string(activity["kind"]) ?? "activity"
        let activityTitle = string(activity["title"]) ?? "Waiting for activity"
        let activityDetail = string(activity["detail"]) ?? ""
        let activityTime = string(activity["time"]) ?? ""
        let recommendation = dict["recommendation"] as? [String: Any] ?? [:]
        let recommendationLabel = string(recommendation["label"]) ?? "Let it run"
        let recommendationDetail = string(recommendation["detail"]) ?? "No immediate intervention needed."
        let recommendationSeverity = string(recommendation["severity"]) ?? "good"
        let verdictInfo = dict["verdict"] as? [String: Any] ?? [:]
        let serverVerdict = Verdict.fromKey(string(verdictInfo["key"])) ?? Verdict.fromLabel(string(verdictInfo["label"]))
        let serverVerdictDetail = string(verdictInfo["detail"])

        let warn = insights.first { string($0["kind"]) == "warn" }
        let firstInsight = warn ?? insights.first
        let topSignal = string(firstInsight?["text"]) ?? "No warnings yet."
        let contextPulse = (dict["context_pulse"] as? [Any] ?? []).compactMap { value -> Double? in
            guard let value = optionalDouble(value) else { return nil }
            return max(0, min(1, value))
        }
        let selection = dict["selection"] as? [String: Any] ?? [:]
        let selectedSessionID = string(selection["selected_id"]) ?? string(source["id"]) ?? ""
        let pinnedSession = bool(selection["pinned"])

        let verdict: Verdict
        if let serverVerdict = serverVerdict {
            verdict = serverVerdict
        } else if ended {
            verdict = .idle
        } else if (contextPct ?? 0) >= 0.85 {
            verdict = .intervene
        } else if costAvailable && lastTurnCost >= 0.50 {
            verdict = .intervene
        } else if (contextPct ?? 0) >= 0.70 || recommendationSeverity == "warn" {
            verdict = .watch
        } else {
            verdict = .healthy
        }

        return MeterSnapshot(
            connected: true,
            error: "",
            verdict: verdict,
            verdictDetail: serverVerdictDetail ?? MeterSnapshot.verdictFallbackDetail(
                verdict,
                contextPct: contextPct,
                lastTurnCost: lastTurnCost,
                costAvailable: costAvailable
            ),
            provider: provider,
            model: model,
            project: project,
            session: session,
            pricingNote: pricingNote,
            costAvailable: costAvailable,
            tokensAvailable: tokensAvailable,
            cacheAvailable: cacheAvailable,
            throughputAvailable: throughputAvailable,
            totalCost: totalCost,
            todaySpendAvailable: todaySpendAvailable,
            todaySpendTotalCost: todaySpendTotalCost,
            todaySpendEstimated: todaySpendEstimated,
            estimatedCost: estimatedCost,
            estimatedTokens: estimatedTokens,
            totalTokens: totalTokens,
            turns: turns,
            outputTokensPerSecond: outputTokensPerSecond,
            outputSpeedLive: outputSpeedLive,
            outputSpeedBasis: outputSpeedBasis,
            outputSpeedSamples: outputSpeedSamples,
            outputSpeedCoverage: outputSpeedCoverage,
            cacheInputShare: cacheInputShare,
            cacheTotalTokens: cacheTotalTokens,
            contextPct: contextPct,
            contextTokens: contextTokens,
            contextWindow: contextWindow,
            lastTurnCost: lastTurnCost,
            idleSeconds: idleSeconds,
            ended: ended,
            activityKind: activityKind,
            activityTitle: activityTitle,
            activityDetail: activityDetail,
            activityTime: activityTime,
            recommendationLabel: recommendationLabel,
            recommendationDetail: recommendationDetail,
            recommendationSeverity: recommendationSeverity,
            topSignal: topSignal,
            contextPulse: contextPulse,
            selectedSessionID: selectedSessionID,
            pinnedSession: pinnedSession
        )
    }

    static func verdictFallbackDetail(_ verdict: Verdict, contextPct: Double?, lastTurnCost: Double, costAvailable: Bool) -> String {
        let pct = Int(((contextPct ?? 0) * 100).rounded())
        switch verdict {
        case .healthy:
            return "Context is \(pct)% and no operational warning needs intervention."
        case .watch:
            return "Context is \(pct)% or an operational warning is active."
        case .intervene:
            if (contextPct ?? 0) >= 0.85 {
                return "Context is \(pct)% of the model window; compact now."
            }
            if costAvailable {
                return "Last execution cost \(formatMoney(lastTurnCost)); review the spike before continuing."
            }
            return "An operational warning needs intervention."
        case .idle:
            return "This is a frozen log view; return to live to follow newest activity."
        case .disconnected:
            return "Token Meter server is not reachable."
        }
    }

    var menuTitle: String {
        if !connected { return "Start Token Meter to see live cost." }
        return provider + projectSuffix
    }

    var projectSuffix: String {
        let trimmed = project.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return "" }
        return " - " + URL(fileURLWithPath: trimmed.replacingOccurrences(of: "~", with: NSHomeDirectory())).lastPathComponent
    }

    var statusTitle: String {
        if !connected { return verdict.prefix }
        return "\(costLabel) · \(contextLabel) · \(outputSpeedLabel) · \(model)"
    }

    var costLabel: String { costAvailable ? "\(formatMoney(totalCost))\(estimatedCost ? " est" : "")" : "--" }

    var menuBarCostLabel: String {
        costAvailable ? String(format: "$%.0f", totalCost) : "--"
    }

    var menuBarTodaySpendLabel: String {
        guard todaySpendAvailable else { return "--" }
        return "\(String(format: "$%.0f", todaySpendTotalCost))\(todaySpendEstimated ? " est" : "")"
    }

    var contextLabel: String {
        guard let pct = contextPct else { return "--% ctx" }
        return "\(Int((pct * 100).rounded()))% ctx"
    }

    var menuBarContextLabel: String {
        guard let pct = contextPct else { return "--%" }
        return "\(Int((pct * 100).rounded()))%"
    }

    var cacheLabel: String {
        guard cacheAvailable else { return "unavailable" }
        guard cacheTotalTokens > 0 else { return "no cache yet" }
        let share = Int(((cacheInputShare ?? 0) * 100).rounded())
        return "\(share)% input cached - \(formatCompactInt(cacheTotalTokens))"
    }

    var outputSpeedLabel: String {
        guard let rate = outputTokensPerSecond, rate > 0 else { return "-- tok/s" }
        return "\(formatTokenRate(rate)) tok/s\(estimatedTokens ? " est" : "")"
    }

    var menuBarOutputSpeedLabel: String {
        guard let rate = outputTokensPerSecond, rate > 0 else { return "-- tok/s" }
        return "\(String(format: "%.0f", rate)) tok/s"
    }

    var idleLabel: String {
        if ended { return "pinned log" }
        if idleSeconds < 60 { return "live - \(idleSeconds)s idle" }
        return "live - \(idleSeconds / 60)m idle"
    }

}

private func contextSignalColor(_ value: Double?) -> NSColor {
    guard let value = value else { return .secondaryLabelColor }
    if value >= 0.85 { return .systemOrange }
    if value >= 0.70 { return .systemYellow }
    return .tokenMeterBlue
}

private func providerSymbol(_ provider: String) -> String {
    switch provider.lowercased() {
    case "codex": return "terminal"
    case "cursor": return "cursorarrow"
    case "opencode": return "gearshape.2"
    default: return "sparkles"
    }
}

final class TokenMeterMenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let stateURL = tokenMeterMenubarURL
    private let dashboardURL = tokenMeterDashboardURL
    private let menu = NSMenu(title: "Token Meter")
    private let menuWidth: CGFloat = 330
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var pinnedSessionID = tokenMeterDefaults.string(forKey: pinnedSessionDefaultsKey)
    private var recentSessions: [RecentSession] = []
    private var runtimeCatalog: [String: RuntimePresentation] = [:]
    private var providerQuotas: [ProviderQuota] = []
    private var statusDisplayMode = StatusDisplayMode(
        rawValue: tokenMeterDefaults.string(forKey: statusDisplayModeDefaultsKey) ?? StatusDisplayMode.text.rawValue
    ) ?? .text
    private var globalShortcut = MenuBarShortcut(
        rawValue: tokenMeterDefaults.string(forKey: globalShortcutDefaultsKey) ?? ""
    ) ?? .controlOptionT
    private var customShortcutKeyCode: UInt32? = {
        guard tokenMeterDefaults.object(forKey: customShortcutKeyCodeDefaultsKey) != nil else { return nil }
        return UInt32(tokenMeterDefaults.integer(forKey: customShortcutKeyCodeDefaultsKey))
    }()
    private var customShortcutModifiers: UInt32 = UInt32(
        tokenMeterDefaults.integer(forKey: customShortcutModifiersDefaultsKey)
    )
    private var globalHotKey: EventHotKeyRef?
    private var globalHotKeyHandler: EventHandlerRef?
    private var titleMetrics: Set<TitleMetric> = {
        var metrics: Set<TitleMetric>
        let savedMetrics = Set(
            (tokenMeterDefaults.array(forKey: titleMetricsDefaultsKey) as? [String] ?? [])
                .compactMap(TitleMetric.init(rawValue:))
        )
        if !savedMetrics.isEmpty {
            metrics = savedMetrics
        } else if tokenMeterDefaults.string(forKey: titleModeDefaultsKey) == "limits" {
            metrics = [.limits]
        } else {
            metrics = [.cost, .speed]
        }
        if tokenMeterDefaults.object(forKey: todaySpendTitlePreferenceDefaultsKey) == nil {
            metrics.insert(.todaySpend)
            tokenMeterDefaults.set(
                TitleMetric.allCases.filter(metrics.contains).map(\.rawValue),
                forKey: titleMetricsDefaultsKey
            )
            tokenMeterDefaults.set(true, forKey: todaySpendTitlePreferenceDefaultsKey)
        }
        return metrics
    }()
    private var quotaAlertsEnabled: Bool = {
        if tokenMeterDefaults.object(forKey: quotaAlertsEnabledDefaultsKey) == nil { return true }
        return tokenMeterDefaults.bool(forKey: quotaAlertsEnabledDefaultsKey)
    }()
    private var quotaAlertThreshold: Int = {
        let saved = tokenMeterDefaults.integer(forKey: quotaAlertThresholdDefaultsKey)
        return [80, 90, 95].contains(saved) ? saved : 80
    }()
    private var quotaNotificationStates: [String: QuotaNotificationState] = {
        guard let data = tokenMeterDefaults.data(forKey: quotaNotificationStatesDefaultsKey),
              let states = try? JSONDecoder().decode([String: QuotaNotificationState].self, from: data)
        else { return [:] }
        return states
    }()
    private var budgetNotificationStates: [String: BudgetNotificationState] = {
        guard let data = tokenMeterDefaults.data(forKey: budgetNotificationStatesDefaultsKey),
              let states = try? JSONDecoder().decode([String: BudgetNotificationState].self, from: data)
        else { return [:] }
        return states
    }()
    private var budgetExceededNotificationMonths = Set(
        tokenMeterDefaults.stringArray(forKey: budgetExceededMonthsDefaultsKey) ?? []
    )
    private var quotaObservationEstablished = false
    private var menuIsOpen = false
    private var menuRefreshPending = false
    private var snapshot = MeterSnapshot.disconnected("Waiting for http://127.0.0.1:8722/menubar")
    private var monthlyBudget: MonthlyBudget?
    private var softwareUpdate = SoftwareUpdateSnapshot.waiting

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if tokenMeterDefaults.object(forKey: statusItemPreferredPositionDefaultsKey) == nil {
            tokenMeterDefaults.set(statusItemInitialPreferredPosition, forKey: statusItemPreferredPositionDefaultsKey)
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.autosaveName = statusItemAutosaveName
        statusItem.menu = menu
        menu.delegate = self
        if let button = statusItem.button {
            button.image = splunkChevronImage()
            button.imagePosition = .imageOnly
            button.contentTintColor = .tokenMeterBlue
        }
        registerGlobalHotKey()
        rebuildMenu()
        fetchState()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.fetchState()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        unregisterGlobalHotKey()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuildMenu()
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuIsOpen = true
    }

    func menuDidClose(_ menu: NSMenu) {
        menuIsOpen = false
        if menuRefreshPending {
            menuRefreshPending = false
            rebuildMenu()
        }
    }

    private func fetchState() {
        let requestedSessionID = pinnedSessionID
        let requestURL = menubarRequestURL(sessionID: requestedSessionID)
        var request = URLRequest(
            url: requestURL,
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: 5.0
        )
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        request.setValue("no-cache", forHTTPHeaderField: "Pragma")
        URLSession.shared.dataTask(with: request) { [weak self] data, _, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard requestedSessionID == self.pinnedSessionID else { return }
                if let error = error {
                    if self.softwareUpdate.isUpdating {
                        self.refreshMenu()
                        return
                    }
                    self.snapshot = MeterSnapshot.disconnected(error.localizedDescription)
                    self.refreshMenu()
                    return
                }
                guard
                    let data = data,
                    let obj = try? JSONSerialization.jsonObject(with: data),
                    let dict = obj as? [String: Any]
                else {
                    if self.softwareUpdate.isUpdating {
                        self.refreshMenu()
                        return
                    }
                    self.snapshot = MeterSnapshot.disconnected("Token Meter returned unreadable state.")
                    self.refreshMenu()
                    return
                }
                let selection = dict["selection"] as? [String: Any] ?? [:]
                if bool(selection["missing"]) {
                    self.persistPinnedSession(nil)
                }
                self.runtimeCatalog = RuntimePresentation.catalog(dict["runtime_catalog"])
                self.recentSessions = (dict["recent_sessions"] as? [[String: Any]] ?? [])
                    .compactMap { RecentSession.fromJSON($0, catalog: self.runtimeCatalog) }
                self.providerQuotas = (dict["provider_quotas"] as? [[String: Any]] ?? [])
                    .compactMap(ProviderQuota.fromJSON)
                self.snapshot = MeterSnapshot.fromJSON(dict)
                self.monthlyBudget = MonthlyBudget.fromJSON(dict["budget"] as? [String: Any])
                self.softwareUpdate = SoftwareUpdateSnapshot.fromJSON(
                    dict["software_update"] as? [String: Any]
                )
                self.evaluateQuotaNotifications()
                self.evaluateBudgetNotifications()
                self.refreshMenu()
            }
        }.resume()
    }

    private func menubarRequestURL(sessionID: String?) -> URL {
        guard let sessionID = sessionID, !sessionID.isEmpty,
              var components = URLComponents(url: stateURL, resolvingAgainstBaseURL: false)
        else { return stateURL }
        components.queryItems = [URLQueryItem(name: "session", value: sessionID)]
        return components.url ?? stateURL
    }

    private func refreshMenu() {
        updateStatusTitle()
        guard !menuIsOpen else {
            menuRefreshPending = true
            return
        }
        rebuildMenu()
    }

    private func openMenu() {
        rebuildMenu()
        NSApp.activate(ignoringOtherApps: true)
        statusItem.button?.performClick(nil)
    }

    private func rebuildMenu() {
        updateStatusTitle()
        menu.removeAllItems()

        addHeader()
        menu.addItem(.separator())
        addQuickActions()
        addSoftwareUpdateItem()
        menu.addItem(.separator())

        addSessionPicker()
        menu.addItem(.separator())

        if !snapshot.connected {
            addConnectionRow()
        }

        let limitsItem = NSMenuItem(title: "Provider limits (Beta)", action: nil, keyEquivalent: "")
        limitsItem.image = menuSymbol("gauge.with.dots.needle.50percent", description: "Provider limits (Beta)")
        limitsItem.submenu = makeLimitsMenu()
        menu.addItem(limitsItem)

        let budgetsItem = NSMenuItem(title: "Budgets", action: nil, keyEquivalent: "")
        let budgetExceeded = monthlyBudget?.anyExceeded == true
        budgetsItem.image = menuSymbol(
            budgetExceeded ? "exclamationmark.triangle.fill" : "calendar",
            description: budgetExceeded ? "Budget alert" : "Budgets"
        )
        budgetsItem.submenu = makeBudgetsMenu()
        menu.addItem(budgetsItem)

        let settingsItem = NSMenuItem(title: "Menu bar settings", action: nil, keyEquivalent: "")
        settingsItem.image = menuSymbol("gearshape", description: "Menu bar settings")
        settingsItem.submenu = makeSettingsMenu()
        menu.addItem(settingsItem)

        let enterpriseTokenomicsItem = NSMenuItem(title: "Get Enterprise Tokenomics", action: #selector(openEnterpriseTokenomics), keyEquivalent: "")
        enterpriseTokenomicsItem.image = menuSymbol("building.2", description: "Get Enterprise Tokenomics")
        enterpriseTokenomicsItem.target = self
        menu.addItem(enterpriseTokenomicsItem)

        menu.addItem(.separator())
        addAction("Quit Token Meter", #selector(quit))
    }

    private func addQuickActions() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 34))
        let actions = QuickAction.allCases
        let font = NSFont.systemFont(ofSize: 11, weight: .medium)
        let symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 10.5, weight: .medium)
        let images = actions.map { action in
            menuSymbol(action.symbol, description: action.title)?
                .withSymbolConfiguration(symbolConfiguration)
        }
        let control = NSSegmentedControl(
            labels: actions.map(\.title),
            trackingMode: .momentary,
            target: self,
            action: #selector(performQuickAction(_:))
        )
        control.frame = NSRect(x: 14, y: 4, width: menuWidth - 28, height: 26)
        control.segmentStyle = .separated
        control.controlSize = .small
        control.font = font
        control.setAccessibilityLabel("Quick actions")

        let preferredWidths = actions.enumerated().map { index, action in
            let titleWidth = ceil(
                (action.title as NSString).size(withAttributes: [.font: font]).width
            )
            let imageWidth = ceil(images[index]?.size.width ?? 11)
            return max(58, titleWidth + imageWidth + 24)
        }
        let preferredTotal = preferredWidths.reduce(0, +)
        let sharedBreathingRoom = max(
            0,
            (control.frame.width - preferredTotal) / CGFloat(actions.count)
        )
        var segmentWidths = preferredWidths.map { floor($0 + sharedBreathingRoom) }
        if preferredTotal > control.frame.width {
            let scale = control.frame.width / preferredTotal
            segmentWidths = preferredWidths.map { floor($0 * scale) }
        }
        if let last = segmentWidths.indices.last {
            segmentWidths[last] += control.frame.width - segmentWidths.reduce(0, +)
        }

        for action in actions {
            let segment = action.rawValue
            control.setImage(images[segment], forSegment: segment)
            control.setImageScaling(.scaleProportionallyDown, forSegment: segment)
            control.setWidth(segmentWidths[segment], forSegment: segment)
        }
        view.addSubview(control)
        addViewItem(view)
    }

    private func addSoftwareUpdateItem() {
        let item: NSMenuItem
        if softwareUpdate.shouldOfferInstall {
            item = NSMenuItem(
                title: "New update available",
                action: #selector(installSoftwareUpdate),
                keyEquivalent: ""
            )
            item.image = menuSymbol("arrow.down.circle", description: "Install Token Meter update")
        } else if softwareUpdate.isUpdating {
            item = NSMenuItem(title: "Updating Token Meter...", action: nil, keyEquivalent: "")
            item.image = menuSymbol("arrow.triangle.2.circlepath", description: "Updating Token Meter")
            item.isEnabled = false
        } else if softwareUpdate.needsAttention {
            item = NSMenuItem(
                title: "Update needs attention",
                action: #selector(openUpdateSettings),
                keyEquivalent: ""
            )
            item.image = menuSymbol("exclamationmark.triangle", description: "Update needs attention")
        } else {
            return
        }
        item.target = self
        menu.addItem(item)
    }

    private func addHeader() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 46))
        let selectedSession = recentSessions.first { $0.id == snapshot.selectedSessionID }
        let titleText: String
        let subtitleText: String
        if snapshot.connected {
            titleText = selectedSession?.identifier ?? snapshot.menuTitle
            let following = pinnedSessionID == nil ? "Following latest" : "Following this session"
            subtitleText = "\(snapshot.provider) · \(following) · \(snapshot.idleLabel)"
        } else {
            titleText = "Token Meter"
            subtitleText = "Server offline"
        }
        let title = menuLabel(
            titleText,
            frame: NSRect(x: 14, y: 22, width: menuWidth - 28, height: 18),
            font: .systemFont(ofSize: 13, weight: .semibold),
            color: .labelColor
        )
        let subtitle = menuLabel(
            subtitleText,
            frame: NSRect(x: 14, y: 4, width: menuWidth - 28, height: 16),
            font: .systemFont(ofSize: 11.5, weight: .regular),
            color: .secondaryLabelColor
        )
        view.addSubview(title)
        view.addSubview(subtitle)
        addViewItem(view)
    }

    private func addSessionPicker() {
        let heading = NSMenuItem(title: "Recent sessions", action: nil, keyEquivalent: "")
        heading.isEnabled = false
        menu.addItem(heading)

        let followLatest = NSMenuItem(title: "Follow latest", action: #selector(followLatest), keyEquivalent: "")
        followLatest.target = self
        followLatest.state = pinnedSessionID == nil ? .on : .off
        followLatest.image = menuSymbol("arrow.triangle.2.circlepath", description: "Follow latest")
        menu.addItem(followLatest)

        for session in recentSessions.prefix(5) {
            let item = NSMenuItem(title: session.menuTitle, action: #selector(pinSession(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = session.id
            item.state = pinnedSessionID == session.id ? .on : .off
            item.image = menuSymbol(session.symbolName, description: session.providerName)
            menu.addItem(item)
        }

        if recentSessions.isEmpty {
            let empty = NSMenuItem(title: "No recent sessions", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
        }
    }

    private func makeLimitsMenu() -> NSMenu {
        let limitsMenu = NSMenu(title: "Provider limits")
        guard !providerQuotas.isEmpty else {
            let empty = NSMenuItem(title: "No provider limits reported", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            limitsMenu.addItem(empty)
            return limitsMenu
        }

        for provider in providerQuotas {
            let summary = provider.highestWindow.map { " · \($0.percentLabel) max" } ?? ""
            let providerItem = NSMenuItem(
                title: "\(provider.label)\(summary)",
                action: nil,
                keyEquivalent: ""
            )
            providerItem.image = menuSymbol(providerSymbol(provider.id), description: provider.label)
            let providerMenu = NSMenu(title: provider.label)
            if provider.windows.isEmpty {
                let unavailable = provider.error.isEmpty ? provider.freshnessLabel : provider.error
                let item = NSMenuItem(title: unavailable, action: nil, keyEquivalent: "")
                item.isEnabled = false
                providerMenu.addItem(item)
            } else {
                for window in provider.windows {
                    let item = NSMenuItem(
                        title: "\(window.label) · \(window.percentLabel) used",
                        action: nil,
                        keyEquivalent: ""
                    )
                    item.isEnabled = false
                    providerMenu.addItem(item)
                }
            }
            providerItem.submenu = providerMenu
            limitsMenu.addItem(providerItem)
        }
        return limitsMenu
    }

    private func makeBudgetsMenu() -> NSMenu {
        let budgetsMenu = NSMenu(title: "Budgets")
        guard let budget = monthlyBudget else {
            let unavailable = NSMenuItem(title: "Budget data unavailable", action: nil, keyEquivalent: "")
            unavailable.isEnabled = false
            budgetsMenu.addItem(unavailable)
            return budgetsMenu
        }

        let overallTitle = budget.configured
            ? "Overall · \(formatMoney(budget.spend)) of \(formatMoney(budget.budget)) · \(Int(budget.percent.rounded()))% used"
            : "Overall · Not set"
        let overall = NSMenuItem(
            title: overallTitle,
            action: #selector(openBudgetSettings),
            keyEquivalent: ""
        )
        overall.target = self
        overall.image = menuSymbol(
            budget.anyExceeded ? "exclamationmark.triangle.fill" : "calendar",
            description: budget.anyExceeded ? "Budget alert" : "Overall budget"
        )
        budgetsMenu.addItem(overall)

        if !budget.scopes.isEmpty {
            budgetsMenu.addItem(.separator())
        }

        for scope in budget.scopes where scope.id != "overall" {
            let title = scope.budget > 0
                ? "\(scope.label) · \(formatMoney(scope.spend)) of \(formatMoney(scope.budget)) · \(Int(scope.percent.rounded()))% used"
                : "\(scope.label) · Not set"
            let item = NSMenuItem(
                title: title,
                action: #selector(openBudgetSettings),
                keyEquivalent: ""
            )
            item.target = self
            item.image = menuSymbol(providerSymbol(scope.id), description: scope.label)
            budgetsMenu.addItem(item)
        }
        return budgetsMenu
    }

    private func addMetricRow(
        _ name: String,
        _ value: String,
        valueColor: NSColor = .labelColor
    ) {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 25))
        let nameLabel = menuLabel(
            name,
            frame: NSRect(x: 14, y: 4, width: 90, height: 17),
            font: .systemFont(ofSize: 12, weight: .regular),
            color: .secondaryLabelColor
        )
        let valueLabel = menuLabel(
            value,
            frame: NSRect(x: 104, y: 3, width: menuWidth - 118, height: 18),
            font: .monospacedDigitSystemFont(ofSize: 12.5, weight: .semibold),
            color: valueColor
        )
        valueLabel.alignment = .right
        view.addSubview(nameLabel)
        view.addSubview(valueLabel)
        addViewItem(view)
    }

    private func addConnectionRow() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: menuWidth, height: 42))
        let title = menuLabel(
            "Token Meter is not reachable",
            frame: NSRect(x: 14, y: 22, width: menuWidth - 28, height: 16),
            font: .systemFont(ofSize: 12, weight: .semibold),
            color: .labelColor
        )
        let detail = menuLabel(
            snapshot.error,
            frame: NSRect(x: 14, y: 4, width: menuWidth - 28, height: 16),
            font: .systemFont(ofSize: 11.5, weight: .regular),
            color: .secondaryLabelColor
        )
        view.addSubview(title)
        view.addSubview(detail)
        addViewItem(view)
    }

    private func menuLabel(_ text: String, frame: NSRect, font: NSFont, color: NSColor) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.frame = frame
        field.font = font
        field.textColor = color
        field.lineBreakMode = .byTruncatingTail
        field.maximumNumberOfLines = 1
        field.isSelectable = false
        field.allowsDefaultTighteningForTruncation = true
        return field
    }

    private func addViewItem(_ view: NSView) {
        let item = NSMenuItem()
        item.view = view
        menu.addItem(item)
    }

    private func addAction(_ title: String, _ action: Selector, enabled: Bool = true) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        item.isEnabled = enabled
        menu.addItem(item)
    }

    private func menuSymbol(_ name: String, description: String) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: description)
        image?.isTemplate = true
        return image
    }

    private var activeShortcutKeyCode: UInt32? {
        globalShortcut == .custom ? customShortcutKeyCode : globalShortcut.keyCode
    }

    private var activeShortcutModifiers: UInt32 {
        globalShortcut == .custom ? customShortcutModifiers : globalShortcut.modifiers
    }

    private func registerGlobalHotKey() {
        unregisterGlobalHotKey()
        guard let keyCode = activeShortcutKeyCode else { return }

        var eventSpec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let userData = Unmanaged.passUnretained(self).toOpaque()
        let handlerStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, _, userData in
                guard let userData = userData else { return noErr }
                let delegate = Unmanaged<TokenMeterMenuBar>.fromOpaque(userData).takeUnretainedValue()
                DispatchQueue.main.async {
                    delegate.openMenu()
                }
                return noErr
            },
            1,
            &eventSpec,
            userData,
            &globalHotKeyHandler
        )
        guard handlerStatus == noErr else { return }

        let hotKeyID = EventHotKeyID(signature: 0x544D4854, id: 1)
        let registrationStatus = RegisterEventHotKey(
            keyCode,
            activeShortcutModifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &globalHotKey
        )
        if registrationStatus != noErr {
            unregisterGlobalHotKey()
        }
    }

    private func unregisterGlobalHotKey() {
        if let globalHotKey = globalHotKey {
            UnregisterEventHotKey(globalHotKey)
            self.globalHotKey = nil
        }
        if let globalHotKeyHandler = globalHotKeyHandler {
            RemoveEventHandler(globalHotKeyHandler)
            self.globalHotKeyHandler = nil
        }
    }

    func runMenuSmoke() throws {
        let originalTitleMetrics = titleMetrics
        let originalSoftwareUpdate = softwareUpdate
        defer {
            titleMetrics = originalTitleMetrics
            softwareUpdate = originalSoftwareUpdate
            timer?.invalidate()
            menu.cancelTracking()
            statusItem = nil
        }
        titleMetrics = Set(TitleMetric.allCases)
        let smokeData = try Data(contentsOf: stateURL)
        let smokeObject = try JSONSerialization.jsonObject(with: smokeData)
        guard let smokePayload = smokeObject as? [String: Any] else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 12,
                userInfo: [NSLocalizedDescriptionKey: "Native menu smoke payload was not a JSON object."]
            )
        }
        snapshot = MeterSnapshot.fromJSON(smokePayload)
        monthlyBudget = MonthlyBudget.fromJSON(smokePayload["budget"] as? [String: Any])
        softwareUpdate = SoftwareUpdateSnapshot.fromJSON(
            smokePayload["software_update"] as? [String: Any]
        )
        providerQuotas = (smokePayload["provider_quotas"] as? [[String: Any]] ?? [])
            .compactMap(ProviderQuota.fromJSON)
        runtimeCatalog = RuntimePresentation.catalog(smokePayload["runtime_catalog"])
        recentSessions = (smokePayload["recent_sessions"] as? [[String: Any]] ?? [])
            .compactMap { RecentSession.fromJSON($0, catalog: runtimeCatalog) }
        rebuildMenu()
        let quickActions = menu.items
            .compactMap(\.view)
            .flatMap(\.subviews)
            .compactMap { $0 as? NSSegmentedControl }
            .first
        let quickActionLabels = (0..<(quickActions?.segmentCount ?? 0)).compactMap {
            quickActions?.label(forSegment: $0)
        }
        let quickActionWidths = (0..<(quickActions?.segmentCount ?? 0)).compactMap {
            quickActions?.width(forSegment: $0)
        }
        let settingsMenu = makeSettingsMenu()
        let menuBarSettingsIndex = menu.items.firstIndex { $0.title == "Menu bar settings" }
        let enterpriseTokenomicsIndex = menu.items.firstIndex { $0.title == "Get Enterprise Tokenomics" }
        guard quickActions?.frame.height == 26,
              quickActionLabels == ["Dashboard", "Spend", "Tools", "Settings"],
              quickActionWidths.count == 4,
              quickActionWidths[QuickAction.dashboard.rawValue] > quickActionWidths[QuickAction.spend.rawValue],
              quickActionWidths[QuickAction.settings.rawValue] > quickActionWidths[QuickAction.tools.rawValue],
              abs(quickActionWidths.reduce(0, +) - (quickActions?.frame.width ?? 0)) < 1,
              !menu.items.contains(where: { ["Open Dashboard", "More"].contains($0.title) }),
              settingsMenu.items.contains(where: {
                  $0.title == "Open Settings" && $0.action == #selector(openSettings)
              }),
              !settingsMenu.items.contains(where: { $0.title == "Open Trace" }),
              settingsMenu.items.contains(where: {
                  $0.title == "Model Prices" && $0.action == #selector(openModelPrices)
              }),
              menu.items.contains(where: {
                  $0.title == "Menu bar settings" && $0.submenu != nil
              }),
              let menuBarSettingsIndex,
              let enterpriseTokenomicsIndex,
              enterpriseTokenomicsIndex == menuBarSettingsIndex + 1,
              menu.items[enterpriseTokenomicsIndex].action == #selector(openEnterpriseTokenomics),
              !settingsMenu.items.contains(where: {
                  ["Open Spend", "Open Tools & Skills"].contains($0.title)
              })
        else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 17,
                userInfo: [NSLocalizedDescriptionKey: "Native quick actions were not compact or complete."]
            )
        }
        func viewContainsToolTip(_ view: NSView) -> Bool {
            view.toolTip != nil || view.subviews.contains(where: viewContainsToolTip)
        }
        func menuContainsToolTip(_ candidate: NSMenu) -> Bool {
            candidate.items.contains { item in
                item.toolTip != nil
                    || item.view.map(viewContainsToolTip) == true
                    || item.submenu.map(menuContainsToolTip) == true
            }
        }
        guard statusItem.button?.toolTip == nil, !menuContainsToolTip(menu) else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 16,
                userInfo: [NSLocalizedDescriptionKey: "Native menu still exposes hover tooltips."]
            )
        }
        let expectedPresentation = selectedStatusTitlePresentation()
        let expectedTitle = expectedPresentation.accessibilityTitle
        let expectedTitleSegments = expectedTitle.components(separatedBy: "  ")
        let expectedContextPercent = snapshot.contextPct.map {
            "\(Int(($0 * 100).rounded()))%"
        } ?? "--%"
        let renderedTitle = statusItem.button?.accessibilityValue() as? String
        let limitSegment = expectedPresentation.segments.first { $0.symbol != nil }
        let expectedProviderSymbol = runtimeCatalog[snapshot.provider]?.symbol ?? "runtime.generic"
        titleMetrics = [.cost, .speed, .context]
        let presentationWithoutLimits = selectedStatusTitlePresentation()
        titleMetrics = Set(TitleMetric.allCases)

        guard statusItem.menu === menu,
              expectedTitleSegments.count >= 4,
              expectedTitleSegments.last == "· \(snapshot.menuBarTodaySpendLabel)",
              expectedTitleSegments.last?.hasPrefix("· ") == true,
              !expectedTitleSegments[0].contains("."),
              !expectedTitleSegments[1].contains("."),
              !expectedTitleSegments[0].contains(" est"),
              !expectedTitleSegments[1].contains(" est"),
              expectedTitleSegments.contains(expectedContextPercent),
              !expectedTitleSegments.contains(where: { $0.hasSuffix(" ctx") }),
              limitSegment?.text.hasSuffix("%") == true,
              limitSegment?.accessibilityText.contains(limitSegment?.text ?? "") == true,
              presentationWithoutLimits.providerSymbol == expectedProviderSymbol,
              presentationWithoutLimits.segments.allSatisfy({ $0.symbol == nil }),
              renderedTitle == expectedTitle,
              menu.items.contains(where: { $0.title == "Follow latest" }),
              menu.items.filter({ $0.representedObject is String }).count == recentSessions.prefix(5).count,
              menu.items.contains(where: { $0.title == "Provider limits (Beta)" && $0.submenu != nil }),
              menu.items.contains(where: { $0.title == "Budgets" && $0.submenu != nil }),
              menu.items.contains(where: { $0.title == "Quit Token Meter" && $0.action == #selector(quit) })
        else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 4,
                userInfo: [NSLocalizedDescriptionKey: "Native menu title did not use whole numbers without centered separators, or compact submenus did not match."]
            )
        }
        softwareUpdate = SoftwareUpdateSnapshot(
            enabled: true,
            autoInstall: false,
            state: "available",
            available: true,
            canUpdate: true,
            actionToken: "smoke-action-token",
            installAllowed: true
        )
        rebuildMenu()
        let availableItem = menu.items.first { $0.title == "New update available" }
        softwareUpdate.state = "updating"
        rebuildMenu()
        let updatingItem = menu.items.first { $0.title == "Updating Token Meter..." }
        softwareUpdate.state = "attention"
        rebuildMenu()
        let attentionItem = menu.items.first { $0.title == "Update needs attention" }
        guard availableItem?.action == #selector(installSoftwareUpdate),
              updatingItem?.isEnabled == false,
              attentionItem?.action == #selector(openUpdateSettings)
        else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 14,
                userInfo: [NSLocalizedDescriptionKey: "Native update rows did not expose install, progress, and recovery states."]
            )
        }
        print("native-update=available updating attention")
        let limitsMenu = menu.items.first { $0.title == "Provider limits (Beta)" }?.submenu
        let budgetsMenu = menu.items.first { $0.title == "Budgets" }?.submenu
        let limitsContainBudgetAction = limitsMenu?.items.contains { providerItem in
            providerItem.action == #selector(openBudgetSettings)
                || providerItem.submenu?.items.contains {
                    $0.action == #selector(openBudgetSettings)
                } == true
        } == true
        guard limitsMenu != nil, budgetsMenu != nil, !limitsContainBudgetAction else {
            throw NSError(
                domain: "TokenMeterMenuBar",
                code: 13,
                userInfo: [NSLocalizedDescriptionKey: "Native provider limits and budgets were not separate."]
            )
        }
        if let budget = monthlyBudget {
            let runtimeBudgetScopes = budget.scopes.filter { $0.id != "overall" }
            let overallBudgetItems = budgetsMenu?.items.filter {
                $0.title.hasPrefix("Overall ·")
            } ?? []
            let budgetActionItems = budgetsMenu?.items.filter {
                $0.action == #selector(openBudgetSettings)
            } ?? []
            let allRuntimeBudgetsVisible = runtimeBudgetScopes.allSatisfy { scope in
                budgetActionItems.contains { $0.title.hasPrefix("\(scope.label) ·") }
            }
            guard overallBudgetItems.count == 1,
                  budgetActionItems.count == runtimeBudgetScopes.count + 1,
                  allRuntimeBudgetsVisible
            else {
                throw NSError(
                    domain: "TokenMeterMenuBar",
                    code: 18,
                    userInfo: [NSLocalizedDescriptionKey: "Native budgets were incomplete or duplicated."]
                )
            }
        }
        if monthlyBudget?.scopes.contains(where: { $0.id == "opencode" }) == true {
            let openCodeItem = budgetsMenu?.items.first { $0.title.hasPrefix("OpenCode ·") }
            if openCodeItem?.action != #selector(openBudgetSettings) {
                throw NSError(
                    domain: "TokenMeterMenuBar",
                    code: 19,
                    userInfo: [NSLocalizedDescriptionKey: "Native budgets omitted the OpenCode monthly budget."]
                )
            }
        }
        print("native-sections=provider-limits,budgets separate=true")
        print("native-quick-actions=Dashboard,Spend,Tools,Settings height=26")
        print("native-enterprise-tokenomics=top-level below-settings=true")
        print("native-menu-title=\(expectedTitle)")
    }

    private func updateStatusTitle() {
        let presentation = selectedStatusTitlePresentation()
        let title = presentation.accessibilityTitle
        let budgetExceeded = monthlyBudget?.anyExceeded == true
        guard let button = statusItem.button else { return }
        button.contentTintColor = budgetExceeded
            ? NSColor.systemRed
            : (snapshot.connected ? NSColor.tokenMeterBlue : NSColor.secondaryLabelColor)
        switch effectiveStatusDisplayMode() {
        case .text:
            let titleImage = statusTitleImage(presentation)
            statusItem.length = titleImage.size.width + 8
            button.title = ""
            button.attributedTitle = NSAttributedString(string: "")
            button.image = titleImage
            button.imagePosition = .imageOnly
            button.contentTintColor = nil
        case .icon:
            statusItem.length = NSStatusItem.squareLength
            button.title = ""
            button.attributedTitle = NSAttributedString(string: "")
            button.image = splunkChevronImage()
            button.imagePosition = .imageOnly
        case .automatic:
            break
        }
        button.setAccessibilityLabel("Token Meter")
        button.setAccessibilityValue(title)
    }

    private func effectiveStatusDisplayMode() -> StatusDisplayMode {
        guard statusDisplayMode == .automatic else { return statusDisplayMode }
        let screen = statusItem.button?.window?.screen ?? NSScreen.main
        // macOS keeps right-side status items in the usable menu-bar region;
        // a notch alone is not a reason to hide the useful readout. Fall
        // back to the chevron only on genuinely narrow menu bars.
        guard let screen = screen, screen.visibleFrame.width > 0 else { return .text }
        return screen.visibleFrame.width < 1200 ? .icon : .text
    }

    private func selectedStatusTitle() -> String {
        selectedStatusTitlePresentation().accessibilityTitle
    }

    private func selectedStatusTitlePresentation() -> StatusTitlePresentation {
        let providerSymbol = runtimeCatalog[snapshot.provider]?.symbol ?? "runtime.generic"
        let providerAccessibilityText = runtimeCatalog[snapshot.provider]?.label ?? snapshot.provider
        guard snapshot.connected else {
            let text = snapshot.verdict.prefix
            return StatusTitlePresentation(
                providerSymbol: providerSymbol,
                providerAccessibilityText: providerAccessibilityText,
                segments: [StatusTitleSegment(text: text, symbol: nil, accessibilityText: text)],
                warning: false
            )
        }
        var parts = TitleMetric.allCases.compactMap { metric -> StatusTitleSegment? in
            guard titleMetrics.contains(metric) else { return nil }
            switch metric {
            case .cost:
                let text = snapshot.menuBarCostLabel
                return StatusTitleSegment(text: text, symbol: nil, accessibilityText: text)
            case .speed:
                let text = snapshot.menuBarOutputSpeedLabel
                return StatusTitleSegment(text: text, symbol: nil, accessibilityText: text)
            case .context:
                let text = snapshot.menuBarContextLabel
                return StatusTitleSegment(text: text, symbol: nil, accessibilityText: text)
            case .model:
                let text = snapshot.model
                return StatusTitleSegment(text: text, symbol: nil, accessibilityText: text)
            case .limits:
                guard let constrained = mostConstrainedQuota(),
                      let accessibilityText = limitsStatusTitle()
                else { return nil }
                return StatusTitleSegment(
                    text: constrained.window.percentLabel,
                    symbol: runtimeCatalog[constrained.provider.id]?.symbol ?? "runtime.generic",
                    accessibilityText: accessibilityText
                )
            case .todaySpend:
                let text = snapshot.menuBarTodaySpendLabel
                return StatusTitleSegment(text: text, symbol: nil, accessibilityText: text)
            }
        }
        if parts.count > 1, titleMetrics.contains(.todaySpend) {
            parts[parts.count - 1].text = "· " + parts[parts.count - 1].text
            parts[parts.count - 1].accessibilityText = "· " + parts[parts.count - 1].accessibilityText
        }
        let segments = parts.isEmpty
            ? [StatusTitleSegment(text: "TM", symbol: nil, accessibilityText: "TM")]
            : parts
        return StatusTitlePresentation(
            providerSymbol: providerSymbol,
            providerAccessibilityText: providerAccessibilityText,
            segments: segments,
            warning: monthlyBudget?.anyExceeded == true
        )
    }

    private func limitsStatusTitle() -> String? {
        guard let constrained = mostConstrainedQuota() else { return nil }
        return "\(constrained.provider.label) \(constrained.window.percentLabel)"
    }

    private func mostConstrainedQuota() -> (provider: ProviderQuota, window: QuotaWindow)? {
        providerQuotas
            .filter(\.fresh)
            .flatMap { provider in provider.windows.map { (provider: provider, window: $0) } }
            .max { $0.window.usedPercent < $1.window.usedPercent }
    }

    private func makeSettingsMenu() -> NSMenu {
        let settingsMenu = NSMenu(title: "Settings")

        let openSettings = NSMenuItem(title: "Open Settings", action: #selector(openSettings), keyEquivalent: "")
        openSettings.target = self
        settingsMenu.addItem(openSettings)
        let modelPrices = NSMenuItem(title: "Model Prices", action: #selector(openModelPrices), keyEquivalent: "")
        modelPrices.target = self
        settingsMenu.addItem(modelPrices)
        settingsMenu.addItem(.separator())

        let displayItem = NSMenuItem(title: "Menu bar display", action: nil, keyEquivalent: "")
        let displayMenu = NSMenu(title: "Menu bar display")
        for mode in StatusDisplayMode.allCases {
            let item = NSMenuItem(title: mode.title, action: #selector(setStatusDisplayMode(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = mode.rawValue
            item.state = statusDisplayMode == mode ? .on : .off
            displayMenu.addItem(item)
        }
        displayItem.submenu = displayMenu
        settingsMenu.addItem(displayItem)

        let shortcutItem = NSMenuItem(title: "Keyboard shortcut", action: nil, keyEquivalent: "")
        let shortcutMenu = NSMenu(title: "Keyboard shortcut")
        for shortcut in MenuBarShortcut.allCases {
            let itemTitle = shortcut == .custom ? customShortcutMenuTitle() : shortcut.title
            let action = shortcut == .custom
                ? #selector(configureCustomShortcut(_:))
                : #selector(setGlobalShortcut(_:))
            let item = NSMenuItem(title: itemTitle, action: action, keyEquivalent: "")
            item.target = self
            item.representedObject = shortcut.rawValue
            item.state = shortcut == .custom && customShortcutKeyCode == nil
                ? .off
                : (globalShortcut == shortcut ? .on : .off)
            shortcutMenu.addItem(item)
        }
        shortcutItem.submenu = shortcutMenu
        settingsMenu.addItem(shortcutItem)

        let titleItem = NSMenuItem(title: "Menu bar title", action: nil, keyEquivalent: "")
        let titleMenu = NSMenu(title: "Menu bar title")
        for metric in TitleMetric.allCases {
            let item = NSMenuItem(title: metric.title, action: #selector(toggleTitleMetric(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = metric.rawValue
            item.state = titleMetrics.contains(metric) ? .on : .off
            titleMenu.addItem(item)
        }
        titleItem.submenu = titleMenu
        settingsMenu.addItem(titleItem)

        let alerts = NSMenuItem(title: "Quota notifications", action: #selector(toggleQuotaAlerts(_:)), keyEquivalent: "")
        alerts.target = self
        alerts.state = quotaAlertsEnabled ? .on : .off
        settingsMenu.addItem(alerts)

        let thresholdItem = NSMenuItem(title: "Quota alert threshold (\(quotaAlertThreshold)%)", action: nil, keyEquivalent: "")
        let thresholdMenu = NSMenu(title: "Quota alert threshold")
        for threshold in [80, 90, 95] {
            let item = NSMenuItem(title: "\(threshold)% used", action: #selector(setQuotaThreshold(_:)), keyEquivalent: "")
            item.target = self
            item.tag = threshold
            item.state = quotaAlertThreshold == threshold ? .on : .off
            thresholdMenu.addItem(item)
        }
        thresholdItem.submenu = thresholdMenu
        settingsMenu.addItem(thresholdItem)
        return settingsMenu
    }

    @objc private func performQuickAction(_ sender: NSSegmentedControl) {
        guard let action = QuickAction(rawValue: sender.selectedSegment) else { return }
        menu.cancelTracking()
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            switch action {
            case .dashboard:
                self.openDashboard()
            case .spend:
                self.openDailyBrief()
            case .tools:
                self.openToolsAndSkills()
            case .settings:
                self.openSettings()
            }
        }
    }

    private func persistPinnedSession(_ sessionID: String?) {
        pinnedSessionID = sessionID
        if let sessionID = sessionID {
            tokenMeterDefaults.set(sessionID, forKey: pinnedSessionDefaultsKey)
        } else {
            tokenMeterDefaults.removeObject(forKey: pinnedSessionDefaultsKey)
        }
    }

    @objc private func followLatest() {
        persistPinnedSession(nil)
        refreshMenu()
        fetchState()
    }

    @objc private func pinSession(_ sender: NSMenuItem) {
        guard let sessionID = sender.representedObject as? String, !sessionID.isEmpty else { return }
        persistPinnedSession(sessionID)
        refreshMenu()
        fetchState()
    }

    @objc private func toggleTitleMetric(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let metric = TitleMetric(rawValue: raw)
        else { return }
        if titleMetrics.contains(metric) {
            titleMetrics.remove(metric)
        } else {
            titleMetrics.insert(metric)
        }
        tokenMeterDefaults.set(
            TitleMetric.allCases.filter(titleMetrics.contains).map(\.rawValue),
            forKey: titleMetricsDefaultsKey
        )
        if metric == .todaySpend {
            tokenMeterDefaults.set(
                titleMetrics.contains(.todaySpend),
                forKey: todaySpendTitlePreferenceDefaultsKey
            )
        }
        tokenMeterDefaults.removeObject(forKey: titleModeDefaultsKey)
        refreshMenu()
    }

    @objc private func setStatusDisplayMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let mode = StatusDisplayMode(rawValue: raw)
        else { return }
        statusDisplayMode = mode
        tokenMeterDefaults.set(mode.rawValue, forKey: statusDisplayModeDefaultsKey)
        updateStatusTitle()
        refreshMenu()
    }

    @objc private func setGlobalShortcut(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let shortcut = MenuBarShortcut(rawValue: raw)
        else { return }
        guard shortcut != .custom else {
            configureCustomShortcut(sender)
            return
        }
        globalShortcut = shortcut
        tokenMeterDefaults.set(shortcut.rawValue, forKey: globalShortcutDefaultsKey)
        registerGlobalHotKey()
        refreshMenu()
    }

    private func customShortcutMenuTitle() -> String {
        guard let keyCode = customShortcutKeyCode else { return MenuBarShortcut.custom.title }
        return "Custom (\(shortcutLabel(keyCode: keyCode, modifiers: customShortcutModifiers)))"
    }

    @objc private func configureCustomShortcut(_ sender: NSMenuItem) {
        let alert = NSAlert()
        alert.messageText = "Set Token Meter shortcut"
        alert.informativeText = "Press a key with at least one modifier (⌃, ⌥, ⌘, or ⇧), then choose Save."
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        alert.buttons.first?.isEnabled = false

        var captured: (keyCode: UInt32, modifiers: UInt32)?
        let monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak alert] event in
            let modifiers = carbonModifiers(from: event.modifierFlags)
            guard modifiers != 0 else {
                alert?.informativeText = "Use at least one modifier with the key."
                return nil
            }
            captured = (UInt32(event.keyCode), modifiers)
            alert?.informativeText = "Shortcut: \(shortcutLabel(keyCode: UInt32(event.keyCode), modifiers: modifiers))"
            alert?.buttons.first?.isEnabled = true
            return nil
        }
        let response = alert.runModal()
        if let monitor = monitor { NSEvent.removeMonitor(monitor) }
        guard response == .alertFirstButtonReturn, let captured = captured else { return }

        customShortcutKeyCode = captured.keyCode
        customShortcutModifiers = captured.modifiers
        globalShortcut = .custom
        tokenMeterDefaults.set(MenuBarShortcut.custom.rawValue, forKey: globalShortcutDefaultsKey)
        tokenMeterDefaults.set(Int(captured.keyCode), forKey: customShortcutKeyCodeDefaultsKey)
        tokenMeterDefaults.set(Int(captured.modifiers), forKey: customShortcutModifiersDefaultsKey)
        registerGlobalHotKey()
        refreshMenu()
    }

    @objc private func toggleQuotaAlerts(_ sender: NSMenuItem) {
        quotaAlertsEnabled.toggle()
        tokenMeterDefaults.set(quotaAlertsEnabled, forKey: quotaAlertsEnabledDefaultsKey)
        refreshMenu()
    }

    @objc private func setQuotaThreshold(_ sender: NSMenuItem) {
        guard [80, 90, 95].contains(sender.tag) else { return }
        quotaAlertThreshold = sender.tag
        tokenMeterDefaults.set(sender.tag, forKey: quotaAlertThresholdDefaultsKey)
        refreshMenu()
    }

    private func evaluateQuotaNotifications() {
        var changed = false
        var observedFreshQuota = false
        for provider in providerQuotas where provider.fresh {
            observedFreshQuota = true
            for window in provider.windows {
                let key = "\(provider.id):\(window.id)"
                let resetAt = window.resetAt?.timeIntervalSince1970
                guard var previous = quotaNotificationStates[key] else {
                    quotaNotificationStates[key] = QuotaNotificationState(
                        lastUsedPercent: window.usedPercent,
                        resetAt: resetAt,
                        firedThresholds: []
                    )
                    changed = true
                    continue
                }

                let resetAdvanced: Bool
                if let oldReset = previous.resetAt, let newReset = resetAt {
                    resetAdvanced = newReset > oldReset + 60
                } else {
                    resetAdvanced = false
                }

                if resetAdvanced {
                    if quotaAlertsEnabled, quotaObservationEstablished,
                       previous.lastUsedPercent >= Double(quotaAlertThreshold),
                       window.usedPercent < Double(quotaAlertThreshold) {
                        deliverQuotaNotification(
                            title: "\(provider.label) quota reset",
                            body: "\(window.label) is back to \(window.percentLabel) used."
                        )
                    }
                    previous.firedThresholds.removeAll()
                } else if quotaAlertsEnabled && quotaObservationEstablished {
                    let thresholds = Array(Set([quotaAlertThreshold, 95, 100])).sorted()
                    let crossed = thresholds.filter { threshold in
                        previous.lastUsedPercent < Double(threshold)
                            && window.usedPercent >= Double(threshold)
                            && !previous.firedThresholds.contains(threshold)
                    }
                    if let threshold = crossed.max() {
                        let severity = threshold >= 100 ? "exhausted" : (threshold >= 95 ? "critical" : "warning")
                        deliverQuotaNotification(
                            title: "\(provider.label) quota \(severity)",
                            body: "\(window.label) reached \(window.percentLabel) used; \(window.resetLabel)."
                        )
                        previous.firedThresholds.formUnion(crossed)
                    }
                }

                previous.lastUsedPercent = window.usedPercent
                previous.resetAt = resetAt
                quotaNotificationStates[key] = previous
                changed = true
            }
        }
        if changed, let data = try? JSONEncoder().encode(quotaNotificationStates) {
            tokenMeterDefaults.set(data, forKey: quotaNotificationStatesDefaultsKey)
        }
        if observedFreshQuota {
            quotaObservationEstablished = true
        }
    }

    private func evaluateBudgetNotifications() {
        guard let budget = monthlyBudget, budget.configured else { return }
        if budget.nativeNotifications,
           budget.exceeded,
           !budgetExceededNotificationMonths.contains(budget.month) {
            let prefix = budget.lowerBound ? "At least " : ""
            deliverQuotaNotification(
                title: "Overall monthly budget exceeded",
                body: "\(prefix)\(formatMoney(budget.spend)) of \(formatMoney(budget.budget)) recorded for \(budget.month) (\(Int(budget.percent.rounded()))% used)."
            )
            budgetExceededNotificationMonths.insert(budget.month)
            tokenMeterDefaults.set(
                Array(budgetExceededNotificationMonths).sorted(),
                forKey: budgetExceededMonthsDefaultsKey
            )
        }
        var changed = false
        for scope in budget.scopes {
            let key = scope.id
            guard var previous = budgetNotificationStates[key],
                  previous.month == budget.month
            else {
                budgetNotificationStates[key] = BudgetNotificationState(
                    month: budget.month,
                    lastPercent: scope.percent,
                    firedThresholds: Set(budget.thresholds.filter { scope.percent >= Double($0) })
                )
                changed = true
                continue
            }
            if budget.nativeNotifications {
                let crossed = budget.thresholds.filter { threshold in
                    previous.lastPercent < Double(threshold)
                        && scope.percent >= Double(threshold)
                        && !previous.firedThresholds.contains(threshold)
                }
                let overallExceededAlreadyReported = scope.id == "overall"
                    && budget.exceeded
                    && budgetExceededNotificationMonths.contains(budget.month)
                if let threshold = crossed.max(), !overallExceededAlreadyReported {
                    let prefix = budget.lowerBound ? "At least " : ""
                    deliverQuotaNotification(
                        title: "\(scope.label) monthly budget reached \(threshold)%",
                        body: scope.id == "overall"
                            ? "\(prefix)\(formatMoney(budget.spend)) of \(formatMoney(budget.budget)) recorded for \(budget.month)."
                            : "\(scope.label) reached \(Int(scope.percent.rounded()))% of its allocation."
                    )
                }
                previous.firedThresholds.formUnion(crossed)
            }
            previous.lastPercent = scope.percent
            budgetNotificationStates[key] = previous
            changed = true
        }
        if changed, let data = try? JSONEncoder().encode(budgetNotificationStates) {
            tokenMeterDefaults.set(data, forKey: budgetNotificationStatesDefaultsKey)
        }
    }

    private func deliverQuotaNotification(title: String, body: String) {
        if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_SMOKE"] == "1" { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = [
            "-e", "on run argv",
            "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run",
            "--", title, body,
        ]
        try? process.run()
    }

    @objc private func openDashboard() {
        if pinnedSessionID?.isEmpty == false {
            openDashboardPanel("summary")
        } else {
            openDashboardPanel("sessions", includePinnedSession: false)
        }
    }

    @objc private func openDailyBrief() {
        openDashboardPanel("spend", includePinnedSession: false)
    }

    @objc private func openBudgetSettings() {
        NSWorkspace.shared.open(tokenMeterBudgetSettingsURL)
    }

    @objc private func openUpdateSettings() {
        NSWorkspace.shared.open(tokenMeterUpdateSettingsURL)
    }

    @objc private func openEnterpriseTokenomics() {
        NSWorkspace.shared.open(tokenMeterEnterpriseTokenomicsURL)
    }

    @objc private func installSoftwareUpdate() {
        guard softwareUpdate.shouldOfferInstall else {
            openUpdateSettings()
            return
        }
        var request = URLRequest(
            url: tokenMeterInstallUpdateURL,
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: 5.0
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(softwareUpdate.actionToken, forHTTPHeaderField: "X-Token-Meter-Action")
        request.httpBody = Data("{}".utf8)
        softwareUpdate.state = "updating"
        refreshMenu()
        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                if error != nil || !(200..<300).contains(statusCode) {
                    self.softwareUpdate.state = "attention"
                }
                self.refreshMenu()
            }
        }.resume()
    }

    @objc private func openToolsAndSkills() {
        openDashboardPanel("capabilities", includePinnedSession: false)
    }

    @objc private func openModelPrices() {
        openDashboardPanel("model-pricing", includePinnedSession: false)
    }

    @objc private func openSettings() {
        openDashboardPanel("settings", includePinnedSession: false)
    }

    private func openDashboardPanel(_ panel: String, includePinnedSession: Bool = true) {
        var components = URLComponents(string: "http://127.0.0.1:8722/")
        if includePinnedSession, let sessionID = pinnedSessionID, !sessionID.isEmpty {
            components?.path = "/sessions/\(sessionID)"
        }
        components?.fragment = panel
        NSWorkspace.shared.open(components?.url ?? dashboardURL)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}

private func string(_ value: Any?) -> String? {
    value as? String
}

private func carbonModifiers(from flags: NSEvent.ModifierFlags) -> UInt32 {
    let flags = flags.intersection(.deviceIndependentFlagsMask)
    var modifiers: UInt32 = 0
    if flags.contains(.control) { modifiers |= UInt32(controlKey) }
    if flags.contains(.option) { modifiers |= UInt32(optionKey) }
    if flags.contains(.command) { modifiers |= UInt32(cmdKey) }
    if flags.contains(.shift) { modifiers |= UInt32(shiftKey) }
    return modifiers
}

private func shortcutLabel(keyCode: UInt32, modifiers: UInt32) -> String {
    var label = ""
    if modifiers & UInt32(controlKey) != 0 { label += "⌃" }
    if modifiers & UInt32(optionKey) != 0 { label += "⌥" }
    if modifiers & UInt32(cmdKey) != 0 { label += "⌘" }
    if modifiers & UInt32(shiftKey) != 0 { label += "⇧" }
    let key = [
        0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C", 9: "V",
        11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T", 31: "O", 32: "U",
        34: "I", 35: "P", 37: "L", 38: "J", 40: "K", 45: "N", 46: "M",
        18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7", 28: "8", 25: "9", 29: "0",
    ][Int(keyCode)] ?? "Key \(keyCode)"
    return label + key
}

private func int(_ value: Any?) -> Int {
    if let value = value as? Int { return value }
    if let value = value as? Double { return Int(value) }
    if let value = value as? String { return Int(value) ?? 0 }
    return 0
}

private func double(_ value: Any?) -> Double {
    if let value = value as? Double { return value }
    if let value = value as? Int { return Double(value) }
    if let value = value as? String { return Double(value) ?? 0 }
    return 0
}

private func optionalDouble(_ value: Any?) -> Double? {
    if value == nil || value is NSNull { return nil }
    return double(value)
}

private func bool(_ value: Any?) -> Bool {
    if let value = value as? Bool { return value }
    if let value = value as? Int { return value != 0 }
    if let value = value as? String { return ["1", "true", "yes"].contains(value.lowercased()) }
    return false
}

private func metricAvailable(_ availability: [String: Any], _ metric: String) -> Bool {
    guard availability.keys.contains(metric) else { return true }
    return bool(availability[metric])
}

private func formatMoney(_ value: Double) -> String {
    if abs(value) >= 1 {
        return String(format: "$%.2f", value)
    }
    return String(format: "$%.3f", value)
}

private func formatInt(_ value: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
}

private func formatCompactInt(_ value: Int) -> String {
    let magnitude = abs(value)
    let sign = value < 0 ? "-" : ""
    if magnitude >= 1_000_000_000 {
        return sign + compactScaled(Double(magnitude) / 1_000_000_000, suffix: "B")
    }
    if magnitude >= 1_000_000 {
        return sign + compactScaled(Double(magnitude) / 1_000_000, suffix: "M")
    }
    if magnitude >= 1_000 {
        return sign + compactScaled(Double(magnitude) / 1_000, suffix: "k")
    }
    return formatInt(value)
}

private func formatTokenRate(_ value: Double) -> String {
    if value >= 100 { return String(format: "%.0f", value) }
    if value >= 10 { return String(format: "%.1f", value) }
    return String(format: "%.2f", value)
}

private func formatCompactDuration(_ value: TimeInterval) -> String {
    let seconds = max(0, Int(value))
    if seconds < 60 { return "\(seconds)s" }
    let minutes = seconds / 60
    if minutes < 60 { return "\(minutes)m" }
    let hours = minutes / 60
    let remainingMinutes = minutes % 60
    if hours < 48 { return "\(hours)h" + (remainingMinutes > 0 ? " \(remainingMinutes)m" : "") }
    let days = hours / 24
    let remainingHours = hours % 24
    return "\(days)d" + (remainingHours > 0 ? " \(remainingHours)h" : "")
}

private func compactScaled(_ value: Double, suffix: String) -> String {
    let pattern: String
    if value >= 100 {
        pattern = "%.0f"
    } else if value >= 10 {
        pattern = "%.1f"
    } else {
        pattern = "%.2f"
    }
    var number = String(format: pattern, value)
    while number.contains(".") && number.hasSuffix("0") {
        number.removeLast()
    }
    if number.hasSuffix(".") {
        number.removeLast()
    }
    return number + suffix
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_MENU_SMOKE"] == "1" {
    let app = NSApplication.shared
    let delegate = TokenMeterMenuBar()
    app.delegate = delegate
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
        do {
            try delegate.runMenuSmoke()
            print("native-menu=ready sessions=direct follow-latest=direct")
            exit(0)
        } catch {
            fputs("Token Meter native menu smoke failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
    app.run()
}

if ProcessInfo.processInfo.environment["TOKEN_METER_MENUBAR_SMOKE"] == "1" {
    do {
        let chevron = splunkChevronImage()
        guard chevron.isTemplate, chevron.size == NSSize(width: 18, height: 18) else {
            throw NSError(domain: "TokenMeterMenuBar", code: 11, userInfo: [NSLocalizedDescriptionKey: "Splunk status-item chevron is invalid."])
        }
        let titleImage = statusTitleImage(StatusTitlePresentation(
            providerSymbol: "runtime.claude",
            providerAccessibilityText: "Claude",
            segments: [
                StatusTitleSegment(text: "$9", symbol: nil, accessibilityText: "$9"),
                StatusTitleSegment(text: "36 tok/s", symbol: nil, accessibilityText: "36 tok/s"),
                StatusTitleSegment(text: "80%", symbol: "runtime.claude", accessibilityText: "Claude 80%"),
            ],
            warning: false
        ))
        guard titleImage.isTemplate,
              titleImage.size.width > 0,
              titleImage.size.height == 18,
              titleImage.accessibilityDescription == "Claude, $9  36 tok/s  Claude 80%"
        else {
            throw NSError(domain: "TokenMeterMenuBar", code: 12, userInfo: [NSLocalizedDescriptionKey: "Adaptive template status-title image is invalid."])
        }
        let data = try Data(contentsOf: tokenMeterMenubarURL)
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let dict = obj as? [String: Any] else {
            throw NSError(domain: "TokenMeterMenuBar", code: 1, userInfo: [NSLocalizedDescriptionKey: "Response was not a JSON object."])
        }
        let snapshot = MeterSnapshot.fromJSON(dict)
        let budget = MonthlyBudget.fromJSON(dict["budget"] as? [String: Any])
        let quotas = (dict["provider_quotas"] as? [[String: Any]] ?? []).compactMap(ProviderQuota.fromJSON)
        let runtimeCatalog = RuntimePresentation.catalog(dict["runtime_catalog"])
        let sessions = (dict["recent_sessions"] as? [[String: Any]] ?? [])
            .compactMap { RecentSession.fromJSON($0, catalog: runtimeCatalog) }
        let savedMetrics: Set<TitleMetric> = {
            if let saved = tokenMeterDefaults.array(forKey: titleMetricsDefaultsKey) as? [String] {
                var parsed = Set(saved.compactMap(TitleMetric.init(rawValue:)))
                if tokenMeterDefaults.object(forKey: todaySpendTitlePreferenceDefaultsKey) == nil {
                    parsed.insert(.todaySpend)
                }
                if !parsed.isEmpty { return parsed }
            }
            return [.cost, .speed, .todaySpend]
        }()
        let alertsEnabled = tokenMeterDefaults.object(forKey: quotaAlertsEnabledDefaultsKey) == nil
            ? true : tokenMeterDefaults.bool(forKey: quotaAlertsEnabledDefaultsKey)
        let savedThreshold = tokenMeterDefaults.integer(forKey: quotaAlertThresholdDefaultsKey)
        let alertThreshold = [80, 90, 95].contains(savedThreshold) ? savedThreshold : 80
        let constrained = quotas
            .filter(\.fresh)
            .flatMap { provider in provider.windows.map { (provider: provider, window: $0) } }
            .max { $0.window.usedPercent < $1.window.usedPercent }
        var titleSegments = TitleMetric.allCases.compactMap { metric -> String? in
            guard savedMetrics.contains(metric) else { return nil }
            switch metric {
            case .cost: return snapshot.menuBarCostLabel
            case .speed: return snapshot.menuBarOutputSpeedLabel
            case .context: return snapshot.menuBarContextLabel
            case .model: return snapshot.model
            case .limits:
                guard let constrained = constrained else { return nil }
                return "\(constrained.provider.label) \(constrained.window.percentLabel)"
            case .todaySpend: return snapshot.menuBarTodaySpendLabel
            }
        }
        if titleSegments.count > 1, savedMetrics.contains(.todaySpend) {
            titleSegments[titleSegments.count - 1] = "· " + titleSegments[titleSegments.count - 1]
        }
        let baseTitle = titleSegments.joined(separator: "  ")
        let activeTitle = budget?.anyExceeded == true ? "⚠︎ \(baseTitle)" : baseTitle
        guard !activeTitle.contains("Today ") else {
            throw NSError(domain: "TokenMeterMenuBar", code: 15, userInfo: [NSLocalizedDescriptionKey: "Compact status title still includes the removed today prefix."])
        }
        print("native-menu=system chevron=template status-title=adaptive-template sessions=\(sessions.prefix(5).count) direct-follow=true")
        print(snapshot.statusTitle)
        print(snapshot.outputSpeedLabel)
        print("active-title=\(activeTitle)")
        print("budget-state=\(budget?.compactLabel ?? "unconfigured") exceeded=\(budget?.anyExceeded == true)")
        print("title-metrics=\(TitleMetric.allCases.filter(savedMetrics.contains).map(\.title).joined(separator: ","))")
        print("quota-alerts=\(alertsEnabled ? "on" : "off") warn-at=\(alertThreshold)%")
        for provider in quotas {
            let windows = provider.windows.map { "\($0.label)=\($0.percentLabel)" }.joined(separator: ",")
            let coverage = provider.coverageNote.isEmpty ? "complete" : provider.coverageNote
            print("quota=\(provider.label) status=\(provider.status) windows=\(windows.isEmpty ? "none" : windows) coverage=\(coverage)")
        }
        exit(0)
    } catch {
        fputs("Token Meter menubar smoke failed: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

let app = NSApplication.shared
let delegate = TokenMeterMenuBar()
app.delegate = delegate
app.run()
