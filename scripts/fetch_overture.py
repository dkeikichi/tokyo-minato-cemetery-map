"""Download the Overture Maps features around Minato ward into work/overture/.

Usage (from minato-cemetery-map/):
    AWS_CA_BUNDLE=... python3 scripts/fetch_overture.py
Without arguments every layer used by build_data.py is fetched.
"""
import os, sys, time, pyarrow.fs as pafs, pyarrow.dataset as ds, pyarrow.compute as pc, pyarrow.parquet as pq
REL='overturemaps-us-west-2/release/2026-09-23.1'
XMIN,YMIN,XMAX,YMAX=139.698,35.612,139.792,35.692
OUT='work/overture'
os.makedirs(OUT, exist_ok=True)
proxy=os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
fs=pafs.S3FileSystem(anonymous=True, region='us-west-2', **({'proxy_options': proxy} if proxy else {}))
def fetch(theme,typ):
    t=time.time()
    d=ds.dataset(f'{REL}/theme={theme}/type={typ}/', filesystem=fs, format='parquet')
    flt=(pc.field('bbox','xmin')<XMAX)&(pc.field('bbox','xmax')>XMIN)&(pc.field('bbox','ymin')<YMAX)&(pc.field('bbox','ymax')>YMIN)
    tbl=d.to_table(filter=flt)
    pq.write_table(tbl, f'{OUT}/{typ}.parquet')
    print(theme,typ,tbl.num_rows,round(time.time()-t,1),flush=True)
LAYERS=['base/land_use','base/water','base/infrastructure','divisions/division_area','places/place','transportation/segment']
for a in sys.argv[1:] or LAYERS:
    th,ty=a.split('/')
    fetch(th,ty)
