# TODO

- **Sparse shapes cut corners.** 8 of the 16 shapes in feed 20260922 have stretches with no
  points for kilometres (5.6 km Menlo Park → Redwood City; also 3.1 km Millbrae → Burlingame,
  2.1 km Redwood City → San Carlos, 1.4 km Broadway → Millbrae). The straight chord there is
  up to ~330 m from the track (compared with denser shapes), so GPS fixes on those stretches
  project to wrong positions and large offsets (> 200 m is ignored by the reference), and the
  speed-limit / physics geometry is off. Fix in the static stage: e.g. give every trip the
  densest shape of its direction, or densify sparse shapes from a dense one.
