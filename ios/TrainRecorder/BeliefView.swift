import SwiftUI

/// What the model believes, for someone glancing at their phone on a train: what the
/// train is doing, where it is, where it has stopped, and how good the measurements
/// behind that are. The numbers are under "Details".
///
/// Every card keeps its size whether or not it has anything to show yet (a greyed-out
/// skeleton stands in), so the screen doesn't jump as fixes arrive.
struct BeliefView: View {
    let belief: Belief?
    let start: Date?  // when the recording started, to show door times as clock times
    let message: String?
    let waiting: String
    @State private var showDetails = false

    var body: some View {
        let b = belief ?? Belief()
        VStack(alignment: .leading, spacing: 8) {
            if let message { Text(message).font(.footnote).foregroundStyle(.red) }
            StatusCard(belief: b, waiting: belief == nil ? waiting : nil)
            PlaceCard(belief: b)
            StopsCard(belief: b, start: start)
            SensorRow(belief: b)
            if belief != nil {
                DisclosureGroup("Details", isExpanded: $showDetails) { DetailsView(belief: b).padding(.top, 8) }
                    .font(.subheadline)
                    .tint(.secondary)
                    .padding(.horizontal, 4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Miles and mph, or kilometres and km/h (Settings).
struct Units {
    static let key = "metricUnits"
    let metric: Bool

    var speedName: String { metric ? "km/h" : "mph" }
    func speed(_ metresPerSecond: Double) -> Double { metresPerSecond * (metric ? 3.6 : 2.23694) }
    func distance(_ metres: Double) -> String { String(format: "%.1f %@", metres / (metric ? 1000 : 1609.344), metric ? "km" : "mi") }
}

private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
    content()
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
}

/// One line: what the train is doing right now.
private struct StatusCard: View {
    let belief: Belief
    let waiting: String?  // shown instead, before there is any belief at all
    @AppStorage(Units.key) private var metric = false

    private var mode: Int { belief.modeP.indices.max(by: { belief.modeP[$0] < belief.modeP[$1] }) ?? -1 }
    private var moving: Bool { belief.stops > 0 && mode == 0 }

    private var look: (text: String, icon: String, color: Color) {
        if let waiting { return (waiting, "ear", .secondary) }
        // before the first door chime the model can't tell a platform from a train
        guard belief.stops > 0 else { return ("Waiting for the first stop…", "ear", .secondary) }
        switch mode {
        case 0:
            // 90 % of the belief lies within 1.645 sd
            let u = Units(metric: metric)
            let v = u.speed(belief.speedMean), ci = u.speed(1.645 * belief.speedSd)
            return ("\(Int(v.rounded())) ± \(Int(ci.rounded(.up))) \(u.speedName)", "tram.fill", .blue)
        case 2: return ("Doors opening", "door.left.hand.open", .green)
        case 3: return ("Doors open", "door.left.hand.open", .green)
        case 4: return ("Doors closing", "door.left.hand.closed", .orange)
        default: return ("Train stopped", "tram.fill", .teal)
        }
    }

    var body: some View {
        card {
            HStack(spacing: 10) {
                Image(systemName: look.icon).font(.title2).foregroundStyle(look.color).frame(width: 30)
                Text(look.text).font(.title2.weight(.semibold).monospacedDigit()).lineLimit(1).minimumScaleFactor(0.6)
                Spacer(minLength: 4)
                if moving, let a = belief.acceleration {
                    Text(String(format: "%+.1f m/s²", a == 0 ? 0 : a))
                        .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .frame(height: 32)  // the same whatever it says, so nothing below jumps
        }
    }
}

/// Where the train is: the last station, the next one, and how far along it has got.
private struct PlaceCard: View {
    fileprivate let belief: Belief
    @AppStorage(Units.key) private var metric = false
    @ScaledMetric(relativeTo: .subheadline) private var labelHeight: CGFloat = 38  // as in StationBar

    var body: some View {
        let u = Units(metric: metric)
        let p = belief.place
        return card {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    if let p {
                        Text(p.southbound ? "Southbound" : "Northbound").font(.subheadline.weight(.medium))
                        Spacer(minLength: 6)
                        if let other = p.alternative, p.confidence <= 0.9 {
                            Text("or \(stationShortName(other))").font(.caption).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.6)
                        }
                        Text(p.confidence > 0.9 ? "confident" : p.confidence > 0.6 ? "fairly sure" : "a guess")
                            .font(.caption.weight(.medium))
                            .padding(.horizontal, 8).padding(.vertical, 2)
                            .background((p.confidence > 0.9 ? Color.green : p.confidence > 0.6 ? .yellow : .orange).opacity(0.25), in: Capsule())
                    } else {
                        Label("Waiting for position fix…", systemImage: "location.magnifyingglass")
                            .font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
                        Spacer()
                    }
                }
                .frame(height: 22)
                if let p {
                    StationBar(place: p, previous: previous(p), previousLeg: belief.legs.last, phase: phase(p),
                               moving: belief.modeP.first.map { $0 > 0.5 } ?? false, units: u)
                } else {
                    SegmentBar(progress: 0, moving: false, skeleton: true)
                    // the same two lines as the station bar's labels
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text("Station name").font(.subheadline.weight(.semibold))
                            Text(u.distance(0)).font(.footnote)
                        }
                        Spacer(minLength: 8)
                        VStack(alignment: .trailing, spacing: 1) {
                            Text("Station name").font(.subheadline.weight(.semibold))
                            Text(u.distance(0)).font(.footnote)
                        }
                    }
                    .lineLimit(1)
                    .frame(height: labelHeight, alignment: .top)
                    .redacted(reason: .placeholder)
                }
            }
        }
    }
}

/// How the position card draws the line: on the way between two stations; pulling in
/// (still the leg just run, the train at its far end); or standing at a station, shown
/// between the one before and the one after.
private enum PlacePhase { case between, arriving, station }

extension PlaceCard {
    /// The station of the stop before the last one, if it can stand on the line next to
    /// the others.
    fileprivate func previous(_ p: Belief.Place) -> String? {
        guard belief.stopLog.count >= 2, let name = belief.stopLog[belief.stopLog.count - 2].station,
              name != p.last, name != p.next else { return nil }
        return name
    }

    fileprivate func phase(_ p: Belief.Place) -> PlacePhase {
        guard previous(p) != nil, !belief.modeP.isEmpty else { return .between }
        // Only once this stop has been counted, which happens about 3 s into the chime: the
        // display runs ahead of that, and until then `p` is still the leg being finished, so
        // the station views would show the wrong three stations.
        guard !belief.stopPending, belief.distanceSinceStop < 50 else { return .between }
        let mode = belief.modeP.indices.max { belief.modeP[$0] < belief.modeP[$1] } ?? 0
        if mode == 2 { return .arriving }  // the door-open chime
        // doors open, closing or closed again; and after that until the train is clearly moving
        if mode >= 3 || belief.modeP[0] < 0.8 { return .station }
        return .between
    }
}

/// The line with its stations and the train. Stations are placed along it by a number:
/// 0 the left end, 1 the right end, outside that off the card. The three phases differ
/// only in those numbers, so a change of phase slides the stations to their new places.
private struct StationBar: View {
    let place: Belief.Place
    let previous: String?
    let previousLeg: Double?  // metres
    let phase: PlacePhase
    let moving: Bool
    let units: Units
    @ScaledMetric(relativeTo: .subheadline) private var labelHeight: CGFloat = 38  // a name, a distance under it

    private struct Node: Identifiable {
        let id: String  // the station's name
        let x: CGFloat
        var distance: String?
        var visible: Bool { x >= 0 && x <= 1 }
    }

    private var nodes: [Node] {
        var out: [Node] = []
        switch phase {
        case .between:
            if let previous { out.append(Node(id: previous, x: -1)) }
            out.append(Node(id: place.last, x: 0, distance: units.distance(place.metresFromLast)))
            if let next = place.next { out.append(Node(id: next, x: 1, distance: units.distance(place.metresToNext ?? 0))) }
        case .arriving:
            if let previous { out.append(Node(id: previous, x: 0, distance: previousLeg.map(units.distance))) }
            out.append(Node(id: place.last, x: 1, distance: units.distance(0)))
            if let next = place.next { out.append(Node(id: next, x: 2)) }
        case .station:
            if let previous { out.append(Node(id: previous, x: 0)) }
            out.append(Node(id: place.last, x: 0.5))
            if let next = place.next { out.append(Node(id: next, x: 1)) }
        }
        return out
    }

    private var trainX: CGFloat {
        switch phase {
        case .between: place.next == nil ? 0 : CGFloat(place.progress)
        case .arriving: 1
        case .station: 0.5
        }
    }

    var body: some View {
        VStack(spacing: 6) {
            GeometryReader { g in
                let w = g.size.width - 28
                ZStack(alignment: .leading) {
                    Capsule().fill(Color(.systemGray4)).frame(height: 6).padding(.horizontal, 14)
                    Capsule().fill(.blue).frame(width: max(w * trainX, 0), height: 6).padding(.leading, 14)
                    ForEach(nodes) { node in
                        Group {
                            if node.x <= trainX {
                                Circle().fill(.blue)
                            } else {
                                Circle().strokeBorder(Color(.systemGray2), lineWidth: 3).background(Circle().fill(Color(.systemBackground)))
                            }
                        }
                        .frame(width: 12, height: 12)
                        .offset(x: 8 + w * node.x)
                        .opacity(node.visible ? 1 : 0)
                    }
                    Image(systemName: "tram.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(.white)
                        .frame(width: 28, height: 28)
                        .background(moving ? Color.blue : .teal, in: Circle())
                        .offset(x: w * trainX)
                }
                .frame(height: 28)
            }
            .frame(height: 28)
            GeometryReader { g in
                ZStack(alignment: .topLeading) {
                    ForEach(nodes) { node in
                        let side = phase == .station && node.x != 0.5
                        let edge = min(max(node.x, 0), 1)
                        let align: HorizontalAlignment = edge == 0 ? .leading : edge == 1 ? .trailing : .center
                        // the name, and under it the distance to it
                        VStack(alignment: align, spacing: 1) {
                            Text(stationShortName(node.id))
                                .font(side ? .footnote : .subheadline.weight(.semibold))
                                .foregroundStyle(side ? .secondary : .primary)
                                .minimumScaleFactor(0.6)
                            if let d = node.distance { distance(d) }
                        }
                        .lineLimit(1)
                        // each station gets its own share of the width, so names never overlap
                        .frame(width: g.size.width * labelShare(node) - 4, alignment: Alignment(horizontal: align, vertical: .top))
                        // left-aligned at 0, centred at 0.5, right-aligned at 1
                        // (a station off the card is laid out at the edge and then pushed out
                        // with an offset: laid out beyond the edge it would shift all the others)
                        .alignmentGuide(.leading) { d in -edge * (g.size.width - d.width) }
                        .offset(x: (node.x - edge) * g.size.width * 0.5)
                        .opacity(node.visible ? 1 : 0)
                    }
                    if place.next == nil, phase == .between {
                        Text("end of the line").font(.subheadline).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }
                .frame(width: g.size.width, height: labelHeight, alignment: .topLeading)
            }
            .frame(height: labelHeight)
        }
        // only a change of phase is animated: the train's own progress, or a jump to
        // another station, is not
        .animation(.easeInOut(duration: 0.7), value: phase)
    }

    /// How much of the card's width a station's label may take: half each for the two ends
    /// of a leg, and when standing at a station a little more for it than for its neighbours.
    private func labelShare(_ node: Node) -> CGFloat {
        phase == .station ? (node.x == 0.5 ? 0.4 : 0.3) : 0.5
    }

    private func distance(_ text: String) -> some View {
        Text(text).font(.footnote.monospacedDigit()).foregroundStyle(.secondary).transition(.opacity)
    }
}

/// The names that don't fit on a small screen, shortened.
func stationShortName(_ name: String) -> String {
    [
        "San Francisco 4th & King Street": "San Francisco",
        "South San Francisco": "South SF",
        "California Avenue": "California Ave",
        "San Jose Diridon": "San Jose",
    ][name] ?? name
}

/// The stops so far: when the doors opened and closed at each (the start of each chime).
/// The latest few, newest first; rows not yet filled are greyed out.
private struct StopsCard: View {
    let belief: Belief
    let start: Date?
    private static let rows = 4

    private func time(_ second: Int?) -> String {
        guard let second else { return "–" }
        guard let start else { return String(format: "%d:%02d", second / 60, second % 60) }
        return start.addingTimeInterval(Double(second)).formatted(date: .omitted, time: .standard)
    }

    var body: some View {
        // station names are only worth showing once the fix is confident
        let confident = (belief.place?.confidence ?? 0) > 0.9
        let stops = confident ? Array(belief.stopLog.suffix(Self.rows).reversed()) : []
        return card {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 5) {
                GridRow {
                    Text("Station")
                    Text("Doors opened").gridColumnAlignment(.trailing)
                    Text("Doors closed").gridColumnAlignment(.trailing)
                }
                .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                ForEach(0..<Self.rows, id: \.self) { k in
                    GridRow {
                        if k < stops.count {
                            Text(stops[k].station.map(stationShortName) ?? "?").font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.7)
                            Text(time(stops[k].opened))
                            Text(time(stops[k].closed))
                        } else {
                            Group {
                                Text("Station name").font(.subheadline.weight(.semibold))
                                Text("0:00:00 PM")
                                Text("0:00:00 PM")
                            }
                            .redacted(reason: .placeholder).opacity(0.5)
                        }
                    }
                    .font(.subheadline.monospacedDigit())
                    .frame(height: 20)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// How good each source of information is right now, as three coloured icons; tap for a
/// line of explanation each. Green good, yellow fair, red missing, grey not in play.
private struct SensorRow: View {
    let belief: Belief
    @State private var expanded = ProcessInfo.processInfo.arguments.contains("-expandsensors")  // (the flag: for screenshots)

    private struct Item: Identifiable {
        let id: String  // the name shown
        let icon: String  // "wheel" and "spring" are our own drawings
        let color: Color
        let state: String
        var number: String?
    }

    private static func pen(_ size: CGFloat) -> StrokeStyle {
        StrokeStyle(lineWidth: size * 0.055, lineCap: .round, lineJoin: .round)
    }

    /// Every sensor looks the same: a disc tinted with its status colour, and on it a plain
    /// glyph of one weight and size in that colour. The glyph says which sensor; the colour
    /// alone says how it is doing.
    private func badge(_ item: Item, size: CGFloat) -> some View {
        ZStack {
            Circle().fill(item.color.opacity(item.color == .gray ? 0.15 : 0.2))
            if item.icon == "wheel" || item.icon == "spring" {
                Group {
                    if item.icon == "wheel" { TrainWheel().stroke(item.color, style: Self.pen(size)) } else { MassOnSpring().stroke(item.color, style: Self.pen(size)) }
                }
                .frame(width: size * 0.64, height: size * 0.5)
            } else {
                Image(systemName: item.icon).font(.system(size: size * 0.46, weight: .semibold)).foregroundStyle(item.color)
            }
        }
        .frame(width: size, height: size)
    }

    /// Evidence is a log likelihood ratio: how many times likelier this measurement makes
    /// the believed speed than an average speed. Shown as that factor.
    private static func grade(_ evidence: Double) -> (Color, String, String) {
        let factor = exp(evidence)
        let number = factor >= 10 ? String(format: "%.0f×", factor) : String(format: "%.1f×", factor)
        return evidence >= 1.5 ? (.green, "clear", number) : evidence >= 0.6 ? (.yellow, "faint", number) : (.red, "not found", number)
    }

    private var items: [Item] {
        let b = belief
        guard b.phoneP.count == 3 else {  // nothing measured yet
            return [("Steadiness", "iphone"), ("Acceleration", "spring"), ("Motor tone", "waveform"),
                    ("Wheel vibration", "wheel")].map { Item(id: $0.0, icon: $0.1, color: .gray, state: "waiting") }
        }
        let phone = b.phoneP.indices.max { b.phoneP[$0] < b.phoneP[$1] } ?? 2
        let moving = b.stops > 0 && (b.modeP.first ?? 0) > 0.5
        var out = [Item(id: "Steadiness", icon: "iphone", color: [.green, .yellow, .red][phone],
                        state: ["lying still", "held", "moving"][phone],
                        number: String(format: "%.0f %%", b.phoneP[phone] * 100))]

        let idle = b.stops > 0 ? "stopped" : "waiting"
        if phone != 0 {
            out.append(Item(id: "Acceleration", icon: "spring", color: .gray, state: "needs phone still"))
        } else if let a = b.accelReading {
            out.append(Item(id: "Acceleration", icon: "spring", color: .green, state: "in use",
                            number: String(format: "%+.2f m/s²", a)))
        } else {
            out.append(Item(id: "Acceleration", icon: "spring", color: .yellow, state: "finding forward"))
        }

        if !moving {
            out.append(Item(id: "Motor tone", icon: "waveform", color: .gray, state: idle))
        } else if b.speedMean < 10 {
            out.append(Item(id: "Motor tone", icon: "waveform", color: .gray, state: "too slow"))
        } else if let e = b.toneEvidence {
            let (c, word, number) = Self.grade(e)
            out.append(Item(id: "Motor tone", icon: "waveform", color: c, state: word, number: number))
        }

        if !moving {
            out.append(Item(id: "Wheel vibration", icon: "wheel", color: .gray, state: idle))
        } else if phone == 2 {
            out.append(Item(id: "Wheel vibration", icon: "wheel", color: .gray, state: "phone moving"))
        } else if let e = b.wheelEvidence {
            let (c, word, number) = Self.grade(e)
            out.append(Item(id: "Wheel vibration", icon: "wheel", color: c, state: word, number: number))
        } else {
            out.append(Item(id: "Wheel vibration", icon: "wheel", color: .gray, state: "no reading yet"))
        }
        return out
    }

    var body: some View {
        card {
            VStack(alignment: .leading, spacing: 8) {
                Button { withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() } } label: {
                    HStack(spacing: 0) {
                        ForEach(items) { item in
                            badge(item, size: 34)
                            .frame(maxWidth: .infinity)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel("\(item.id): \(item.state)")
                        }
                        Image(systemName: "chevron.down").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                            .rotationEffect(.degrees(expanded ? 180 : 0))
                            .frame(width: 24)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if expanded {
                    ForEach(items) { item in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Circle().fill(item.color).frame(width: 8, height: 8).alignmentGuide(.firstTextBaseline) { $0[.bottom] }
                            Text("**\(item.id)**: \(item.state)").font(.footnote)
                            Spacer(minLength: 6)
                            if let number = item.number {
                                Text(number).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
    }
}

/// A railway wheel on its rail, shaking: one ring for the tyre, a hub, the rail, and
/// vibration marks to either side. Drawn in the manner of the system's symbols.
private struct TrainWheel: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        let railY = r.maxY
        let radius = r.height / 2 * 0.72
        let c = CGPoint(x: r.midX, y: railY - radius - r.height * 0.07)
        p.addEllipse(in: CGRect(x: c.x - radius, y: c.y - radius, width: 2 * radius, height: 2 * radius))
        p.addEllipse(in: CGRect(x: c.x - radius * 0.28, y: c.y - radius * 0.28, width: 0.56 * radius, height: 0.56 * radius))
        p.move(to: CGPoint(x: r.minX, y: railY))
        p.addLine(to: CGPoint(x: r.maxX, y: railY))
        // vibration: two short arcs off each side of the tyre, the outer one shorter
        let arcs: [(reach: CGFloat, half: Double)] = [(1.4, 30), (1.8, 20)]
        for side in [180.0, 0.0] {
            for (reach, half) in arcs {
                let a0 = CGFloat((side - half) * Double.pi / 180)
                p.move(to: CGPoint(x: c.x + radius * reach * CGFloat(cos(Double(a0))), y: c.y + radius * reach * CGFloat(sin(Double(a0)))))
                p.addArc(center: c, radius: radius * reach, startAngle: .degrees(side - half), endAngle: .degrees(side + half), clockwise: false)
            }
        }
        return p
    }
}

/// A mass on a spring, fixed to a wall on the left: what an accelerometer is.
private struct MassOnSpring: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        let y = r.midY
        // the wall
        p.move(to: CGPoint(x: r.minX, y: r.minY + r.height * 0.12))
        p.addLine(to: CGPoint(x: r.minX, y: r.maxY - r.height * 0.12))
        // the spring: a zigzag
        let x0 = r.minX, x1 = r.minX + r.width * 0.6, amp = r.height * 0.2
        let lead = r.width * 0.07, n = 4
        p.move(to: CGPoint(x: x0, y: y))
        p.addLine(to: CGPoint(x: x0 + lead, y: y))
        for k in 0..<n {
            let x = x0 + lead + (x1 - x0 - 2 * lead) * (CGFloat(k) + 0.5) / CGFloat(n)
            p.addLine(to: CGPoint(x: x, y: y + (k % 2 == 0 ? -amp : amp)))
        }
        p.addLine(to: CGPoint(x: x1 - lead, y: y))
        p.addLine(to: CGPoint(x: x1, y: y))
        // the mass
        let side = r.height * 0.56
        p.addRoundedRect(in: CGRect(x: x1, y: y - side / 2, width: min(side, r.maxX - x1), height: side),
                         cornerSize: CGSize(width: side * 0.18, height: side * 0.18))
        return p
    }
}

/// A line from the last station to the next with the train on it.
private struct SegmentBar: View {
    let progress: Double
    let moving: Bool
    var skeleton = false  // no train, no progress: just the line

    var body: some View {
        GeometryReader { g in
            let w = g.size.width - 28
            ZStack(alignment: .leading) {
                Capsule().fill(Color(.systemGray4)).frame(height: 6).padding(.horizontal, 14)
                Capsule().fill(.blue).frame(width: max(w * progress, 0), height: 6).padding(.leading, 14)
                Circle().fill(skeleton ? Color(.systemGray4) : .blue).frame(width: 12, height: 12).offset(x: 8)
                Circle().strokeBorder(Color(.systemGray2), lineWidth: 3).background(Circle().fill(Color(.systemBackground)))
                    .frame(width: 12, height: 12).offset(x: g.size.width - 20)
                if !skeleton {
                Image(systemName: "tram.fill")
                    .font(.system(size: 15))
                    .foregroundStyle(.white)
                    .frame(width: 28, height: 28)
                    .background(moving ? Color.blue : .teal, in: Circle())
                    .offset(x: w * progress)  // (not animated: a jump to another station shouldn't slide)
                }
            }
            .frame(height: 28)
        }
        .frame(height: 28)
    }
}

/// Everything the model believes, as numbers: for the curious, and for debugging.
private struct DetailsView: View {
    let belief: Belief
    private static let modeColors: [Color] = [.gray, .blue, .orange, .teal, .yellow, .pink]

    var body: some View {
        let b = belief
        VStack(alignment: .leading, spacing: 14) {
            section("Speed") {
                Text(String(format: "%.1f ± %.1f m/s (%.0f mph)", b.speedMean, b.speedSd, b.speedMean * 2.23694)).font(.footnote.monospacedDigit())
                SpeedDistribution(belief: b).frame(height: 80)
            }
            section("What the train is doing") {
                ForEach(0..<b.modeP.count, id: \.self) { m in bar(modeNames[m], b.modeP[m], Self.modeColors[m]) }
            }
            section("What the phone is doing") {
                ForEach(0..<b.phoneP.count, id: \.self) { s in bar(phoneNames[s], b.phoneP[s], .secondary) }
                Text(b.accelInUse ? "Using the accelerometer for speed changes" : "Accelerometer not in use (needs a resting phone and one clear acceleration)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            section("Stations") {
                if b.stations.isEmpty {
                    Text(b.stops == 0 ? "No door opening heard yet." : "One stop heard: every station is still possible.")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(b.stations) { s in bar("\(s.name) \(s.southbound ? "↓" : "↑")", s.p, .blue) }
                }
                Text(String(format: "%d stops heard · %.2f km since the last", b.stops, b.distanceSinceStop / 1000)).font(.footnote.monospacedDigit())
                if !b.stopLog.isEmpty {
                    Text("Door chimes, seconds into the recording (open / close): "
                         + b.stopLog.map { "\($0.opened) / \($0.closed.map(String.init) ?? "–")" }.joined(separator: ", "))
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                if !b.legs.isEmpty {
                    Text("Legs: " + b.legs.map { String(format: "%.2f", $0 / 1000) }.joined(separator: ", ") + " km")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Text("\(b.second / 60):\(String(format: "%02d", b.second % 60)) into the recording. Uses only what has been heard and felt so far; stops and stations are confirmed about 3 s after the door chime.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .foregroundStyle(.primary)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }

    private func bar(_ name: String, _ p: Double, _ color: Color) -> some View {
        HStack(spacing: 8) {
            Text(name).font(.footnote).frame(width: 150, alignment: .leading)
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    Capsule().fill(color).frame(width: max(g.size.width * p, p > 0.005 ? 3 : 0))
                }
            }
            .frame(height: 7)
            Text(String(format: "%.2f", p)).font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                .frame(width: 36, alignment: .trailing)
        }
    }
}

/// P(speed) in 1 m/s bins, with the probability of standing as its own bar on the left.
private struct SpeedDistribution: View {
    let belief: Belief

    var body: some View {
        Canvas { ctx, size in
            let n = belief.speedHist.count
            let gap: CGFloat = 14, labelH: CGFloat = 14
            let w = (size.width - gap - 14) / CGFloat(n)
            let h = size.height - labelH
            let top = max(belief.speedHist.max() ?? 0, Float(belief.pStanding), 0.05)
            func rect(_ x: CGFloat, _ p: Float, _ width: CGFloat) -> CGRect {
                let bh = CGFloat(p / top) * h
                return CGRect(x: x, y: h - bh, width: width, height: bh)
            }
            ctx.fill(Path(roundedRect: rect(0, Float(belief.pStanding), 12), cornerRadius: 2), with: .color(.teal))
            for k in 0..<n {
                ctx.fill(Path(roundedRect: rect(14 + gap + CGFloat(k) * w, belief.speedHist[k], max(w - 1, 1)), cornerRadius: 1.5),
                         with: .color(.blue))
            }
            ctx.stroke(Path { p in p.move(to: CGPoint(x: 0, y: h)); p.addLine(to: CGPoint(x: size.width, y: h)) },
                       with: .color(.secondary.opacity(0.4)), lineWidth: 0.5)
            ctx.draw(Text("still").font(.caption2).foregroundStyle(.secondary), at: CGPoint(x: 6, y: h + 8), anchor: .center)
            for v in stride(from: 0, through: 30, by: 10) {
                ctx.draw(Text("\(v)").font(.caption2).foregroundStyle(.secondary),
                         at: CGPoint(x: 14 + gap + CGFloat(v) * w, y: h + 8), anchor: .center)
            }
            ctx.draw(Text("m/s").font(.caption2).foregroundStyle(.secondary), at: CGPoint(x: size.width - 10, y: h + 8), anchor: .center)
        }
    }
}
