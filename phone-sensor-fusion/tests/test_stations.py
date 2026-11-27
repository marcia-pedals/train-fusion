"""The station layer on a made-up ride: real stops plus one door opening that wasn't a stop."""

import numpy as np

from labels.frames import MOVING, OPEN_CHIME
from maps import track as trackmod
from model import stations
from model.infer import Result


def _ride(stops, false_at):
    """A train stopping at the stations `stops` (indices, in order) at 15 m/s with 30 s
    dwells, and a door opening that wasn't a stop `false_at` of the way through each leg
    given in the dict {leg: fraction}. The first layer is taken to be exact."""
    tr = trackmod.load()
    v, mode = [], []
    for i, (a, b) in enumerate(zip(stops, stops[1:])):
        v += [0.0] * 30
        mode += [OPEN_CHIME] * 5 + [MOVING] * 25
        n = int(round(abs(tr.station_s[b] - tr.station_s[a]) / 15.0))
        leg = [MOVING] * n
        if i in false_at:
            k = int(n * false_at[i])
            leg[k:k + 5] = [OPEN_CHIME] * 5
        v += [15.0] * n
        mode += leg
    v += [0.0] * 30
    mode += [OPEN_CHIME] * 5 + [MOVING] * 25
    v, mode = np.array(v), np.array(mode)
    T = len(v)
    return tr, Result(v=v, v_sd=np.zeros(T), v_filt=v, mode_p=np.zeros((T, 6)), path_mode=mode,
                      path_v=v, phone=np.zeros((T, 3)), loglik=0.0)


def test_a_false_opening_is_found_and_does_not_lose_the_way():
    stops = [3, 4, 5, 6, 7, 8]
    tr, res = _ride(stops, {2: 0.4})
    loc = stations.localise(res, {}, use=())
    assert len(loc.t_stop) == len(stops) + 1
    false = 3  # the opening part-way through the third leg
    real = [j for j in range(len(loc.t_stop)) if j != false]
    assert loc.p_real[false] < 0.2
    assert all(loc.p_real[j] > 0.8 for j in real)
    best = [np.unravel_index(np.argmax(loc.smooth[j]), loc.smooth[j].shape) for j in real]
    assert [a for a, _ in best] == stops
    assert all(d == 0 for _, d in best)  # southbound: station index increasing
    # the belief from the past alone, at the stop after the false opening
    a, _ = np.unravel_index(np.argmax(loc.filt[false + 1]), loc.filt[false + 1].shape)
    assert a == stops[3]


def test_trusting_every_opening_is_the_old_layer():
    tr, res = _ride([3, 4, 5, 6], {})
    loc = stations.localise(res, {}, use=(), p_false=0.0)
    assert np.all(loc.p_real == 1.0)
    assert [np.argmax(loc.smooth[j].sum(1)) for j in range(4)] == [3, 4, 5, 6]


def test_most_likely_path_calls_the_false_opening_false():
    stops = [3, 4, 5, 6, 7, 8]
    tr, res = _ride(stops, {2: 0.4})
    loc = stations.localise(res, {}, use=())
    assert list(loc.path) == stops[:3] + [-1] + stops[3:]
    assert loc.path_dir == 0
