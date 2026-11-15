"""Small local metric grid and polygon clipping for the UPC forecast area."""
import math

LON_METERS = 111320 * math.cos(math.radians(34.025))
LAT_METERS = 111320


def xy(point):
    return [(point[0] + 118.3) * LON_METERS, (point[1] - 34.01) * LAT_METERS]


def lonlat(point):
    return [point[0] / LON_METERS - 118.3, point[1] / LAT_METERS + 34.01]


def area(polygon):
    return abs(sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def clip(polygon, axis, value, greater):
    if not polygon:
        return []
    result = []
    for a, b in zip(polygon[-1:] + polygon[:-1], polygon):
        ia, ib = (a[axis] >= value) == greater, (b[axis] >= value) == greater
        if ia != ib:
            f = (value - a[axis]) / (b[axis] - a[axis])
            result.append([a[0] + f*(b[0]-a[0]), a[1] + f*(b[1]-a[1])])
        if ib:
            result.append(b)
    return result


def grid(boundary, size=250):
    polygon = [xy(p) for p in boundary['geometry']['coordinates'][0][:-1]]
    cells = []
    for ix in range(math.floor(min(p[0] for p in polygon)/size), math.ceil(max(p[0] for p in polygon)/size)):
        for iy in range(math.floor(min(p[1] for p in polygon)/size), math.ceil(max(p[1] for p in polygon)/size)):
            points = polygon
            for axis, value, greater in [(0, ix*size, True), (0, (ix+1)*size, False), (1, iy*size, True), (1, (iy+1)*size, False)]:
                points = clip(points, axis, value, greater)
            if len(points) >= 3 and area(points) > .01:
                ring = [lonlat(p) for p in points + points[:1]]
                cells.append({'id': f'{ix}:{iy}', 'ix': ix, 'iy': iy, 'area_m2': area(points),
                    'center': lonlat([sum(p[0] for p in points)/len(points), sum(p[1] for p in points)/len(points)]),
                    'geometry': {'type': 'Polygon', 'coordinates': [ring]}})
    return cells


def cell_id(lon, lat, size=250):
    x, y = xy([lon, lat])
    return f'{math.floor(x/size)}:{math.floor(y/size)}'


def inside(point, ring):
    x, y = point
    result = False
    for a, b in zip(ring, ring[1:]):
        cross = (x-a[0])*(b[1]-a[1])-(y-a[1])*(b[0]-a[0])
        if abs(cross) < 1e-12 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return True
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            result = not result
    return result


def line_parts(a, b, ring, size=250):
    """Split at boundary/grid intersections, retaining exact in-polygon pieces."""
    p, q = xy(a), xy(b)
    delta = [q[0]-p[0], q[1]-p[1]]
    cuts = [0., 1.]
    for axis in (0, 1):
        if abs(delta[axis]) < 1e-9:
            continue
        for n in range(math.floor(min(p[axis], q[axis])/size)+1, math.ceil(max(p[axis], q[axis])/size)):
            cuts.append((n*size-p[axis])/delta[axis])
    def cross(u, v):
        return u[0]*v[1]-u[1]*v[0]
    for c, d in zip(ring, ring[1:]):
        c, d = xy(c), xy(d)
        edge = [d[0]-c[0], d[1]-c[1]]
        denominator = cross(delta, edge)
        if abs(denominator) < 1e-9:
            continue
        offset = [c[0]-p[0], c[1]-p[1]]
        t, u = cross(offset, edge)/denominator, cross(offset, delta)/denominator
        if 0 < t < 1 and 0 <= u <= 1:
            cuts.append(t)
    cuts = sorted(set(round(t, 12) for t in cuts))
    for lo, hi in zip(cuts, cuts[1:]):
        midpoint = lonlat([p[i]+delta[i]*(lo+hi)/2 for i in (0, 1)])
        if hi-lo > 1e-9 and inside(midpoint, ring):
            first = lonlat([p[i]+delta[i]*lo for i in (0, 1)])
            last = lonlat([p[i]+delta[i]*hi for i in (0, 1)])
            yield first, last, cell_id(*midpoint, size), math.hypot(*delta)*(hi-lo)
