"""The cameras: where they are, how to fetch them, and whether they see the tracks.

`sees_track`: what we found by looking at their images (see docs/cams.md):
    yes    the Caltrain tracks are in view and trains have been seen
    track  the tracks are in view, no train seen yet (or only rarely run there)
    maybe  something that could be the tracks, unconfirmed
    no     looked, no tracks in view (pan-tilt-zoom cameras may still turn toward them)
    down   showed only the "unavailable" image while we looked
Only `enabled` cameras are archived by default.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cam:
    id: str
    source: str          # key into sources.SOURCES
    name: str
    lat: float
    lon: float
    url: str
    period_s: float      # how often the source refreshes the image
    sees_track: str
    enabled: bool = False
    track_m: int | None = None  # distance from the camera to the nearest Caltrain shape
    km: float | None = None     # position along the line, km from SF (for passes.py)
    note: str = ""
    recheck_s: float | None = None  # while down: how often to look for it coming back
                                    # (default schedule.DOWN_POLL_S)


CALTRANS_D4 = "https://cwwp2.dot.ca.gov/data/d4/cctv/image"


def _caltrans(tv, name, lat, lon, slug, track_m, sees_track="no", enabled=False, note=""):
    return Cam(f"caltrans-{tv}", "caltrans", name, lat, lon, f"{CALTRANS_D4}/{slug}/{slug}.jpg",
               300.0, sees_track, enabled, track_m, None, note)


# Central Expwy @ Rengstorff Ave and @ Farley St: right beside the tracks (Rengstorff is a
# level crossing), flagged as promising; all but one view were down on 2026-11-11.
SCC_PROMISING = {"401821", "401822", "401823", "401824", "401825", "401826", "401827"}


def _scc(web_id, county_id, name, lat, lon, km, track_m, sees_track, enabled, note=""):
    """A Santa Clara County camera, by its TrafficLand id (the county GIS layer's Web_ID;
    county_id is its CAM_ID there). period_s is the fast rate inside a pass window."""
    promising = web_id in SCC_PROMISING
    if promising:
        note = f"{note}; promising: {track_m} m from the track, next to the Rengstorff/Farley crossings"
    return Cam(f"scc-{web_id}", "trafficland", f"{name} (county cam {county_id})", lat, lon,
               web_id, 3.0, sees_track, enabled or (promising and sees_track != "no"), track_m, km,
               note, 300.0 if promising or sees_track in ("yes", "track") else None)


CAMS: list[Cam] = [
    _caltrans("tv316", "I-80 : 6th Street, San Francisco", 37.77696, -122.40347,
              "tv316i806thstreet", 576, "no", False),
    _caltrans("tv317", "I-280 : 6th Street Offramp, San Francisco", 37.77145, -122.3974,
              "tv317i2806thstreetofframp", 216, "no", False),
    _caltrans("tv304", "I-80 : US-101, San Francisco", 37.77025, -122.40554,
              "tv304i80us101", 489, "no", False),
    _caltrans("tv314", "I-280 : At Indiana St, San Francisco", 37.756079, -122.39198,
              "tv314i280atindianast", 32, "no", False),
    _caltrans("tv309", "I-280 : AT CESAR CHAVEZ, San Francisco", 37.75025, -122.3924,
              "tv309i280atcesarchavez", 59, "track", True,
              "Caltrain on a bridge with catenary poles at the bottom right; only ~100 m of track in view, so a train is in view ~5 s"),
    _caltrans("tv326", "I-280 : Just south of Cesar Chavez, San Francisco", 37.7453, -122.39498,
              "tv326i280justsouthofcesarchavez", 101, "no", False),
    _caltrans("tv325", "I-280 : Just South Of Cesar Chavez, San Francisco", 37.74208, -122.39759,
              "tv325i280justsouthofcesarchavez", 291, "no", False),
    _caltrans("tv380", "US-101 : At 280 Split, San Francisco", 37.72781, -122.4029,
              "tv380us101at280split", 583, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv379", "US-101 : AT 3RD BAYSHORE, San Francisco", 37.71802, -122.3996,
              "tv379us101at3rdbayshore", 48, "no", False),
    _caltrans("tv305", "US-101 : Just North of Candlestick Park, San Francisco", 37.71069, -122.39526,
              "tv305us101justnorthofcandlestickpark", 538, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv403", "US-101 : Just North of Lagoon Way, Brisbane", 37.68159, -122.38861,
              "tv403us101justnorthoflagoonway", 390, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv404", "US-101 : JNO SIERRA PT PKWY, Brisbane", 37.6815, -122.38861,
              "tv404us101jnosierraptpkwy", 385, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv405", "US-101 : South of Bayshore Boulevard, Brisbane", 37.66996, -122.39229,
              "tv405us101southofbayshoreboulevard", 57, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11; promising: 57 m from the track, by Bayshore station"),
    _caltrans("tv401", "US-101 : On Airport Flyover Structure, South San Francisco", 37.66353, -122.399599,
              "tv401us101onairportflyoverstructure", 135, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11; promising: 135 m, by the track at South SF"),
    _caltrans("tv406", "US-101 : AT AIRPORT BL, South San Francisco", 37.650235, -122.407093,
              "tv406us101atairportbl", 206, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11; promising: 206 m, near South SF station"),
    _caltrans("tv398", "I-380 : West of US-101, San Bruno", 37.63377, -122.40778,
              "tv398i380westofus101", 453, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv397", "I-380 : JEO EL CAMINO REAL, San Bruno", 37.6333, -122.41781,
              "tv397i380jeoelcaminoreal", 429, "no", False),
    _caltrans("tv410", "US-101 : AT JNO SFO IC, San Francisco", 37.61638, -122.3988,
              "tv410us101atjnosfoic", 267, "no", False),
    _caltrans("tv411", "US-101 : AT JNO MILLBRAE AVE OC, Millbrae", 37.60805, -122.38908,
              "tv411us101atjnomillbraeaveoc", 408, "no", False),
    _caltrans("tv412", "US-101 : AT JSO MILBRAE AV OC, Burlingame", 37.6017, -122.3797,
              "tv412us101atjsomilbraeavoc", 588, "no", False),
    _caltrans("tv413", "US-101 : AT JNO BROADWAY, Burlingame", 37.5958, -122.37038,
              "tv413us101atjnobroadway", 475, "no", False,
              "fogged-over lens / washed out"),
    _caltrans("t093s", "US-101 : N101 Broadway On Off Ramp Southview, Burlingame", 37.59231, -122.36276,
              "t093sus101n101broadwayonofframpsouthview", 448, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("t093w", "US-101 : N101 Broadway On Off Ramp Westview, Burlingame", 37.59228, -122.36271,
              "t093wus101n101broadwayonofframpwestview", 447, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("t093n", "US-101 : N101 Broadway On Off Ramp Northview, Burlingame", 37.59196, -122.36265,
              "t093nus101n101broadwayonofframpnorthview", 418, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv414", "US-101 : N101 at Broadway, Burlingame", 37.59004, -122.36151,
              "tv414us101n101atbroadway", 278, "no", False,
              "washed-out white image"),
    _caltrans("tv458", "SR-92 : AT S DELAWARE ST, San Mateo", 37.5528, -122.30499,
              "tv458sr92atsdelawarest", 357, "no", False),
    _caltrans("tv082", "SR-92 : AT 82 IC, San Mateo", 37.55065, -122.31181,
              "tv082sr92at82ic", 280, "no", False),
    _caltrans("tv457", "SR-92 : El Camino Real, San Mateo", 37.54995, -122.31291,
              "tv457sr92elcaminoreal", 400, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("t028n", "SR-82 : Embarcadero, Palo Alto", 37.4375, -122.16,
              "t028nsr82embarcadero", 279, "no", False),
    _caltrans("t028w", "SR-82 : Embarcadero, Stanford", 37.43738, -122.16048,
              "t028wsr82embarcadero", 314, "no", False),
    _caltrans("t028e", "SR-82 : Embarcadero, Palo Alto", 37.4373, -122.15976,
              "t028esr82embarcadero", 283, "no", False),
    _caltrans("t028s", "SR082 : Embarcadero, Stanford", 37.43709, -122.16023,
              "t028ssr082embarcadero", 327, "no", False),
    _caltrans("t029n", "SR-82 : Page Mill Road / Oregon Expressway, Palo Alto", 37.42334, -122.14216,
              "t029nsr82pagemillroadoregonexpressway", 533, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("t029e", "SR-82 : Page Mill Road / Oregon Expressway, Palo Alto", 37.42316, -122.14176,
              "t029esr82pagemillroadoregonexpressway", 525, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("t029w", "SR-82 : Page Mill Road / Oregon Expressway, Palo Alto", 37.42295, -122.14233,
              "t029wsr82pagemillroadoregonexpressway", 576, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("t029s", "SR-82 : Page Mill Road / Oregon Expressway, Palo Alto", 37.42282, -122.14195,
              "t029ssr82pagemillroadoregonexpressway", 565, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tvc92", "SR-85 : AT CENTRAL EXPY, Mountain View", 37.39571, -122.0685,
              "tvc92sr85atcentralexpy", 417, "no", False),
    _caltrans("tvc91", "SR-85 : AT EVELYN AV OR, Mountain View", 37.3907, -122.0689,
              "tvc91sr85atevelynavor", 94, "no", False),
    _caltrans("tvc54", "I-880 : AT COLEMAN AV, San Jose", 37.34861, -121.91803,
              "tvc54i880atcolemanav", 420, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tvc55", "I-880 : The Alameda, San Jose", 37.34504, -121.92384,
              "tvc55i880thealameda", 182, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11; promising: 182 m, I-880 crosses the line by College Park"),
    _caltrans("tvb76", "I-880 : The Alameda, San Jose", 37.342878, -121.926239,
              "tvb76i880thealameda", 497, "no", False),
    _caltrans("tv923", "SR-87 : AT JCT 280, San Jose", 37.32302, -121.89119,
              "tv923sr87atjct280", 546, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tv922", "SR-87 : AT ALMADEN EXPRESSWAY, San Jose", 37.30231, -121.87665,
              "tv922sr87atalmadenexpressway", 31, "track", True,
              "the rail bridge over SR-87 is in the top-left corner; only Gilroy-branch trains (4 SB in the evening, 4 NB in the morning) and freight pass"),
    _caltrans("tvc84", "SR-87 : AT CURTNER AVE, San Jose", 37.2937, -121.87326,
              "tvc84sr87atcurtnerave", 370, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tvc86", "SR-85 : AT GREAT OAKS BL, San Jose", 37.24065, -121.78106,
              "tvc86sr85atgreatoaksbl", 165, "no", False),
    _caltrans("tvb51", "US-101 : South of SR-85, San Jose", 37.235543, -121.75711,
              "tvb51us101southofsr85", 590, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
    _caltrans("tvb50", "US-101 : Metcalf Road Overcross, Morgan Hill", 37.230073, -121.746954,
              "tvb50us101metcalfroadovercross", 577, "down", False,
              "placeholder (Temporarily Unavailable) on 2026-11-11"),
]

# Santa Clara County: Central Expwy in Mountain View, which runs right beside the tracks.
# County GIS: maps.santaclaracounty.gov/server/rest/services/agency/SCCTrafficCamera/MapServer/1
CAMS += [
    _scc("401818", 1101, "Central Expy @ Mayfield Ave (WB View)", 37.40709136, -122.10587006, 54.883, 45,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401819", 1102, "Central Expy @ Mayfield Ave (EB View)", 37.4066966, -122.10575755, 54.917, 16,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401820", 1103, "Mayfield Ave @ Central Expy (SB View)", 37.40676117, -122.10595263, 54.899, 11,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401821", 1106, "Central Expy @ Rengstorff Ave (WB View)", 37.40336715, -122.09750288, 55.74, 38,
         "no", False, "WB view along Central Expwy; tracks out of frame (to the right)"),
    _scc("401822", 1107, "Central Expy @ Rengstorff Ave (EB View)", 37.4030747, -122.09723121, 55.776, 20,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401823", 1108, "Rengstorff Ave @ Central Expy (SB View)", 37.40313738, -122.09756611, 55.747, 13,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401824", 1109, "Rengstorff Ave @ Central Expy (NB View)", 37.40330647, -122.09723078, 55.765, 43,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401825", 1111, "Central Expy @ Farley St (WB View)", 37.40079053, -122.09076596, 56.401, 47,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401826", 1112, "Central Expy @ Farley St (EB View)", 37.40046677, -122.09058691, 56.432, 22,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401827", 1113, "Farley St @ Central Expy (SB View)", 37.40043926, -122.09061143, 56.431, 18,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401828", 1116, "Central Expy @ Shoreline Blvd W (WB View)", 37.39775772, -122.08282197, 57.181, 59,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401829", 1117, "Central Expy @ Shoreline Blvd W (EB View)", 37.39748958, -122.08276069, 57.199, 35,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401830", 1118, "Shoreline Blvd W @ Central Expy @ (SB View)", 37.39745201, -122.08278859, 57.199, 30,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401831", 1121, "Central Expy @ Shoreline Blvd E (WB View)", 37.39708658, -122.08113649, 57.348, 58,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401832", 1122, "Central Expy @ Shoreline Blvd E (EB View)", 37.39679835, -122.08108017, 57.367, 32,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401833", 1123, "Shoreline Blvd E @ Central Expy @ (SB View)", 37.39677811, -122.08109588, 57.366, 29,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401834", 1126, "Central Expy @ Moffett Blvd (WB View)", 37.39572728, -122.0778567, 57.675, 52,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401835", 1127, "Central Expy @ Castro St (EB View)", 37.39543242, -122.07765501, 57.706, 31,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401836", 1128, "Moffett Blvd @ Central Expy (SB View)", 37.39548061, -122.07800749, 57.675, 22,
         "no", False, "SB view down Moffett Blvd, away from the tracks"),
    _scc("401837", 1129, "Castro St @ Central Expy (NB View)", 37.39570363, -122.0775944, 57.697, 60,
         "track", True, "NB view up Castro St: the Castro St level crossing (gates, signals) and Mountain View station platforms in the middle distance"),
    _scc("401838", 1131, "Central Expy @ Highway 85 (WB View)", 37.39215944, -122.06909745, 58.546, 43,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401839", 1132, "Central Expy @ Highway 85 (EB View)", 37.3918211, -122.06893614, 58.576, 16,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401840", 1133, "Central Expy @ Highway 85 Off-Ramp (SB View)", 37.3917836, -122.06895866, 58.576, 11,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401841", 1136, "Central Expy @ Whisman Station Dr (WB View)", 37.38991626, -122.06339667, 59.109, 48,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401842", 1137, "Central Expy @ Whisman Station Dr (EB View)", 37.3895861, -122.06328244, 59.134, 19,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401843", 1138, "Whisman Station Dr @ Central Expy (SB View)", 37.38954717, -122.06330493, 59.135, 15,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401844", 1141, "Central Expy @ Ferguson Dr (WB View)", 37.38800856, -122.05794495, 59.639, 39,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401845", 1142, "Central Expy @ Ferguson Dr (EB View)", 37.3877436, -122.05787667, 59.656, 15,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401846", 1143, "Ferguson Dr @ Central Expy (SB View)", 37.387797, -122.05807501, 59.638, 13,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401847", 1146, "Central Expy @ Bernardo Ave (WB View)", 37.38619834, -122.05231476, 60.177, 45,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401848", 1147, "Central Expy @ Bernardo Ave (EB View)", 37.38590327, -122.05217924, 60.2, 19,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
    _scc("401849", 1148, "Bernardo Ave @ Central Expy (SB View)", 37.38586996, -122.05219199, 60.201, 16,
         "down", False, "TrafficLand 404 (camera down) all of 2026-11-11"),
]

# ALERTCalifornia (UC San Diego) wildfire cameras: pan-tilt-zoom, with their frames
# archived on their side (alertca.py). url = the ALERTCalifornia camera id.
CAMS += [
    Cam("alertca-sanbrunomtn-cam01", "alertcalifornia", "San Bruno Mountain (ALERTCalifornia)",
        37.687371, -122.435452, "sanbrunomtn-cam01", 11.0, "maybe", True, 3900, 7.94,
        "hilltop PTZ 3.9 km SW of Bayshore station; home pose (pan 57.35, zoomed) looks past it "
        "at Hunters Point; the wide pan-65 detour overlooks Brisbane and the Bayshore area"),
    Cam("alertca-redwoodcity-cam02", "alertcalifornia", "Redwood City 2 (ALERTCalifornia)",
        37.488216, -122.230957, "redwoodcity-cam02", 11.0, "yes", True, 300, 40.4,
        "rooftop PTZ in downtown Redwood City. Its home pose (pan -83.28, ~80% of frames) shows "
        "the line ~350 m west as a thin band just above the privacy mask (x ~600-1300, "
        "y ~560-640 of 1920x1080); trains seen there at 10:05:07, 12:36:10, 14:06:09 on "
        "2026-11-11. A train is ~20 px tall. Fogged in until ~09:15 that day."),
]
# per camera: home pose, poses that show the tracks (tags are unreliable: see alertca.py),
# download policy ("all": every frame; the poses are logged for the later stage)
ALERTCA = {
    "alertca-sanbrunomtn-cam01": {"home": (57.35, -1.35, 1722.0), "track_poses": [(65.23, 0.0, 1.0)],
                                  "mode": "all"},
    "alertca-redwoodcity-cam02": {"home": (-83.28, -3.15, 1.0), "track_poses": [(-83.28, -3.15, 1.0)],
                                  "mode": "all"},
}

# Camera catalogs archived daily (fetch.catalog_loop): name -> url
CATALOGS = {
    "caltrans_d4": "https://cwwp2.dot.ca.gov/data/d4/cctv/cctvStatusD04.json",
    "alertcalifornia": "https://cameras.alertcalifornia.org/public-camera-data/all_cameras-v3.json",
    "scc": "https://maps.santaclaracounty.gov/server/rest/services/agency/SCCTrafficCamera/"
           "MapServer/1/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=json",
}
