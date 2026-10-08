import math

# City centres (WGS84) — used to place the origin/destination of a trip on the map.
CITY_COORDS = {
    "Kathmandu": (27.7172, 85.3240),
    "Pokhara": (28.2096, 83.9856),
    "Beni": (28.3500, 83.5667),
    "Bhairahawa": (27.5000, 83.4500),
    "Janakpur": (26.7288, 85.9266),
    "Jayanagar": (26.5833, 85.9167),
}


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def lerp(a, b, t):
    return a + (b - a) * t


def cumulative_km(points):
    cum = [0.0]
    for (a, b), (c, d) in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_km(a, b, c, d))
    return cum


def resample(points, n=300):
    """Pick ~n points evenly spaced by distance along a long road polyline (keeps both ends)."""
    if len(points) <= n:
        return [list(p) for p in points]
    cum = cumulative_km(points)
    total = cum[-1]
    out, j = [], 0
    for i in range(n):
        target = total * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j]
        t = (target - cum[j]) / seg if seg else 0
        out.append([lerp(points[j][0], points[j + 1][0], t), lerp(points[j][1], points[j + 1][1], t)])
    return out


def point_at(points, fraction):
    """Point `fraction` (0..1) of the way along the polyline, by distance."""
    cum = cumulative_km(points)
    target = cum[-1] * max(0.0, min(1.0, fraction))
    for j in range(len(points) - 1):
        if cum[j + 1] >= target:
            seg = cum[j + 1] - cum[j]
            t = (target - cum[j]) / seg if seg else 0
            return lerp(points[j][0], points[j + 1][0], t), lerp(points[j][1], points[j + 1][1], t)
    return tuple(points[-1])


def snap_fraction(points, lat, lng):
    """Fraction (0..1) along the polyline of the vertex nearest to (lat, lng)."""
    cum = cumulative_km(points)
    best = min(range(len(points)), key=lambda i: (points[i][0] - lat) ** 2 + (points[i][1] - lng) ** 2)
    return cum[best] / cum[-1] if cum[-1] else 0.0
