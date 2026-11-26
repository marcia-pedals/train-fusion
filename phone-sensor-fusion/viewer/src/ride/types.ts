// What model/ride_view.py writes per ride and run (eval/runs/<run>/rides/<id>/meta.json).

export interface Raster { file: string; fps: number; bins: number; frames: number; fmax: number }

export interface DoorEvent {
  t: number;
  kind: "open" | "open_end" | "close" | "close_end";
  station: string | null;
}

export interface RideMeta {
  id: string;
  T: number;
  audio: {file: string; t0: number};
  spec: Raster;
  accel: Raster;
  speed_post: {filt: string; smooth: string; bins: number; dv: number};
  mode_names: string[];
  stops: {station: string; t_arrive: number | null; t_open: number | null; t_close: number | null; t_depart: number | null}[];
  events: DoorEvent[];
  series: {
    v_smooth: (number | null)[]; v_smooth_sd: (number | null)[];
    v_filt: (number | null)[]; v_filt_sd: (number | null)[];
    v_filt_lo: number[]; v_filt_hi: number[];  // the central 95 % of the belief
    v_smooth_lo: number[]; v_smooth_hi: number[];
    v_label: (number | null)[]; v_label_confident: number[];
    mode_filt: number[][]; mode_smooth: number[][]; mode_label: number[];
    loc_p_true: number[]; loc_p_best: number[]; loc_wrong: number[];
  };
  loc_openings: number[];
}

/** The ride with its binary files loaded. */
export interface Ride {
  meta: RideMeta;
  spec: Uint8Array;
  accel: Uint8Array;
  speedFilt: Uint8Array;
  speedSmooth: Uint8Array;
  run: string;  // the evaluation run whose inference this is
}

/** The visible stretch of recording time, in seconds. */
export interface View { t0: number; t1: number }
