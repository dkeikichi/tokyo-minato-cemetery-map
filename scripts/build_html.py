"""Assemble index.html from src/template.html, data/minato.json and Leaflet's stylesheet.

Leaflet's CSS is inlined (the JS loads from cdnjs), so the page works as a single file.
Pass --fragment to also write build/artifact.html without the <html>/<head>/<body> wrapper.
"""
import io, re, sys, tarfile, urllib.request, os

LEAFLET_VERSION = '1.9.4'
CDN = f'https://cdnjs.cloudflare.com/ajax/libs/leaflet/{LEAFLET_VERSION}/leaflet.js'


def leaflet_css():
    url = f'https://registry.npmjs.org/leaflet/-/leaflet-{LEAFLET_VERSION}.tgz'
    with urllib.request.urlopen(url) as r:
        tf = tarfile.open(fileobj=io.BytesIO(r.read()), mode='r:gz')
    css = tf.extractfile('package/dist/leaflet.css').read().decode().replace('\r\n', '\n')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    return re.sub(r'\n\s*\n+', '\n', css)


tpl = open('src/template.html').read()
data = open('data/minato.json').read().replace('</', '<\\/')
frag = tpl.replace('/*__LEAFLET_CSS__*/', leaflet_css()).replace('__DATA_JSON__', data).replace('__LEAFLET_JS__', CDN)
head, body = frag.split('<div class="app">', 1)
full = ('<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + head.strip() + '\n</head>\n<body>\n<div class="app">' + body.rstrip() + '\n</body>\n</html>\n')
open('index.html', 'w').write(full)
if '--fragment' in sys.argv:
    os.makedirs('build', exist_ok=True)
    open('build/artifact.html', 'w').write(frag)
print('index.html', len(full.encode()), 'bytes')
