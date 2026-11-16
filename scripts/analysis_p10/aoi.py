"""Project 10 areas of interest (WGS 84 / UTM 53N, EPSG:32653, metres)."""

EPSG = 32653

# Lon/lat box covering Blue Corner, Siaes Corner, Ulong and German Channel.
WEST_LONLAT = (134.19, 7.10, 134.30, 7.33)
# Same box in UTM 53N (rounded out to 10 m).
WEST_UTM = (410500, 784800, 422800, 810300)

SITES = {
    "blue-corner": {"lat": 7.135, "lon": 134.220, "utm": (413871, 788746)},
    "siaes-corner": {"lat": 7.312, "lon": 134.225, "utm": (414456, 808313)},
    "ulong-channel": {"lat": 7.2833, "lon": 134.2456, "utm": (416725, 805136)},
    "german-channel": {"lat": 7.1210, "lon": 134.2720, "utm": (419610, 787188)},
}


def box(site: str, half: float = 1000) -> tuple:
    x, y = SITES[site]["utm"]
    return (x - half, y - half, x + half, y + half)


# Pilot moored-boat boxes: 3 x 3 km around the two busiest sites (both on tile 53NMH).
BOAT_BOXES = {
    "blue-corner": box("blue-corner", 1500),
    "german-channel": box("german-channel", 1500),
}
