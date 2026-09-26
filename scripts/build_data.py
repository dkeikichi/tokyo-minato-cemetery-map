"""Build data/minato.json for the Minato cemetery-distance map from the Overture extracts in work/overture/."""
import base64, json, math, re, zlib, collections
import numpy as np
import pyarrow.parquet as pq
import shapely
from shapely import wkb
from shapely.geometry import Point, box, mapping

R = 6378137.0
LAT0 = 35.652
COS0 = math.cos(math.radians(LAT0))
PIX_M = 8.0                     # true metres per raster pixel
S = PIX_M / COS0                # mercator units per pixel
MARGIN_M = 700.0                # cemeteries this far outside the ward still count


def merc(lon, lat):
    return R * np.radians(lon), R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def unmerc(x, y):
    return np.degrees(x / R), np.degrees(2 * np.arctan(np.exp(y / R)) - np.pi / 2)


def to_m(g):
    """lon/lat geometry -> local true-metre geometry (mercator scaled by cos(lat0))."""
    return shapely.transform(g, lambda c: np.column_stack(merc(c[:, 0], c[:, 1])) * COS0)


def names(r):
    return ((r.get('names') or {}).get('primary') or '').strip()


# ---------------------------------------------------------------- ward
da = pq.read_table('work/overture/division_area.parquet').to_pylist()
WARD = [wkb.loads(r['geometry']) for r in da if r['subtype'] == 'locality' and names(r) == '港区'][0]
WARD_M = to_m(WARD)
NEAR_M = WARD_M.buffer(MARGIN_M)
VIEW_M = WARD_M.buffer(1500)
VIEW_BOX = box(*shapely.transform(VIEW_M.envelope, lambda c: np.column_stack(unmerc(c[:, 0] / COS0, c[:, 1] / COS0))).bounds)

# ---------------------------------------------------------------- grid
wx0, wy0, wx1, wy1 = shapely.transform(WARD_M.buffer(MARGIN_M), lambda c: c / COS0).bounds  # mercator
X0 = math.floor(wx0 / S) * S
Y1 = math.ceil(wy1 / S) * S
NX = int(math.ceil((wx1 - X0) / S))
NY = int(math.ceil((Y1 - wy0) / S))
print('grid', NX, NY, NX * NY)
cx = X0 + (np.arange(NX) + 0.5) * S
cy = Y1 - (np.arange(NY) + 0.5) * S
GX, GY = np.meshgrid(cx, cy)
GLON, GLAT = unmerc(GX, GY)


def raster(geom):
    """Boolean mask of pixel centres inside a lon/lat geometry."""
    out = np.zeros((NY, NX), bool)
    if geom.is_empty:
        return out
    x0, y0, x1, y1 = geom.bounds
    c0 = max(0, np.searchsorted(GLON[0], x0) - 1); c1 = min(NX, np.searchsorted(GLON[0], x1) + 1)
    col = GLAT[:, 0]
    r0 = max(0, np.searchsorted(-col, -y1) - 1); r1 = min(NY, np.searchsorted(-col, -y0) + 1)
    if c1 <= c0 or r1 <= r0:
        return out
    shapely.prepare(geom)
    out[r0:r1, c0:c1] = shapely.contains_xy(geom, GLON[r0:r1, c0:c1], GLAT[r0:r1, c0:c1])
    return out


ward_mask = raster(WARD)

# ---------------------------------------------------------------- encoding helpers
OX, OY = 139.6, 35.6


def enc_line(coords):
    pts = np.round((np.asarray(coords)[:, :2] - [OX, OY]) * 1e5).astype(int)
    if len(pts) < 2:
        return None
    d = np.vstack([pts[:1], np.diff(pts, axis=0)])
    keep = np.ones(len(d), bool)
    keep[1:] = np.any(d[1:] != 0, axis=1)
    d = d[keep]
    if len(d) < 2:
        return None
    return d.flatten().tolist()


def enc_poly(g):
    polys = [g] if g.geom_type == 'Polygon' else list(getattr(g, 'geoms', []))
    out = []
    for p in polys:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        rings = [enc_line(p.exterior.coords)] + [enc_line(i.coords) for i in p.interiors]
        rings = [r for r in rings if r and len(r) >= 6]
        if rings:
            out.append(rings)
    return out


def simp(g, tol_m):
    return g.simplify(tol_m * 1e-5 / 1.1, preserve_topology=True)


# ---------------------------------------------------------------- places: temples
pl = pq.read_table('work/overture/place.parquet', columns=['names', 'basic_category', 'confidence', 'geometry', 'operating_status']).to_pylist()
TEMPLE_RE = re.compile(r'(寺|院|庵|堂|坊)')
temples = []
for r in pl:
    n = names(r)
    if r['basic_category'] not in ('buddhist_place_of_worship', 'religious_organization', 'religious_landmark'):
        continue
    if not n or not TEMPLE_RE.search(n) or (r['confidence'] or 0) < 0.5:
        continue
    if any(w in n for w in ('会館', '事務所', '幼稚園', '保育', '病院', '学院', '斎場', 'ホール', '霊廟')):
        continue
    p = wkb.loads(r['geometry'])
    pm = to_m(p)
    if not NEAR_M.contains(pm):
        continue
    temples.append({'name': n, 'pt': p, 'pm': pm, 'conf': r['confidence'] or 0})
# dedupe: same leading name within 120 m -> keep highest confidence / shortest name
temples.sort(key=lambda t: (-t['conf'], len(t['name'])))
kept = []
for t in temples:
    base = re.split(r'[ 　\(（・]', t['name'])[0]
    base = re.sub(r'(山門|本堂|客殿|境内)$', '', base) or t['name']
    if any((k['base'] == base or base.startswith(k['base']) or k['base'].startswith(base)) and k['pm'].distance(t['pm']) < 120 for k in kept):
        continue
    t['base'] = base
    kept.append(t)
temples = kept
print('temples', len(temples))

# ---------------------------------------------------------------- land use
lu = pq.read_table('work/overture/land_use.parquet').to_pylist()
cems = []
nonres_geoms = []
parks = []
for r in lu:
    g = wkb.loads(r['geometry'])
    if g.geom_type not in ('Polygon', 'MultiPolygon'):
        continue
    gm = to_m(g)
    sub, cls, n = r['subtype'], r['class'], names(r)
    if sub == 'cemetery':
        if gm.intersects(NEAR_M):
            cems.append({'g': g, 'gm': gm, 'name': n, 'cls': cls})
        continue
    if not gm.intersects(VIEW_M):
        continue
    if sub == 'park' or (sub == 'horticulture' and cls == 'garden' and gm.area > 3000) or (sub == 'recreation' and cls in ('recreation_ground',)):
        parks.append(g)
    big_garden = sub == 'horticulture' and cls == 'garden' and gm.area > 5000
    if (sub in ('park', 'transportation', 'military') or cls in ('industrial', 'university') or big_garden
            or n in ('赤坂御用地',)):
        nonres_geoms.append(g)

# name cemeteries from nearby temples
for c in cems:
    if c['name']:
        c['label'] = {'Zenkoji Temple Cemetary': '善光寺墓地'}.get(c['name'], c['name'])
        c['kind'] = '霊園'
        continue
    best, bd = None, 1e9
    for t in temples:
        d = c['gm'].distance(t['pm'])
        if d < bd:
            best, bd = t, d
    if best and bd <= 70:
        c['label'] = f"{best['base']}の墓地"
        c['kind'] = '寺院墓地'
    else:
        c['label'] = '墓地（名称不明）'
        c['kind'] = '墓地'
    c['temple_d'] = bd
print('cemeteries', len(cems), collections.Counter(c['kind'] for c in cems))

# temples with no mapped graveyard within 60 m (candidates for the "also avoid temples" toggle)
cem_union_m = shapely.union_all([c['gm'] for c in cems])
for t in temples:
    t['has_cem'] = t['pm'].distance(cem_union_m) <= 60

# ---------------------------------------------------------------- water
wt = pq.read_table('work/overture/water.parquet').to_pylist()
water = []
for r in wt:
    g = wkb.loads(r['geometry'])
    if r['subtype'] in ('human_made', 'spring') or r['class'] in ('swimming_pool', 'reflecting_pool', 'fountain'):
        continue
    if g.geom_type not in ('Polygon', 'MultiPolygon'):
        continue
    g = g.intersection(VIEW_BOX)
    if g.is_empty:
        continue
    water.append(g)
    nonres_geoms.append(g)
water_u = shapely.union_all(water)

# ---------------------------------------------------------------- zones (丁目)
CHOME_RE = re.compile(r'^(.+?)([一二三四五六七八九十]+丁目)$')
nb = [r for r in da if r['subtype'] in ('neighborhood', 'microhood')]
chome, townfill = [], {}
for r in nb:
    g = wkb.loads(r['geometry'])
    n = names(r)
    if not WARD.contains(g.representative_point()):
        continue
    g = g.intersection(WARD)
    if g.is_empty:
        continue
    m = CHOME_RE.match(n)
    if r['subtype'] == 'neighborhood' and m:
        chome.append({'name': n, 'town': m.group(1), 'g': g})
    else:
        townfill.setdefault(n, []).append(g)
chome_towns = {c['town'] for c in chome}
# neighbourhoods without 丁目 whose town has no chome polygons are themselves chome-level (麻布永坂町 etc.)
for n, gs in list(townfill.items()):
    if n not in chome_towns and any(r['subtype'] == 'neighborhood' and names(r) == n for r in nb):
        chome.append({'name': n, 'town': n, 'g': shapely.union_all(gs)})
        del townfill[n]
townfill = {n: shapely.union_all(gs) for n, gs in townfill.items() if n in chome_towns}
KANJI = '一二三四五六七八九十'


def chome_key(c):
    m = CHOME_RE.match(c['name'])
    num = 0
    if m:
        s = m.group(2)[:-2]
        num = KANJI.index(s[-1]) + 1 + (10 if len(s) > 1 else 0)
    return (c['town'], num)


chome.sort(key=chome_key)
zone = np.zeros((NY, NX), np.uint8)
zones = []
for c in chome:
    msk = raster(c['g']) & ward_mask & (zone == 0)
    if msk.sum() == 0:
        continue
    zones.append({'name': c['name'], 'town': c['town'], 'pseudo': False})
    zone[msk] = len(zones)
for n, g in sorted(townfill.items()):
    msk = raster(g) & ward_mask & (zone == 0)
    if msk.sum() < 20:
        continue
    zones.append({'name': f'{n}（その他）', 'town': n, 'pseudo': True})
    zone[msk] = len(zones)
# remaining ward pixels -> town of nearest zone
from scipy import ndimage
rest = ward_mask & (zone == 0)
if rest.any():
    _, (iy, ix) = ndimage.distance_transform_edt(zone == 0, return_indices=True)
    near_town = {}
    for (y, x) in zip(*np.nonzero(rest)):
        t = zones[zone[iy[y, x], ix[y, x]] - 1]['town']
        near_town.setdefault(t, []).append((y, x))
    for t, px in sorted(near_town.items()):
        zones.append({'name': f'{t}（その他）', 'town': t, 'pseudo': True})
        ys, xs = zip(*px)
        zone[list(ys), list(xs)] = len(zones)
assert len(zones) < 255
print('zones', len(zones), 'real', sum(not z['pseudo'] for z in zones))

nonres = np.zeros((NY, NX), bool)
for g in nonres_geoms:
    nonres |= raster(g)
for c in cems:
    nonres |= raster(c['g'])
nonres &= ward_mask
water_mask = raster(water_u) & ward_mask
flags = nonres.astype(np.uint8) | (water_mask.astype(np.uint8) << 1)
print('nonres share', nonres.sum() / ward_mask.sum(), 'land km2', (ward_mask & ~water_mask).sum() * PIX_M**2 / 1e6)

# town label points
towns = {}
for i, z in enumerate(zones):
    towns.setdefault(z['town'], []).append(i + 1)
town_labels = []
for t, ids in towns.items():
    msk = np.isin(zone, ids) & ~nonres
    if msk.sum() == 0:
        msk = np.isin(zone, ids)
    g = shapely.union_all([c['g'] for c in chome if c['town'] == t] + ([townfill[t]] if t in townfill else []))
    if g.is_empty:
        ys, xs = np.nonzero(msk)
        p = (float(GLON[int(ys.mean()), int(xs.mean())]), float(GLAT[int(ys.mean()), int(xs.mean())]))
    else:
        rp = g.representative_point() if not g.centroid.within(g) else g.centroid
        p = (rp.x, rp.y)
    town_labels.append({'name': t, 'lon': round(p[0], 5), 'lat': round(p[1], 5)})
zone_labels = []
for i, z in enumerate(zones):
    if z['pseudo']:
        continue
    c = next(c for c in chome if c['name'] == z['name'])
    rp = c['g'].centroid if c['g'].centroid.within(c['g']) else c['g'].representative_point()
    zone_labels.append([i + 1, round(rp.x, 5), round(rp.y, 5)])

# ---------------------------------------------------------------- stations
inf = pq.read_table('work/overture/infrastructure.parquet', columns=['subtype', 'class', 'names', 'geometry']).to_pylist()
st_raw = [(names(r), wkb.loads(r['geometry'])) for r in inf if r['class'] in ('railway_station', 'subway_station') and names(r)]
groups = []
for n, g in st_raw:
    p = g.centroid
    pm = to_m(p)
    for gr in groups:
        if gr['name'] == n and gr['pm'].distance(pm) < 700:
            gr['pts'].append(p); break
    else:
        groups.append({'name': n, 'pm': pm, 'pts': [p]})
stations = []
for gr in groups:
    lon = float(np.mean([p.x for p in gr['pts']])); lat = float(np.mean([p.y for p in gr['pts']]))
    pm = to_m(Point(lon, lat))
    if not VIEW_M.contains(pm) or gr['name'].startswith('モノレール'):
        continue
    d = WARD_M.distance(pm)
    stations.append({'name': gr['name'].replace('〈', '（').replace('〉', '）'), 'lon': round(lon, 5), 'lat': round(lat, 5),
                     'inWard': bool(d <= 150), 'n': len(gr['pts'])})
print('stations', len(stations), sum(s['inWard'] for s in stations))

# ---------------------------------------------------------------- roads & rail
seg = pq.read_table('work/overture/segment.parquet', columns=['subtype', 'class', 'names', 'geometry', 'road_flags', 'rail_flags']).to_pylist()
MAJOR = {'motorway': 0, 'trunk': 1, 'primary': 1, 'secondary': 2, 'tertiary': 2, 'residential': 3, 'unclassified': 3, 'living_street': 3}
roads = [[], [], [], []]
rail, subway = [], []
for r in seg:
    g = wkb.loads(r['geometry'])
    if not g.intersects(VIEW_BOX):
        continue
    g = g.intersection(VIEW_BOX)
    lines = [g] if g.geom_type == 'LineString' else [x for x in getattr(g, 'geoms', []) if x.geom_type == 'LineString']
    if r['subtype'] == 'road' and r['class'] in MAJOR:
        tgt = roads[MAJOR[r['class']]]
    elif r['subtype'] == 'rail' and r['class'] in ('standard_gauge', 'light_rail', 'monorail', 'unknown'):
        tgt = rail
    elif r['subtype'] == 'rail' and r['class'] == 'subway':
        tgt = subway
    else:
        continue
    for ln in lines:
        e = enc_line(simp(ln, 1.5).coords)
        if e:
            tgt.append(e)
print('roads', [len(x) for x in roads], 'rail', len(rail), 'subway', len(subway))

# ---------------------------------------------------------------- output
def cem_out(c):
    g = simp(c['g'], 0.8)
    return {'name': c['label'], 'kind': c['kind'], 'area': int(round(c['gm'].area)), 'inWard': bool(c['gm'].intersects(WARD_M)),
            'poly': enc_poly(g)}


def b64z(a):
    return base64.b64encode(zlib.compress(a.astype(np.uint8).tobytes(), 9)).decode()


# simplify water & parks
water_out = enc_poly(simp(water_u, 2.0))
park_out = enc_poly(simp(shapely.union_all(parks).intersection(VIEW_BOX), 1.5))
ward_out = enc_poly(simp(WARD, 1.0))
view_out = [round(v, 5) for v in VIEW_BOX.bounds]

data = {
    'origin': [OX, OY],
    'grid': {'x0': X0, 'y1': Y1, 's': S, 'nx': NX, 'ny': NY, 'pixM': PIX_M,
             'zone': b64z(zone), 'flags': b64z(flags)},
    'ward': ward_out,
    'view': view_out,
    'zones': [{'name': z['name'], 'town': z['town'], 'pseudo': z['pseudo'],
               'poly': [] if z['pseudo'] else enc_poly(simp(next(c['g'] for c in chome if c['name'] == z['name']), 2.5))} for z in zones],
    'zoneLabels': zone_labels,
    'townLabels': town_labels,
    'cems': [cem_out(c) for c in cems],
    'temples': [{'name': t['name'], 'lon': round(t['pt'].x, 5), 'lat': round(t['pt'].y, 5), 'cem': t['has_cem']} for t in temples],
    'stations': stations,
    'water': water_out,
    'parks': park_out,
    'roads': roads,
    'rail': rail,
    'subway': subway,
    'source': 'Overture Maps Foundation release 2026-09-23.1 (base / divisions / places / transportation)。地図データの大部分は © OpenStreetMap contributors (ODbL)。',
}
s = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
open('data/minato.json', 'w').write(s)
print('data/minato.json bytes', len(s.encode()))
for k, v in data.items():
    print(' ', k, len(json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode()))
