import Foundation

/// The fitted model and the track, as exported by phone-sensor-fusion/app/export.py
/// (model.json in the app bundle). Everything that could be precomputed already is:
/// this file only describes the JSON.
struct Model: Decodable {
    struct Chime: Decodable { let log_in: [Double]; let log_out: [Double] }
    struct Partial: Decodable {
        let ratio: Double
        let bin: [Int]  // per speed state: spectrum bin to read, -1 = off the end
        let band: [Int]  // per speed state: row of `table`
        let table: [[Double]]  // [speed band][prominence bin] log-likelihood ratio
        let extra: Int  // extra max-pooling (bins each side) before the lookup
    }
    struct Comb: Decodable { let partials: [Partial]; let z_edges: [Double] }
    struct Level: Decodable { let mu: [Double]; let sd: Double; let weight: Double }
    struct Accel: Decodable { let sd: Double; let outlier: Double; let sd_outlier: Double }
    struct Phone: Decodable { let mean: [Double]; let sd: [Double]; let `switch`: Double }
    struct Bins: Decodable { let `in`: [Int]; let side: [Int] }
    struct ChimeDSP: Decodable {
        let nwin: Int; let hop: Int; let db: Float; let min_partials: Int
        let window: [Float]; let open: [Bins]; let close: [Bins]
    }
    struct LineDSP: Decodable {
        let nwin: Int; let nfft: Int; let hop: Int?; let bins: Int; let median: Int; let pool: Int; let window: [Float]
    }
    struct DSP: Decodable {
        let fs: Int
        let decimate_taps: [Float]
        let chime: ChimeDSP
        let tone: LineDSP
        let accel: LineDSP
        let rumble_sos: [[Double]]
        let vib_sos: [[[Double]]]
    }
    struct Track: Decodable {
        let stations: [String]; let station_s: [Double]
        let step: Double; let curvature: [Double]; let heading_south: [Double]
        let elev_step: Double; let elevation: [Double]
        let declination: Double
    }

    let n_stopped: Int
    let n_v: Int
    let dv: Double
    let v: [Double]  // speed of every state
    let mode: [Int]  // mode of every state (0 moving, 1...5 the stop cycle)
    let prior: [Double]
    let transitions: [[Double]]  // [from, to, probability]
    let open_chime_state: Int
    let close_chime_state: Int
    let chime: [String: Chime]
    let chime_edges: [Double]
    let tone: Comb
    let wheel: [String: Comb?]
    let level: [String: Level?]
    let level_outlier: Double
    let level_floor: Double
    let accel: Accel
    let phone: Phone
    let dsp: DSP
    let track: Track
    let stations: [String: Double]

    var n: Int { n_stopped + n_v }

    static func load() throws -> Model {
        guard let url = Bundle.main.url(forResource: "model", withExtension: "json") else {
            throw CocoaError(.fileNoSuchFile, userInfo: [NSLocalizedDescriptionKey: "model.json is not in the app bundle"])
        }
        return try JSONDecoder().decode(Model.self, from: Data(contentsOf: url))
    }
}

/// What the app shows: the model's belief about one second, using only the past.
struct Belief: Sendable {
    struct Station: Sendable, Identifiable {
        let name: String
        let southbound: Bool
        let p: Double
        var id: String { name + (southbound ? "S" : "N") }
    }

    /// Where the train is, in the terms a rider thinks in: the station it last stopped at,
    /// the next one along, and how far between them it has got.
    struct Place: Sendable {
        let last: String
        let next: String?  // nil at the end of the line
        let southbound: Bool
        let confidence: Double  // probability of (last station, direction)
        let progress: Double  // 0 at `last` ... 1 at `next`
        let metresFromLast: Double
        let metresToNext: Double?
        let alternative: String?  // the runner-up, as "last → next", when it is a real contender
    }

    /// One station stop, by the leading edge of each door chime (seconds into the recording).
    struct Stop: Sendable, Identifiable {
        let opened: Int
        var closed: Int?
        var station: String?  // the best guess, right or wrong: see `stations` for how sure
        var id: Int { opened }
    }

    var stopLog: [Stop] = []  // oldest first
    /// A door-open chime is believed but not yet counted as a stop (that takes about 3 s):
    /// `place`, `stops` and `stopLog` are still about the leg just finished.
    var stopPending = false
    var place: Place?  // nil until a door opening has been heard and a station can be named
    var second = 0  // recording time this belief is about: the latest second that is complete
    var speedMean = 0.0  // m/s
    var speedSd = 0.0
    var acceleration: Double?  // m/s^2: how the believed speed has changed over the last 3 s
    var speedHist: [Float] = []  // P(speed in [k, k+1) m/s), moving states only
    var pStanding = 0.0
    var modeP: [Double] = []  // moving, stopped before doors, door-open chime, doors open, door-close chime, stopped after doors
    var phoneP: [Double] = []  // resting, held, handled
    var distanceSinceStop = 0.0  // m, since the last door-open chime (or the start)
    var stops = 0
    var legs: [Double] = []  // m, distance of each completed leg
    var stations: [Station] = []  // most probable first; empty before the first stop
    var accelInUse = false  // along-track acceleration is being trusted right now
    var accelReading: Double?  // m/s^2 along the track as the accelerometer has it (sign and zero applied), when in use
    /// How strongly the motor tone / the wheel-rotation lines back the believed speed: log
    /// likelihood there relative to the average over speeds, averaged over the last few
    /// seconds. nil = that measurement isn't being read (wheel lines: phone being handled).
    var toneEvidence: Double?
    var wheelEvidence: Double?
}

let modeNames = ["moving", "stopped, before doors", "door-open chime", "doors open", "door-close chime", "stopped, after doors"]
let phoneNames = ["resting", "held", "handled"]
