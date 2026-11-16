import math
from datetime import date, datetime
from functools import lru_cache

from app.config import Home, settings


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def homes_on(when: datetime | date | None, homes: list[Home] | None = None) -> list[Home]:
    homes = settings.homes if homes is None else homes
    day = when.date() if isinstance(when, datetime) else when
    return [h for h in homes if h.covers(day)]


def km_from_home(lat: float | None, lon: float | None, when, homes: list[Home] | None = None) -> float | None:
    """Distance to the nearest home lived in at `when`; None if unknown."""
    current = homes_on(when, homes)
    if lat is None or lon is None or not current:
        return None
    return min(haversine_km(lat, lon, h.lat, h.lon) for h in current)


def is_near_home(lat: float | None, lon: float | None, when) -> bool:
    km = km_from_home(lat, lon, when)
    return km is not None and km * 1000 <= settings.home_radius_m


@lru_cache(maxsize=1)
def _geocoder():
    # Offline GeoNames lookup — locations never leave the machine/server.
    import reverse_geocoder

    return reverse_geocoder.RGeocoder(mode=1, verbose=False)


def place_name(lat: float, lon: float) -> str:
    hit = _geocoder().query([(lat, lon)])[0]
    region = hit["admin1"] if hit["cc"] == "US" else hit["cc"]
    return f"{hit['name']}, {region}"
