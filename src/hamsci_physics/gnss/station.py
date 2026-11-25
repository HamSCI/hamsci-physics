"""The station position the VTEC service uses until the receiver sends its own.

Order: precise position in the config, then STATION_LAT / STATION_LON in the
environment, then the centre of the grid square.  Never 0/0.  This module
logs nothing: a position never reaches a log.
"""

from typing import Mapping, Optional, Tuple

from hamsci_dsp.geometry import grid_to_latlon

from .broadcast_ephemeris import geodetic_to_ecef
from .rtcm3_adapter import config_station_ecef, plausible_ecef

Ecef = Tuple[float, float, float]


def _from_latlon(lat, lon, alt=0.0) -> Optional[Ecef]:
    """ECEF for a usable latitude/longitude, else None.  0/0 is the
    template placeholder, so it counts as absent."""
    try:
        lat, lon = float(lat), float(lon)
        alt = float(alt or 0.0)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    pos = geodetic_to_ecef(lat, lon, alt)
    return pos if plausible_ecef(pos) else None


def _grid_ecef(grid) -> Optional[Ecef]:
    grid = str(grid or "").strip()
    if len(grid) < 4 or "<" in grid or ">" in grid:
        return None
    try:
        lat, lon = grid_to_latlon(grid)
    except Exception:
        return None
    return _from_latlon(lat, lon)


def station_ecef(config: dict, environ: Mapping[str, str]) -> Optional[Ecef]:
    st = (config or {}).get("station") or {}
    # config_station_ecef does not refuse 0/0, so screen the template first.
    if _from_latlon(st.get("latitude"), st.get("longitude")) is not None:
        pos = config_station_ecef(config)
        if pos is not None:
            return pos
    pos = _from_latlon(environ.get("STATION_LAT"), environ.get("STATION_LON"))
    if pos is not None:
        return pos
    return _grid_ecef(st.get("grid_square") or environ.get("STATION_GRID"))
