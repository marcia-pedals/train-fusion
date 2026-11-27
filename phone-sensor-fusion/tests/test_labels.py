"""Stop rows: with door chimes, with only some of them, and without any (this car's doors shut)."""

import numpy as np

from labels.load import Stop

nan = np.nan


def test_a_stop_without_door_chimes_stands_from_wheels_stop_to_wheels_start():
    st = Stop("X", 10.0, nan, nan, nan, nan, 90.0)
    assert not st.doors
    assert (st.t_still, st.t_leave) == (10.0, 90.0)


def test_a_stop_with_chimes_falls_back_on_them_where_wheel_times_are_blank():
    st = Stop("X", nan, 12.0, 18.0, 40.0, 48.0, nan)
    assert st.doors
    assert (st.t_still, st.t_leave) == (12.0, 48.0)


def test_boarding_through_open_doors_still_has_doors():
    st = Stop("X", nan, nan, nan, 40.0, 48.0, 60.0)  # only the door-close chime heard
    assert st.doors
