#!/usr/bin/env python3
"""
tile_app.py — all-in-one map tile downloader, server, viewer, and test suite.

Subcommands:
    python3 tile_app.py                          # launch GTK GUI (default)
    python3 tile_app.py gui                      # same as above
    python3 tile_app.py server [--cli] ...       # start tile server
    python3 tile_app.py download BOUNDS ZOOM ... # download tiles
    python3 tile_app.py clean CACHE_DIR [--delete]
    python3 tile_app.py view [-d DIR]            # tkinter viewer
    python3 tile_app.py test                     # run test suite

No external project imports — everything is inlined in this single file.
"""

import argparse
import http.server
import io
import math
import os
import socketserver
import sys
import threading
import time
import urllib.parse
import urllib.request
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageDraw

# Optional OCR / CV dependencies (used by server and viewer for text removal)
try:
    import numpy as np
    import cv2
    import pytesseract
    _HAVE_OCV = True
except ImportError:
    _HAVE_OCV = False

# Optional GeoTIFF export dependency
try:
    import tifffile
    _HAVE_TIFF = True
except ImportError:
    _HAVE_TIFF = False


# =========================================================================== #
# SECTION 1: tile_services  (service URLs, geometry, validation)
# =========================================================================== #

def _osm_url(z, x, y, s=None):
    return f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"

def _osm_hot_url(z, x, y, s=None):
    return f"https://tile.openstreetmap.org/hot/{z}/{x}/{y}.png"

def _osm_cycle_url(z, x, y, s=None):
    return f"https://tile.thunderforest.com/cycle/{z}/{x}/{y}.png"

def _opentopomap_url(z, x, y, s=None):
    return f"https://a.tile.opentopomap.org/{z}/{x}/{y}.png"

def _openseamap_url(z, x, y, s=None):
    return f"https://tiles.openseamap.org/seamap/{z}/{x}/{y}.png"

def _stamen_terrain_url(z, x, y, s="a"):
    return f"https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png"

def _stamen_toner_url(z, x, y, s="a"):
    return f"https://tiles.stadiamaps.com/tiles/stamen_toner/{z}/{x}/{y}.png"

def _stamen_watercolor_url(z, x, y, s="a"):
    return f"https://tiles.stadiamaps.com/tiles/stamen_watercolor/{z}/{x}/{y}.png"

def _carto_voyager_url(z, x, y, s="a"):
    return f"https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"

def _carto_positron_url(z, x, y, s="a"):
    return f"https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"

def _carto_darkmatter_url(z, x, y, s="a"):
    return f"https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"

def _esri_world_imagery_url(z, x, y, s=None):
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

def _esri_world_topo_url(z, x, y, s=None):
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}"

def _esri_world_street_url(z, x, y, s=None):
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"

def _esri_delorme_url(z, x, y, s=None):
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Reference_Overlay/MapServer/tile/{z}/{y}/{x}"

def _google_road_url(z, x, y, s=None):
    sub = s if s else str(z % 4)
    return f"https://mt{sub}.google.com/vt/lyrs=m&z={z}&x={x}&y={y}"

def _google_satellite_url(z, x, y, s=None):
    sub = s if s else str(z % 4)
    return f"https://mt{sub}.google.com/vt/lyrs=s&z={z}&x={x}&y={y}"

def _google_hybrid_url(z, x, y, s=None):
    sub = s if s else str(z % 4)
    return f"https://mt{sub}.google.com/vt/lyrs=y&z={z}&x={x}&y={y}"

def _google_terrain_url(z, x, y, s=None):
    sub = s if s else str(z % 4)
    return f"https://mt{sub}.google.com/vt/lyrs=p&z={z}&x={x}&y={y}"

def _tile_xy_to_quadkey(z, x, y):
    """Convert tile X/Y coordinates to a Bing Maps quadkey string."""
    quadkey = ""
    for i in range(z, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        quadkey += str(digit)
    return quadkey

def _bing_aerial_url(z, x, y, s=None):
    sub = s if s else str(z % 4)
    quadkey = _tile_xy_to_quadkey(z, x, y)
    return f"https://ecn.t{sub}.tiles.virtualearth.net/tiles/a{quadkey}.png?g=1"

def _bing_road_url(z, x, y, s=None):
    sub = s if s else str(z % 4)
    quadkey = _tile_xy_to_quadkey(z, x, y)
    return f"https://ecn.t{sub}.tiles.virtualearth.net/tiles/r{quadkey}.png?g=1"

def _here_normal_day_url(z, x, y, s=1):
    return f"https://maps.hereapi.com/v3/base/mc/{z}/{x}/{y}/png8?style=explore.day&size=256&apiKey=YOUR_API_KEY"

def _here_satellite_url(z, x, y, s=1):
    return f"https://maps.hereapi.com/v3/base/mc/{z}/{x}/{y}/png8?style=satellite.day&size=256&apiKey=YOUR_API_KEY"

def _yandex_map_url(z, x, y, s=None):
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}"

def _thunderforest_cycle_url(z, x, y, s="a"):
    return f"https://{s}.tile.thunderforest.com/cycle/{z}/{x}/{y}.png"

def _thunderforest_transport_url(z, x, y, s="a"):
    return f"https://{s}.tile.thunderforest.com/transport/{z}/{x}/{y}.png"

_OPENWEATHER_API_KEY = "8d997676c42fadd20c7efb5a3292d5ad"

def _openweather_url(z, x, y, s=None):
    return f"https://tile.openweathermap.org/map/temperature_new/{z}/{x}/{y}.png?appid={_OPENWEATHER_API_KEY}"

def _wikimedia_url(z, x, y, s=None):
    return f"https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"

def _osm_sia_url(z, x, y, s="a"):
    return f"https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png"

def _kosmik_url(z, x, y, s="a"):
    return f"https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png"

def _hikebike_url(z, x, y, s="a"):
    return f"https://{s}.tile.openstreetmap.fr/openriverboatmap/{z}/{x}/{y}.png"

def _humanitarian_url(z, x, y, s="a"):
    return f"https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png"


MAP_SERVICES = {
    "osm": {"name": "OpenStreetMap (Standard)", "url": _osm_url, "attribution": "© OpenStreetMap contributors"},
    "osm-hot": {"name": "OSM Hot (Humanitarian)", "url": _humanitarian_url, "subdomains": ["a", "b", "c"], "attribution": "© OpenStreetMap contributors, Humanitarian style"},
    "osm-cycle": {"name": "OSM Cycle", "url": _osm_cycle_url, "attribution": "© OpenStreetMap contributors, OpenCycleMap"},
    "osm-fr": {"name": "OSM French (OSMFR)", "url": _kosmik_url, "subdomains": ["a", "b", "c"], "attribution": "© OpenStreetMap France"},
    "osm-sia": {"name": "OSM German (OSM DE)", "url": _osm_sia_url, "subdomains": ["a", "b", "c"], "attribution": "© OpenStreetMap Germany"},
    "hikebike": {"name": "OpenRiverboatMap", "url": _hikebike_url, "subdomains": ["a", "b", "c"], "attribution": "© OpenStreetMap France, OpenRiverboatMap"},
    "opentopomap": {"name": "OpenTopoMap", "url": _opentopomap_url, "attribution": "© OpenTopoMap (CC-BY-SA)"},
    "openseamap": {"name": "OpenSeaMap (maritime)", "url": _openseamap_url, "attribution": "© OpenSeaMap"},
    "stamen-terrain": {"name": "Stamen Terrain", "url": _stamen_terrain_url, "attribution": "© Stadia Maps, © OpenStreetMap contributors"},
    "stamen-toner": {"name": "Stamen Toner (B/W)", "url": _stamen_toner_url, "attribution": "© Stadia Maps, © OpenStreetMap contributors"},
    "stamen-watercolor": {"name": "Stamen Watercolor", "url": _stamen_watercolor_url, "attribution": "© Stadia Maps, © OpenStreetMap contributors"},
    "thunderforest-cycle": {"name": "Thunderforest Cycle", "url": _thunderforest_cycle_url, "subdomains": ["a", "b", "c"], "attribution": "© Thunderforest, © OpenStreetMap contributors"},
    "thunderforest-transport": {"name": "Thunderforest Transport", "url": _thunderforest_transport_url, "subdomains": ["a", "b", "c"], "attribution": "© Thunderforest, © OpenStreetMap contributors"},
    "carto-voyager": {"name": "CARTO Voyager", "url": _carto_voyager_url, "subdomains": ["a", "b", "c", "d"], "attribution": "© CARTO, © OpenStreetMap contributors"},
    "carto-positron": {"name": "CARTO Positron (light)", "url": _carto_positron_url, "subdomains": ["a", "b", "c", "d"], "attribution": "© CARTO, © OpenStreetMap contributors"},
    "carto-darkmatter": {"name": "CARTO Dark Matter", "url": _carto_darkmatter_url, "subdomains": ["a", "b", "c", "d"], "attribution": "© CARTO, © OpenStreetMap contributors"},
    "esri-imagery": {"name": "Esri World Imagery (satellite)", "url": _esri_world_imagery_url, "attribution": "© Esri, © Maxar"},
    "esri-topo": {"name": "Esri World Topo", "url": _esri_world_topo_url, "attribution": "© Esri"},
    "esri-street": {"name": "Esri World Street", "url": _esri_world_street_url, "attribution": "© Esri"},
    "esri-delorme": {"name": "Esri DeLorme (reference overlay)", "url": _esri_delorme_url, "attribution": "© Esri"},
    "google": {"name": "Google Maps (road)", "url": _google_road_url, "attribution": "© Google"},
    "google-satellite": {"name": "Google Maps (satellite)", "url": _google_satellite_url, "attribution": "© Google"},
    "google-hybrid": {"name": "Google Maps (hybrid)", "url": _google_hybrid_url, "attribution": "© Google"},
    "google-terrain": {"name": "Google Maps (terrain)", "url": _google_terrain_url, "attribution": "© Google"},
    "bing": {"name": "Bing Maps (aerial)", "url": _bing_aerial_url, "attribution": "© Bing"},
    "bing-road": {"name": "Bing Maps (road)", "url": _bing_road_url, "attribution": "© Bing"},
    "here-normal": {"name": "HERE Maps (normal day)", "url": _here_normal_day_url, "subdomains": [1, 2, 3, 4], "attribution": "© HERE", "status": "api-key"},
    "here-satellite": {"name": "HERE Maps (satellite)", "url": _here_satellite_url, "subdomains": [1, 2, 3, 4], "attribution": "© HERE", "status": "api-key"},
    "yandex": {"name": "Esri Shaded Relief", "url": _yandex_map_url, "attribution": "© Esri"},
    "wikimedia": {"name": "Esri Ocean", "url": _wikimedia_url, "attribution": "© Esri"},
    "openweather": {"name": "OpenWeatherMap (temperature)", "url": _openweather_url, "attribution": "© OpenWeatherMap"},
}

WATERMARK_REGIONS = {
    "google": [(70, 236, 116, 16)], "google-satellite": [(70, 236, 116, 16)],
    "google-hybrid": [(70, 236, 116, 16)], "google-terrain": [(70, 236, 116, 16)],
    "bing": [(4, 240, 90, 14)], "bing-road": [(4, 240, 90, 14)],
    "here-normal": [(4, 240, 70, 14)], "here-satellite": [(4, 240, 70, 14)],
    "yandex": [(4, 240, 70, 14)],
    "esri-imagery": [(160, 240, 92, 14)], "esri-topo": [(160, 240, 92, 14)],
    "esri-street": [(160, 240, 92, 14)], "esri-delorme": [(160, 240, 92, 14)],
    "osm": [], "osm-hot": [], "osm-cycle": [], "osm-fr": [], "osm-sia": [],
    "hikebike": [], "opentopomap": [], "openseamap": [],
    "stamen-terrain": [], "stamen-toner": [], "stamen-watercolor": [],
    "thunderforest-cycle": [], "thunderforest-transport": [],
    "carto-voyager": [], "carto-positron": [], "carto-darkmatter": [],
    "wikimedia": [], "openweather": [],
}
DEFAULT_REGIONS = [(0, 240, 256, 16)]


def lon2x(lon, z):
    return int((lon + 180) / 360 * 2**z)

def lat2y(lat, z):
    lat_rad = math.radians(lat)
    return int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * 2**z)

def tile_path(cache_dir, z, x, y):
    return os.path.join(cache_dir, str(z), f"{x}_{y}.png")

def compute_tile_range(min_lat, min_lon, max_lat, max_lon, z):
    max_tile = 1 << z
    x_start = max(0, min(lon2x(min_lon, z), max_tile - 1))
    x_end   = max(0, min(lon2x(max_lon, z), max_tile - 1))
    y_start = max(0, min(lat2y(max_lat, z), max_tile - 1))
    y_end   = max(0, min(lat2y(min_lat, z), max_tile - 1))
    return x_start, x_end, y_start, y_end

def build_url(svc, z, x, y, idx=0):
    url_fn = MAP_SERVICES[svc]["url"]
    subdomains = MAP_SERVICES[svc].get("subdomains")
    if subdomains:
        s = subdomains[idx % len(subdomains)]
        return url_fn(z, x, y, s)
    return url_fn(z, x, y)

def is_valid_tile(data):
    if not data or len(data) < 100:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


# =========================================================================== #
# SECTION 2: tile_server  (fetch, watermark, HTTP handler, GTK GUI)
# =========================================================================== #

USER_AGENT = "LocalTileServer/1.0 (contact: https://openstreetmap.org)"
FETCH_HEADERS = {"User-Agent": USER_AGENT, "Accept": "image/png,image/jpeg,*/*;q=0.8"}


def fetch_tile(svc, z, x, y, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    url = build_url(svc, z, x, y, 0)
    headers = dict(FETCH_HEADERS)
    if svc.startswith("stamen-"):
        headers["Referer"] = "http://localhost:8080/"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if not is_valid_tile(data):
            print(f"[warn] not a valid tile image z={z} x={x} y={y} (possibly 'access blocked')", file=sys.stderr)
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception as exc:
        print(f"[warn] fetch failed z={z} x={x} y={y}: {exc}", file=sys.stderr)
        return False


def remove_watermarks(data, regions):
    if not regions:
        return data
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return data
    draw = ImageDraw.Draw(img, "RGBA")
    for (rx, ry, rw, rh) in regions:
        draw.rectangle([(rx, ry), (rx + rw - 1, ry + rh - 1)], fill=(255, 255, 255, 0))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def remove_text_labels(data):
    if not _HAVE_OCV:
        return data
    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        cv_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if cv_img is None:
            return data
        data_ocr = pytesseract.image_to_data(cv_img, output_type=pytesseract.Output.DICT)
        mask = np.zeros(cv_img.shape[:2], dtype=np.uint8)
        for i, txt in enumerate(data_ocr["text"]):
            if not (txt or "").strip():
                continue
            x, y, w, h = (data_ocr["left"][i], data_ocr["top"][i],
                          data_ocr["width"][i], data_ocr["height"][i])
            pad = 2
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1 = min(cv_img.shape[1], x + w + pad)
            y1 = min(cv_img.shape[0], y + h + pad)
            mask[y0:y1, x0:x1] = 255
        if mask.any():
            cv_img = cv2.inpaint(cv_img, mask, 3, cv2.INPAINT_TELEA)
            ok, buf = cv2.imencode(".png", cv_img)
            if ok:
                return buf.tobytes()
    except Exception:
        pass
    return data


class TileHandler(http.server.BaseHTTPRequestHandler):
    cache_dir = "./tiles"
    proxy = False
    proxy_service = "osm"
    mask_service = None
    remove_text = False

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            self._serve_index()
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[2].endswith(".png"):
            try:
                z = int(parts[0]); x = int(parts[1]); y = int(parts[2][:-4])
            except ValueError:
                self.send_error(400, "Invalid tile coordinates")
                return
            self._serve_tile(z, x, y)
            return
        self.send_error(404, "Not found")

    def _serve_tile(self, z, x, y):
        dest = tile_path(self.cache_dir, z, x, y)
        if not os.path.exists(dest) and self.proxy:
            fetch_tile(self.proxy_service, z, x, y, dest)
        if not os.path.exists(dest):
            self.send_error(404, "Tile not available")
            return
        try:
            with open(dest, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(500, "Failed reading tile")
            return
        if self.mask_service:
            if self.mask_service == "__auto__":
                regions = DEFAULT_REGIONS
            else:
                regions = WATERMARK_REGIONS.get(self.mask_service, DEFAULT_REGIONS)
            if regions:
                data = remove_watermarks(data, regions)
        if self.remove_text:
            data = remove_text_labels(data)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self):
        port = self.server.server_address[1]
        attribution = MAP_SERVICES.get(
            TileHandler.proxy_service, {"attribution": "© local cache"}
        )["attribution"]
        layers_js = _build_layers_js(port)
        html = INDEX_HTML.format(port=port, attribution=attribution, layers_js=layers_js)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def _build_layers_js(port):
    """Build JS object literal of all base layers from MAP_SERVICES."""
    import json
    layers = []
    # Local cache first (default)
    layers.append(
        f'    "Local cache": L.tileLayer('
        f"'http://localhost:{port}/{{z}}/{{x}}/{{y}}.png', "
        f"{{maxZoom: 22, crossOrigin: true}})"
    )
    # Use unique large integers as sentinels to avoid collisions with
    # subdomain indices or z%x rotations in URL builder functions
    Z_VAL, X_VAL, Y_VAL = 777, 888, 999
    for key, info in MAP_SERVICES.items():
        name = info["name"]
        status = info.get("status")
        if status:
            suffix = {"offline": " (offline)", "broken": " (broken)", "api-key": " (needs API key)"}.get(status, "")
            display_name = name + suffix
        else:
            display_name = name
        url_fn = info["url"]
        subs = info.get("subdomains")
        # Generate URL with sentinel values
        if subs:
            s_val = subs[0]
            url = url_fn(Z_VAL, X_VAL, Y_VAL, s_val)
        else:
            url = url_fn(Z_VAL, X_VAL, Y_VAL, None)
        # Replace sentinel values with Leaflet template placeholders
        url_template = url.replace(str(Z_VAL), "{z}").replace(str(X_VAL), "{x}").replace(str(Y_VAL), "{y}")
        # Handle subdomain: replace the first subdomain value with {s}
        if subs:
            url_template = url_template.replace(str(subs[0]), "{s}", 1)
        # Skip if we couldn't template all three
        if "{z}" not in url_template or "{x}" not in url_template or "{y}" not in url_template:
            continue
        # Build subdomains option for Leaflet
        if subs:
            sub_str = ",".join(str(s) for s in subs)
            sub_opt = f", subdomains: '{sub_str}'"
        else:
            sub_opt = ""
        attribution = info.get("attribution", "")
        attr_escaped = attribution.replace("'", "\\'")
        layer_js = (
            f'    {json.dumps(display_name)}: L.tileLayer('
            f"'{url_template}', "
            f"{{maxZoom: 22, attribution: '{attr_escaped}'{sub_opt}}})"
        )
        layers.append(layer_js)
    return ",\n".join(layers)


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Local Tile Server</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    html, body {{ height: 100%; margin: 0; font-family: sans-serif; }}
    #map {{ position: absolute; top: 0; left: 0; right: 0; bottom: 0; }}
    #info {{ position: absolute; z-index: 1000; top: 10px; right: 10px;
      background: rgba(255,255,255,0.92); padding: 10px 14px;
      border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.3); font-size: 13px;
      min-width: 220px; }}
    #info .title {{ font-weight: bold; margin-bottom: 6px; }}
    #info .row {{ display: flex; gap: 6px; align-items: center; margin-top: 4px; }}
    #info input[type=text] {{ width: 140px; padding: 2px 4px; font-size: 13px; }}
    #info button {{ cursor: pointer; font-size: 13px; padding: 2px 8px; }}
    .copy-btn {{ cursor: pointer; font-size: 12px; color: #1976d2; border: none;
      background: none; padding: 0 4px; }}
    .copy-btn:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="info">
    <div class="title">Local Tile Server</div>
    <div>serving from cache — search via Nominatim</div>
    <div class="row">
      <input type="text" id="search" placeholder="town name…" minlength="3" />
      <button id="search-btn">Go</button>
    </div>
    <div id="search-status" style="color:#888;font-size:11px;margin-top:2px;"></div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map('map').setView([48.1374, 11.5980], 10);

    const baseLayers = {{
{layers_js}
    }};
    // Add the local cache layer to the map by default
    baseLayers["Local cache"].addTo(map);
    L.control.layers(baseLayers, null, {{collapsed: true, position: 'bottomright'}}).addTo(map);

    // --- click-to-copy + reverse geocode ---
    let searchReq = null;
    map.on('click', (e) => {{
      const c = e.latlng;
      const latLon = `${{c.lat.toFixed(5)}},${{c.lng.toFixed(5)}}`;
      const popupId = 'popup-' + Date.now();
      const html = `
        <div id="${{popupId}}">
          <b>lat,lon</b> = ${{c.lat.toFixed(5)}}, ${{c.lng.toFixed(5)}}
          <button class="copy-btn" onclick="navigator.clipboard.writeText('${{latLon}}')">Copy</button><br/>
          <b>zoom</b> = ${{map.getZoom()}}<br/>
          <b>Nearest:</b> <span id="${{popupId}}-rev">loading…</span>
        </div>`;
      L.popup().setLatLng(c).setContent(html).openOn(map);
      // Reverse geocode
      const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${{c.lat}}&lon=${{c.lng}}&zoom=14`;
      fetch(url).then(r => r.json()).then(data => {{
        const el = document.getElementById('${{popupId}}-rev');
        if (!el) return;
        const a = data.address || {{}};
        const name = a.city || a.town || a.village || a.hamlet || a.county || data.display_name || '—';
        el.textContent = name;
      }}).catch(() => {{
        const el = document.getElementById('${{popupId}}-rev');
        if (el) el.textContent = '—';
      }});
    }});

    // --- town name search ---
    function doSearch() {{
      const q = document.getElementById('search').value.trim();
      const st = document.getElementById('search-status');
      if (q.length < 3) {{ st.textContent = 'type at least 3 chars'; return; }}
      if (searchReq) searchReq.abort();
      st.textContent = 'searching…';
      const ctrl = new AbortController();
      searchReq = ctrl;
      fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${{encodeURIComponent(q)}}`,
              {{signal: ctrl.signal}})
        .then(r => r.json())
        .then(data => {{
          if (!data || !data.length) {{ st.textContent = 'no match'; return null; }}
          const r = data[0];
          st.textContent = r.display_name;
          map.setView([parseFloat(r.lat), parseFloat(r.lon)], 13);
          L.marker([parseFloat(r.lat), parseFloat(r.lon)])
            .addTo(map).bindPopup(r.display_name).openPopup();
        }})
        .catch(err => {{ if (err.name !== 'AbortError') st.textContent = 'error'; }});
    }}
    document.getElementById('search-btn').addEventListener('click', doSearch);
    document.getElementById('search').addEventListener('keydown', (e) => {{
      if (e.key === 'Enter') doSearch();
    }});
  </script>
</body>
</html>
"""


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server(host, port, cache_dir, proxy, proxy_service, mask_service,
               remove_text=False):
    TileHandler.cache_dir = os.path.abspath(cache_dir)
    TileHandler.proxy = proxy
    TileHandler.proxy_service = proxy_service
    TileHandler.mask_service = mask_service
    TileHandler.remove_text = remove_text
    server = ThreadingHTTPServer((host, port), TileHandler)
    print(f"Tile server starting on http://{host}:{port}/")
    print(f"  cache : {TileHandler.cache_dir}")
    print(f"  proxy : {'on (' + proxy_service + ')' if proxy else 'off'}")
    print(f"  mask  : {mask_service or 'off'}")
    print(f"  text  : {'on' if remove_text else 'off'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("Server stopped.")


# --- GTK GUI --------------------------------------------------------------- #

def _have_gtk():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        return True
    except Exception:
        return False


class TileServerGUI:
    def __init__(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib
        self.Gtk = Gtk
        self.GLib = GLib
        self.httpd = None
        self.server_thread = None
        self.dl_thread = None

        self.win = Gtk.Window()
        self.win.set_title("Local Tile Server")
        self.win.set_default_size(560, 560)
        self.win.connect("destroy", self._on_destroy)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.win.add(outer)

        frm_cache = Gtk.Frame(label="Cache Directory")
        frm_cache_h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        frm_cache.add(frm_cache_h)
        outer.pack_start(frm_cache, False, False, 0)
        self.cache_entry = Gtk.Entry()
        self.cache_entry.set_text(os.path.abspath("./tiles"))
        self.cache_entry.set_hexpand(True)
        frm_cache_h.pack_start(self.cache_entry, True, True, 6)
        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.connect("clicked", self._browse_cache)
        frm_cache_h.pack_start(btn_browse, False, False, 6)

        frm_srv = Gtk.Frame(label="Server")
        frm_srv_grid = Gtk.Grid()
        frm_srv_grid.set_column_spacing(8)
        frm_srv_grid.set_row_spacing(6)
        frm_srv.add(frm_srv_grid)
        outer.pack_start(frm_srv, False, False, 0)
        frm_srv_grid.attach(Gtk.Label(label="Host:"), 0, 0, 1, 1)
        self.host_combo = Gtk.ComboBoxText()
        for h in ["127.0.0.1", "0.0.0.0", "localhost"]:
            self.host_combo.append_text(h)
        self.host_combo.set_active(0)
        frm_srv_grid.attach(self.host_combo, 1, 0, 1, 1)
        frm_srv_grid.attach(Gtk.Label(label="Port:"), 2, 0, 1, 1)
        self.port_spin = Gtk.SpinButton.new_with_range(1024, 65535, 1)
        self.port_spin.set_value(8080)
        frm_srv_grid.attach(self.port_spin, 3, 0, 1, 1)

        frm_svc = Gtk.Frame(label="Map Service (for proxy & watermark masking)")
        frm_svc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        frm_svc.add(frm_svc_box)
        outer.pack_start(frm_svc, False, False, 0)
        svc_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        frm_svc_box.pack_start(svc_row, False, False, 4)
        svc_row.pack_start(Gtk.Label(label="Service:"), False, False, 6)
        self.svc_combo = Gtk.ComboBoxText()
        for key in MAP_SERVICES:
            self.svc_combo.append_text(key)
        self.svc_combo.set_active(0)
        self.svc_combo.connect("changed", self._on_svc_change)
        svc_row.pack_start(self.svc_combo, False, False, 6)
        self.svc_name_lbl = Gtk.Label(label="")
        self.svc_name_lbl.set_halign(Gtk.Align.START)
        frm_svc_box.pack_start(self.svc_name_lbl, False, False, 4)
        self._on_svc_change()

        frm_tog = Gtk.Frame(label="Options")
        frm_tog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        frm_tog.add(frm_tog_box)
        outer.pack_start(frm_tog, False, False, 0)
        self.proxy_check = Gtk.CheckButton(label="Proxy: fetch missing tiles on demand")
        frm_tog_box.pack_start(self.proxy_check, False, False, 4)
        self.mask_check = Gtk.CheckButton(label="Remove logos / watermarks (uses service profile)")
        self.mask_check.set_active(True)
        frm_tog_box.pack_start(self.mask_check, False, False, 4)
        self.text_check = Gtk.CheckButton(label="Remove text labels (OCR inpaint — slower)")
        self.text_check.set_active(False)
        frm_tog_box.pack_start(self.text_check, False, False, 4)

        # Download panel
        frm_dl = Gtk.Frame(label="Download")
        frm_dl_grid = Gtk.Grid()
        frm_dl_grid.set_column_spacing(8)
        frm_dl_grid.set_row_spacing(4)
        frm_dl.add(frm_dl_grid)
        outer.pack_start(frm_dl, False, False, 0)

        frm_dl_grid.attach(Gtk.Label(label="Service:"), 0, 0, 1, 1)
        self.dl_svc_combo = Gtk.ComboBoxText()
        for key in MAP_SERVICES:
            self.dl_svc_combo.append_text(key)
        self.dl_svc_combo.set_active(0)
        frm_dl_grid.attach(self.dl_svc_combo, 1, 0, 1, 1)
        frm_dl_grid.attach(Gtk.Label(label="Bounds:"), 2, 0, 1, 1)
        self.dl_bounds_entry = Gtk.Entry()
        self.dl_bounds_entry.set_placeholder_text("min_lat,min_lon,max_lat,max_lon")
        self.dl_bounds_entry.set_width_chars(28)
        frm_dl_grid.attach(self.dl_bounds_entry, 3, 0, 1, 1)
        frm_dl_grid.attach(Gtk.Label(label="Zoom:"), 4, 0, 1, 1)
        self.dl_zoom_entry = Gtk.Entry()
        self.dl_zoom_entry.set_text("10")
        self.dl_zoom_entry.set_width_chars(8)
        frm_dl_grid.attach(self.dl_zoom_entry, 5, 0, 1, 1)

        frm_dl_grid.attach(Gtk.Label(label="Output:"), 0, 1, 1, 1)
        self.dl_dir_entry = Gtk.Entry()
        self.dl_dir_entry.set_text(os.path.abspath("./tiles"))
        self.dl_dir_entry.set_hexpand(True)
        frm_dl_grid.attach(self.dl_dir_entry, 1, 1, 3, 1)
        self.dl_browse_btn = Gtk.Button(label="Browse…")
        self.dl_browse_btn.connect("clicked", self._dl_browse)
        frm_dl_grid.attach(self.dl_browse_btn, 4, 1, 1, 1)
        self.dl_btn = Gtk.Button(label="Download")
        self.dl_btn.connect("clicked", self._dl_start)
        frm_dl_grid.attach(self.dl_btn, 5, 1, 1, 1)

        self.dl_progress = Gtk.ProgressBar()
        self.dl_progress.set_show_text(True)
        frm_dl_grid.attach(self.dl_progress, 0, 2, 6, 1)
        self.dl_status_lbl = Gtk.Label(label="")
        self.dl_status_lbl.set_halign(Gtk.Align.START)
        frm_dl_grid.attach(self.dl_status_lbl, 0, 3, 6, 1)

        frm_btn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.pack_start(frm_btn, False, False, 0)
        self.btn_start = Gtk.Button(label="Start")
        self.btn_start.connect("clicked", self._start)
        frm_btn.pack_start(self.btn_start, False, False, 6)
        self.btn_stop = Gtk.Button(label="Stop")
        self.btn_stop.set_sensitive(False)
        self.btn_stop.connect("clicked", self._stop)
        frm_btn.pack_start(self.btn_stop, False, False, 6)
        self.btn_open = Gtk.Button(label="Open in browser")
        self.btn_open.connect("clicked", self._open_browser)
        frm_btn.pack_start(self.btn_open, False, False, 6)

        frm_status = Gtk.Frame(label="Status")
        frm_status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frm_status.add(frm_status_box)
        outer.pack_start(frm_status, True, True, 0)
        scr = Gtk.ScrolledWindow()
        scr.set_hexpand(True); scr.set_vexpand(True)
        frm_status_box.pack_start(scr, True, True, 4)
        self.status_buf = Gtk.TextBuffer()
        status_view = Gtk.TextView(buffer=self.status_buf)
        status_view.set_editable(False)
        status_view.set_monospace(True)
        scr.add(status_view)

        self._log("Ready. Auto-starting server and opening browser…")
        GLib.timeout_add_seconds(1, self._autostart)

    def _autostart(self):
        if not (self.server_thread and self.server_thread.is_alive()):
            self._start(None)
        self.GLib.timeout_add_seconds(1, self._open_browser_deferred)
        return False

    def _open_browser_deferred(self):
        self._open_browser(None)
        return False

    def _on_svc_change(self, _widget=None):
        key = self.svc_combo.get_active_text()
        info = MAP_SERVICES.get(key, {})
        name = info.get("name", "?")
        subs = info.get("subdomains")
        sub_txt = f"  (subdomains: {subs})" if subs else ""
        self.svc_name_lbl.set_text(f"{name}{sub_txt}")

    def _browse_cache(self, _widget):
        dialog = self.Gtk.FileChooserDialog(
            title="Select tiles cache directory", parent=self.win,
            action=self.Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons("Cancel", self.Gtk.ResponseType.CANCEL,
                           "Open", self.Gtk.ResponseType.OK)
        if dialog.run() == self.Gtk.ResponseType.OK:
            self.cache_entry.set_text(dialog.get_filename())
        dialog.destroy()

    def _start(self, _widget):
        if self.server_thread and self.server_thread.is_alive():
            return
        cache = self.cache_entry.get_text().strip()
        os.makedirs(cache, exist_ok=True)
        host = self.host_combo.get_active_text().strip()
        port = int(self.port_spin.get_value())
        svc = self.svc_combo.get_active_text()
        proxy = self.proxy_check.get_active()
        mask = self.mask_check.get_active()
        remove_text = self.text_check.get_active()
        mask_service = None
        if mask:
            mask_service = svc if svc in WATERMARK_REGIONS else "__auto__"
        TileHandler.cache_dir = os.path.abspath(cache)
        TileHandler.proxy = proxy
        TileHandler.proxy_service = svc
        TileHandler.mask_service = mask_service
        TileHandler.remove_text = remove_text

        def _serve():
            try:
                self.httpd = ThreadingHTTPServer((host, port), TileHandler)
                self._log(f"Server running on http://{host}:{port}/")
                self.httpd.serve_forever()
            except OSError as e:
                self._log(f"ERROR: {e}")
                self.httpd = None
            finally:
                self.GLib.idle_add(self._after_stop_ui)

        self.server_thread = threading.Thread(target=_serve, daemon=True)
        self.server_thread.start()
        self.btn_start.set_sensitive(False)
        self.btn_stop.set_sensitive(True)
        self._log(f"Starting on {host}:{port}  cache={cache}  service={svc}  proxy={proxy}  mask={mask}  text={remove_text}")

    def _stop(self, _widget):
        if self.httpd:
            self._log("Stopping server…")
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    def _after_stop_ui(self):
        self.btn_start.set_sensitive(True)
        self.btn_stop.set_sensitive(False)
        self._log("Server stopped.")
        return False

    def _open_browser(self, _widget):
        host = self.host_combo.get_active_text().strip()
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = int(self.port_spin.get_value())
        url = f"http://{host}:{port}/"
        import webbrowser
        webbrowser.open(url)
        self._log(f"Opening {url} in browser…")

    def _on_destroy(self, _widget):
        if self.httpd:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()
        self.GLib.idle_add(self.Gtk.main_quit)

    def _dl_browse(self, _widget):
        dialog = self.Gtk.FileChooserDialog(
            title="Select download output directory", parent=self.win,
            action=self.Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons("Cancel", self.Gtk.ResponseType.CANCEL,
                           "Open", self.Gtk.ResponseType.OK)
        if dialog.run() == self.Gtk.ResponseType.OK:
            self.dl_dir_entry.set_text(dialog.get_filename())
        dialog.destroy()

    def _dl_start(self, _widget):
        if self.dl_thread and self.dl_thread.is_alive():
            self._log("Download already running.")
            return
        svc = self.dl_svc_combo.get_active_text()
        bounds = self.dl_bounds_entry.get_text().strip()
        zoom_str = self.dl_zoom_entry.get_text().strip()
        out = self.dl_dir_entry.get_text().strip() or "./tiles"
        if not bounds:
            self._log("Error: bounds required for download.")
            return
        try:
            zooms = _parse_zoom(zoom_str)
        except ValueError as e:
            self._log(f"Error: {e}")
            return
        try:
            _parse_bounds(bounds)
        except ValueError as e:
            self._log(f"Error: {e}")
            return
        self.dl_btn.set_sensitive(False)
        self.dl_progress.set_fraction(0.0)
        self._log(f"Download start: {svc} z={zoom_str} bounds={bounds} -> {out}")
        zlabel = str(zooms[0]) if len(zooms) == 1 else f"{zooms[0]}-{zooms[-1]}"

        def worker():
            total_ok = total_fail = total_count = 0
            for z in zooms:
                def cb(done, total, failed, _z=z):
                    frac = done / total if total else 0.0
                    txt = f"z={_z}: {done}/{total} ({failed} failed)"
                    self.GLib.idle_add(self._dl_update_progress, frac, txt)
                fail = _download_all(svc, z, bounds, out, 10, progress_cb=cb)
                min_lat, min_lon, max_lat, max_lon = _parse_bounds(bounds)
                x0, x1, y0, y1 = compute_tile_range(min_lat, min_lon, max_lat, max_lon, z)
                t = (x1 - x0 + 1) * (y1 - y0 + 1)
                total_ok += t - fail
                total_fail += fail
                total_count += t
            self.GLib.idle_add(self._dl_done, total_ok, total_fail, total_count, zlabel)

        self.dl_thread = threading.Thread(target=worker, daemon=True)
        self.dl_thread.start()

    def _dl_update_progress(self, frac, txt):
        self.dl_progress.set_fraction(frac)
        self.dl_status_lbl.set_text(txt)
        return False

    def _dl_done(self, ok, fail, total, zlabel):
        self.dl_btn.set_sensitive(True)
        self.dl_progress.set_fraction(1.0 if total else 0.0)
        msg = f"Download done z={zlabel}: {ok} OK, {fail} failed of {total}."
        self.dl_status_lbl.set_text(msg)
        self._log(msg)
        return False

    def _log(self, msg):
        end = self.status_buf.get_end_iter()
        self.status_buf.insert(end, msg + "\n")

    def run(self):
        self.win.show_all()
        self.Gtk.main()


# =========================================================================== #
# SECTION 3: download_tiles  (download subcommand)
# =========================================================================== #

DL_HEADERS = {
    "User-Agent": "TileDownloader/1.0 (contact: https://openstreetmap.org)",
    "Accept": "image/png,image/jpeg,*/*;q=0.8",
}

def download_tile(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        return True
    try:
        headers = dict(DL_HEADERS)
        if "stadiamaps.com" in url:
            headers["Referer"] = "http://localhost:8080/"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if not is_valid_tile(data):
                return False
            with open(dest, "wb") as f:
                f.write(data)
        return True
    except Exception:
        return False

def _parse_bounds(bounds_str):
    parts = [float(x) for x in bounds_str.split(",")]
    if len(parts) != 4:
        raise ValueError("Bounds must contain exactly 4 comma-separated values")
    for name, val in [("min_lat", parts[0]), ("min_lon", parts[1]),
                      ("max_lat", parts[2]), ("max_lon", parts[3])]:
        if not (-90.0 <= val <= 90.0 if "lat" in name else -180.0 <= val <= 180.0):
            raise ValueError(f"{name}={val} is out of valid range")
    if parts[0] > parts[2] or parts[1] > parts[3]:
        raise ValueError("Bounding box is not ordered: min values must be smaller than max values")
    return parts

def _parse_zoom(arg):
    """Parse a zoom argument: '10' -> [10], '10-14' -> [10,11,12,13,14]."""
    arg = str(arg).strip()
    if "-" in arg:
        lo, hi = arg.split("-", 1)
        lo, hi = int(lo), int(hi)
        if lo > hi:
            lo, hi = hi, lo
        zooms = list(range(lo, hi + 1))
    else:
        zooms = [int(arg)]
    for z in zooms:
        if not (1 <= z <= 21):
            raise ValueError(f"zoom level {z} must be between 1 and 21")
    return zooms


# --- progress sidecar ------------------------------------------------------- #

def _progress_path(cache_dir):
    return os.path.join(cache_dir, ".progress.json")

def _progress_load(cache_dir):
    path = _progress_path(cache_dir)
    if not os.path.exists(path):
        return None
    try:
        import json
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None

def _progress_save(cache_dir, state):
    import json
    os.makedirs(cache_dir, exist_ok=True)
    path = _progress_path(cache_dir)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)

def _progress_clear(cache_dir):
    try:
        os.remove(_progress_path(cache_dir))
    except FileNotFoundError:
        pass


# --- progress bar (CLI) ----------------------------------------------------- #

def _print_progress(done, total, failed):
    if total <= 0:
        return
    width = 20
    filled = int(width * done / total)
    bar = "#" * filled + "-" * (width - filled)
    pct = 100 * done // total
    sys.stderr.write(f"\r[{bar}] {done}/{total} ({pct}%) — {failed} failed")
    sys.stderr.flush()


def _download_all(svc_name, z, bounds, cache_dir, num_workers, progress_cb=None):
    """Download tiles for one zoom level.

    progress_cb(done, total, failed) is invoked after each tile completes;
    if None, a CLI progress bar is drawn on stderr.
    """
    min_lat, min_lon, max_lat, max_lon = _parse_bounds(bounds)
    x_start, x_end, y_start, y_end = compute_tile_range(min_lat, min_lon, max_lat, max_lon, z)
    svc_label = MAP_SERVICES[svc_name]["name"]
    all_coords = [(x, y) for x in range(x_start, x_end + 1)
                  for y in range(y_start, y_end + 1)]

    # Resume from sidecar
    state = _progress_load(cache_dir)
    done_set = set()
    failed_prev = []
    if state and state.get("service") == svc_name and state.get("bounds") == bounds:
        zstate = state.get("zooms", {}).get(str(z))
        if zstate:
            failed_prev = [tuple(c) for c in zstate.get("failed", [])]
            done_set = set(all_coords) - set(failed_prev)
            # Further reduce done_set by checking which files actually exist
            done_set = {c for c in done_set
                        if os.path.exists(tile_path(cache_dir, z, c[0], c[1]))}
    # Also skip any tiles that already exist on disk (previously cached)
    work = [(x, y) for (x, y) in all_coords
            if not os.path.exists(tile_path(cache_dir, z, x, y))]

    total = len(all_coords)
    already = total - len(work)
    print(f"[{svc_label}] z={z}: {total} tiles "
          f"(x=[{x_start}-{x_end}], y=[{y_start}-{y_end}])")
    if already:
        print(f"  {already} already cached, fetching {len(work)}")
    print(f"Cache: {cache_dir}")

    done = already
    failed = 0
    failed_coords = list(failed_prev)

    if not work:
        if progress_cb:
            progress_cb(done, total, failed)
        else:
            _print_progress(done, total, failed)
            sys.stderr.write("\n")
        print(f"Done: {done - failed} OK, {failed} failed/missing.")
        return failed

    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        futures = {}
        idx = 0
        for (x, y) in work:
            url = build_url(svc_name, z, x, y, idx)
            idx += 1
            dest = tile_path(cache_dir, z, x, y)
            fut = pool.submit(download_tile, url, dest)
            futures[fut] = (x, y)
        for fut in as_completed(futures):
            coord = futures[fut]
            ok = False
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if ok:
                done += 1
            else:
                failed += 1
                failed_coords.append(coord)
            if progress_cb:
                progress_cb(done, total, failed)
            else:
                _print_progress(done, total, failed)
            # Save sidecar every 50 completions
            if (done + failed) % 50 == 0:
                _state_save(cache_dir, svc_name, bounds, z, total, done, failed, failed_coords)
    if not progress_cb:
        sys.stderr.write("\n")
    print(f"Done: {done - failed} OK, {failed} failed/missing.")

    # Update / clear sidecar
    if failed == 0:
        # This zoom is complete; remove from sidecar if present
        state = _progress_load(cache_dir)
        if state and state.get("zooms"):
            state["zooms"].pop(str(z), None)
            if not state["zooms"]:
                _progress_clear(cache_dir)
            else:
                _progress_save(cache_dir, state)
    else:
        _state_save(cache_dir, svc_name, bounds, z, total, done, failed, failed_coords)
    return failed


def _state_save(cache_dir, svc_name, bounds, z, total, done, failed, failed_coords):
    state = _progress_load(cache_dir) or {"service": svc_name, "bounds": bounds, "zooms": {}}
    state["service"] = svc_name
    state["bounds"] = bounds
    state.setdefault("zooms", {})
    state["zooms"][str(z)] = {
        "total": total,
        "done": done,
        "failed": [list(c) for c in failed_coords],
    }
    _progress_save(cache_dir, state)


def cmd_download(args):
    try:
        zooms = _parse_zoom(args.zoom)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Using service: {args.service} ({MAP_SERVICES[args.service]['name']})")
    status = MAP_SERVICES[args.service].get("status")
    if status:
        warnings = {"offline": "WARNING: this service appears to be offline (DNS unreachable)",
                    "broken": "WARNING: this service's URL scheme is broken and tiles will likely fail",
                    "api-key": "WARNING: this service requires an API key and will fail without one"}
        print(warnings.get(status, f"WARNING: service status: {status}"), file=sys.stderr)
    for z in zooms:
        _download_all(args.service, z, args.bounds, args.output, args.jobs)


# =========================================================================== #
# SECTION 4: clean_cache  (clean subcommand)
# =========================================================================== #

def cmd_clean(args):
    if not os.path.isdir(args.cache_dir):
        print(f"Error: {args.cache_dir} is not a directory", file=sys.stderr)
        sys.exit(1)
    removed = 0
    checked = 0
    for root, dirs, files in os.walk(args.cache_dir):
        for fname in files:
            if not fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            fpath = os.path.join(root, fname)
            checked += 1
            try:
                with open(fpath, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if not is_valid_tile(data):
                label = "DELETING" if args.delete else "WOULD DELETE"
                print(f"{label}: {fpath} ({len(data)} bytes)")
                if args.delete:
                    try:
                        os.remove(fpath)
                        removed += 1
                    except OSError as e:
                        print(f"  error: {e}")
    print(f"\nChecked {checked} files, {'removed' if args.delete else 'would remove'} {removed}.")
    if not args.delete:
        print("(dry run — re-run with --delete to actually remove)")


# =========================================================================== #
# SECTION 5: view_tiles  (tkinter viewer subcommand)
# =========================================================================== #

def tile_to_lon(tile_x, z):
    return tile_x / (2 ** z) * 360.0 - 180.0

def tile_to_lat(tile_y, z):
    n = math.pi - 2.0 * math.pi * tile_y / (2 ** z)
    return math.degrees(math.atan(math.sinh(n)))

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * R * math.asin(math.sqrt(a))

def format_distance(meters):
    if meters < 1000:
        return f"Distance: {meters:.1f} m"
    return f"Distance: {meters / 1000:.3f} km"


def cmd_view(args):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from PIL import ImageTk
    except ImportError:
        print("tkinter is not available. Install with: sudo apt install python3-tk")
        sys.exit(1)

    CROP_BOTTOM = 22
    TILE_SIZE = 256

    class TileApp(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Map Tile Downloader & Viewer")
            self.geometry("1000x700")
            self.zoom = 0
            self.base_dir = ""
            self.min_tile_x = 0
            self.min_tile_y = 0
            self.composed_image = None
            self.composed_tk = None
            self.measure_mode = False
            self.measure_points = []
            self.measure_marks = []
            self.zoom_groups = {}
            self._build_download_panel()
            self._build_canvas()
            self._build_measure_panel()
            self._build_zoom_panel()
            self._build_status_bar()

        def _build_download_panel(self):
            panel = tk.LabelFrame(self, text="Download", bd=1, relief=tk.GROOVE)
            panel.pack(fill=tk.X, padx=4, pady=2)
            row1 = tk.Frame(panel); row1.pack(fill=tk.X, padx=4, pady=2)
            tk.Label(row1, text="Service:").pack(side=tk.LEFT, padx=(0, 4))
            self.service_var = tk.StringVar(value="osm")
            ttk.Combobox(row1, textvariable=self.service_var, state="readonly",
                         values=list(MAP_SERVICES.keys()), width=12).pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(row1, text="Bounds:").pack(side=tk.LEFT, padx=(0, 4))
            self.bounds_var = tk.StringVar()
            tk.Entry(row1, textvariable=self.bounds_var, width=34).pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(row1, text="Zoom:").pack(side=tk.LEFT, padx=(0, 4))
            self.zoom_var = tk.StringVar(value="15")
            tk.Entry(row1, textvariable=self.zoom_var, width=5).pack(side=tk.LEFT, padx=(0, 10))
            row2 = tk.Frame(panel); row2.pack(fill=tk.X, padx=4, pady=2)
            tk.Label(row2, text="Output dir:").pack(side=tk.LEFT, padx=(0, 4))
            self.dir_var = tk.StringVar()
            tk.Entry(row2, textvariable=self.dir_var, width=60).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(row2, text="Browse...", command=self._browse_dir).pack(side=tk.LEFT, padx=(0, 8))
            tk.Button(row2, text="Download", command=self._run_download).pack(side=tk.LEFT, padx=(0, 8))
            tk.Button(row2, text="Load View", command=self._load_dir).pack(side=tk.LEFT, padx=(0, 8))
            self.export_btn = tk.Button(row2, text="Export GeoTIFF", command=self._export_geotiff)
            self.export_btn.pack(side=tk.LEFT, padx=(0, 8))
            if not _HAVE_TIFF:
                self.export_btn.configure(state=tk.DISABLED,
                                          text="Export GeoTIFF (install tifffile)")

        def _build_canvas(self):
            frame = tk.Frame(self, bd=1, relief=tk.SUNKEN)
            frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
            self.canvas = tk.Canvas(frame, bg="#dddddd", scrollregion=(0, 0, 4000, 4000))
            hbar = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
            vbar = tk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
            self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
            self.canvas.grid(row=0, column=0, sticky="nsew")
            hbar.grid(row=1, column=0, sticky="ew")
            vbar.grid(row=0, column=1, sticky="ns")
            frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
            self.canvas.bind("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind("<Button-1>", self._on_canvas_click)

        def _build_measure_panel(self):
            panel = tk.Frame(self, bd=1, relief=tk.GROOVE)
            panel.pack(fill=tk.X, padx=4, pady=2)
            self.measure_btn = tk.Button(panel, text="Measure: OFF", command=self._toggle_measure)
            self.measure_btn.pack(side=tk.LEFT, padx=4)
            tk.Label(panel, text="(toggle, then click two points on the map)").pack(side=tk.LEFT, padx=4)

        def _build_zoom_panel(self):
            panel = tk.Frame(self, bd=1, relief=tk.GROOVE)
            panel.pack(fill=tk.X, padx=4, pady=2)
            tk.Label(panel, text="Zoom:").pack(side=tk.LEFT, padx=(4, 4))
            self.zoom_scale = tk.Scale(panel, from_=1, to=21, orient=tk.HORIZONTAL,
                                       command=self._on_zoom_change, showvalue=True)
            self.zoom_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            self.zoom_scale.set(1)
            self.zoom_scale.configure(state=tk.DISABLED)

        def _build_status_bar(self):
            self.status = tk.Label(self, text="Ready.", bd=1, relief=tk.SUNKEN, anchor=tk.W)
            self.status.pack(fill=tk.X, side=tk.BOTTOM)

        def _browse_dir(self):
            d = filedialog.askdirectory(title="Select Tiles Directory")
            if d: self.dir_var.set(d)

        def _load_dir(self):
            dir_path = self.dir_var.get().strip() or "./tiles"
            if not os.path.isdir(dir_path):
                messagebox.showerror("Error", f"Directory not found:\n{dir_path}")
                return
            self.base_dir = dir_path
            self._scan_and_compose(dir_path)

        def _scan_and_compose(self, dir_path):
            png_files = []
            for root, _dirs, files in os.walk(dir_path):
                for f in sorted(files):
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        png_files.append(os.path.join(root, f))
            if not png_files:
                messagebox.showinfo("Info", "No image files found.")
                return
            zoom_groups = {}
            for f in png_files:
                parent = os.path.basename(os.path.dirname(f))
                try: z = int(parent)
                except ValueError:
                    parts = os.path.basename(f).rsplit(".", 1)[0].split("_")
                    try: z = int(parts[0])
                    except (ValueError, IndexError): continue
                zoom_groups.setdefault(z, []).append(f)
            if not zoom_groups:
                messagebox.showinfo("Info", "No valid tiles found.")
                return
            self.zoom_groups = zoom_groups
            max_z = max(zoom_groups.keys()); self.zoom = max_z
            min_z = min(zoom_groups.keys())
            self.zoom_scale.configure(state=tk.NORMAL, from_=min_z, to=max_z)
            self.zoom_scale.set(max_z)
            self._compose_canvas(zoom_groups[max_z])
            self.status.config(text=f"Showing {len(zoom_groups[max_z])} tiles from z={max_z}")

        def _on_zoom_change(self, val):
            z = int(float(val))
            if z == self.zoom or not self.zoom_groups.get(z):
                return
            self.zoom = z
            self.zoom_scale.configure(state=tk.DISABLED)
            self._compose_canvas(self.zoom_groups[z])
            self.zoom_scale.configure(state=tk.NORMAL)
            self.status.config(text=f"Showing {len(self.zoom_groups[z])} tiles from z={z}")

        def _export_geotiff(self):
            if not _HAVE_TIFF:
                messagebox.showerror("Missing dependency",
                                     "Install tifffile:\n    pip install tifffile")
                return
            if self.composed_image is None:
                messagebox.showerror("Error", "Load a tile directory first.")
                return
            path = filedialog.asksaveasfilename(
                title="Export GeoTIFF", defaultextension=".tif",
                filetypes=[("GeoTIFF", "*.tif"), ("All files", "*.*")])
            if not path:
                return
            import numpy as np
            z = self.zoom
            # Top-left corner of the composed image, in geo coordinates
            lon0 = tile_to_lon(self.min_tile_x, z)
            lat0 = tile_to_lat(self.min_tile_y, z)
            # Pixel scale: degrees per pixel
            deg_per_px_x = 360.0 / (TILE_SIZE * 2 ** z)
            # Latitude span of the composed image
            rows = self.composed_image.size[1] / (TILE_SIZE - CROP_BOTTOM)
            lat1 = tile_to_lat(self.min_tile_y + rows, z)
            deg_per_px_y = abs(lat0 - lat1) / self.composed_image.size[1] if self.composed_image.size[1] else 0
            arr = np.array(self.composed_image)
            # GeoTIFF tags (ModelPixelScaleTag=33575, ModelTiePointTag=33576)
            extratags = [
                (33575, "d", 3, [deg_per_px_x, deg_per_px_y, 0.0]),
                (33576, "d", 6, [0.0, 0.0, 0.0, lon0, lat0, 0.0]),
            ]
            tifffile.imwrite(path, arr, photometric="rgb", extratags=extratags)
            self.status.config(text=f"Exported GeoTIFF: {path}")

        def _compose_canvas(self, tiles):
            parsed = []
            for f in tiles:
                parts = os.path.basename(f).rsplit(".", 1)[0].split("_")
                if len(parts) < 2: continue
                try: tx, ty = int(parts[-2]), int(parts[-1])
                except ValueError: continue
                parsed.append((tx, ty, f))
            if not parsed: return
            self.min_tile_x = min(p[0] for p in parsed)
            self.min_tile_y = min(p[1] for p in parsed)
            max_tx = max(p[0] for p in parsed)
            max_ty = max(p[1] for p in parsed)
            cols = max_tx - self.min_tile_x + 1
            rows = max_ty - self.min_tile_y + 1
            tile_w = TILE_SIZE
            tile_h = TILE_SIZE - CROP_BOTTOM
            composed = Image.new("RGB", (cols * tile_w, rows * tile_h), (220, 220, 220))
            for tx, ty, f in parsed:
                try: img = Image.open(f).convert("RGB")
                except Exception: continue
                w, h = img.size
                if h > CROP_BOTTOM:
                    img = img.crop((0, 0, w, h - CROP_BOTTOM))
                if img.size != (tile_w, tile_h):
                    img = img.resize((tile_w, tile_h), Image.LANCZOS)
                composed.paste(img, ((tx - self.min_tile_x) * tile_w, (ty - self.min_tile_y) * tile_h))
            self.composed_image = composed
            self.composed_tk = ImageTk.PhotoImage(composed)
            self.canvas.delete("all")
            self.canvas.configure(scrollregion=(0, 0, composed.size[0], composed.size[1]))
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self.composed_tk)
            self.measure_points = []; self.measure_marks = []

        def _run_download(self):
            svc = self.service_var.get()
            bounds = self.bounds_var.get().strip()
            try:
                zooms = _parse_zoom(self.zoom_var.get().strip())
            except ValueError as e:
                messagebox.showerror("Error", str(e)); return
            out = self.dir_var.get().strip() or "./tiles"
            if not bounds:
                messagebox.showerror("Error", "Bounds required."); return
            zlabel = str(zooms[0]) if len(zooms) == 1 else f"{zooms[0]}-{zooms[-1]}"
            self.status.config(text=f"Downloading {svc} z={zlabel} ...")
            def worker():
                try:
                    total_ok = total_fail = total_count = 0
                    for z in zooms:
                        def cb(done, total, failed, z=z):
                            self.after(0, lambda d=done, t=total, f=failed, zz=z:
                                       self.status.config(
                                           text=f"Downloading {svc} z={zz}: {d}/{t} ({f} failed)"))
                        fail = _download_all(svc, z, bounds, out, 10, progress_cb=cb)
                        min_lat, min_lon, max_lat, max_lon = _parse_bounds(bounds)
                        x0, x1, y0, y1 = compute_tile_range(min_lat, min_lon, max_lat, max_lon, z)
                        t = (x1 - x0 + 1) * (y1 - y0 + 1)
                        total_ok += t - fail
                        total_fail += fail
                        total_count += t
                    self.after(0, lambda: self._download_done(svc, zlabel, out, total_ok, total_fail, total_count))
                except Exception as e:
                    self.after(0, lambda: self.status.config(text=f"Error: {e}"))
            threading.Thread(target=worker, daemon=True).start()

        def _download_done(self, svc, z, out, ok, fail, total):
            self.status.config(text=f"Done: {ok} OK, {fail} failed of {total}.")
            self.base_dir = out
            self._scan_and_compose(out)

        def _toggle_measure(self):
            self.measure_mode = not self.measure_mode
            self.measure_points = []; self._clear_measure_marks()
            if self.measure_mode:
                self.measure_btn.config(text="Measure: ON")
                self.status.config(text="Measure mode: click two points.")
            else:
                self.measure_btn.config(text="Measure: OFF")
                self.status.config(text="Measure mode off.")

        def _clear_measure_marks(self):
            for item_id in self.measure_marks:
                try: self.canvas.delete(item_id)
                except tk.TclError: pass
            self.measure_marks = []

        def _on_canvas_click(self, event):
            if not self.measure_mode or self.composed_image is None: return
            cx = self.canvas.canvasx(event.x); cy = self.canvas.canvasy(event.y)
            if len(self.measure_points) >= 2:
                self.measure_points = []; self._clear_measure_marks()
            self.measure_points.append((cx, cy))
            r = 4
            self.measure_marks.append(self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill="red", outline="white"))
            if len(self.measure_points) == 2:
                (x1, y1), (x2, y2) = self.measure_points
                self.measure_marks.append(self.canvas.create_line(x1, y1, x2, y2, fill="red", width=2, dash=(4, 2)))
                z = self.zoom
                tx1 = self.min_tile_x + x1 / TILE_SIZE
                ty1 = self.min_tile_y + y1 / (TILE_SIZE - CROP_BOTTOM)
                tx2 = self.min_tile_x + x2 / TILE_SIZE
                ty2 = self.min_tile_y + y2 / (TILE_SIZE - CROP_BOTTOM)
                dist = haversine(tile_to_lat(ty1, z), tile_to_lon(tx1, z),
                                 tile_to_lat(ty2, z), tile_to_lon(tx2, z))
                self.status.config(text=format_distance(dist))

        def _on_mousewheel(self, event):
            self.canvas.xview_scroll(int(-event.delta / 120), "units")
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    app = TileApp()
    if args.dir:
        app.dir_var.set(args.dir)
        app._load_dir()
    app.mainloop()


# =========================================================================== #
# SECTION 6: test suite
# =========================================================================== #

def _make_png(size=256, color=(255, 0, 0, 255)):
    img = Image.new("RGBA", (size, size), color)
    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

def _make_png_with_mark(size=256):
    img = Image.new("RGBA", (size, size), (200, 200, 200, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 240), (size - 1, size - 1)], fill=(0, 0, 0, 255))
    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()


class TestGeometry(unittest.TestCase):
    def test_lon2x_known_values(self):
        self.assertEqual(lon2x(-180.0, 0), 0)
        self.assertEqual(lon2x(0.0, 0), 0)
        self.assertEqual(lon2x(0.0, 1), 1)
        self.assertEqual(lon2x(90.0, 2), 3)

    def test_lat2y_known_values(self):
        self.assertEqual(lat2y(85.0, 0), 0)
        self.assertEqual(lat2y(80.0, 1), 0)
        self.assertEqual(lat2y(-80.0, 1), 1)

    def test_lon2x_lat2y_bounds(self):
        for z in range(1, 10):
            for lon in (-170, -90, 0, 90, 170):
                self.assertGreaterEqual(lon2x(lon, z), 0)
                self.assertLess(lon2x(lon, z), 2**z)


class TestComputeTileRange(unittest.TestCase):
    def test_world_at_z0(self):
        self.assertEqual(compute_tile_range(-85, -180, 85, 180, 0), (0, 0, 0, 0))

    def test_quadrant_at_z1(self):
        self.assertEqual(compute_tile_range(1, 1, 2, 2, 1), (1, 1, 0, 0))

    def test_full_world_z2(self):
        x0, x1, y0, y1 = compute_tile_range(-85, -180, 85, 180, 2)
        self.assertEqual(x0, 0); self.assertEqual(x1, 3)
        self.assertLessEqual(y0, y1)


class TestTilePath(unittest.TestCase):
    def test_format(self):
        self.assertEqual(tile_path("/cache", 5, 10, 20), os.path.join("/cache", "5", "10_20.png"))

    def test_distinct(self):
        self.assertNotEqual(tile_path("/c", 1, 2, 3), tile_path("/c", 1, 3, 2))


class TestBuildUrl(unittest.TestCase):
    def test_osm_url(self):
        self.assertEqual(build_url("osm", 5, 10, 20), "https://tile.openstreetmap.org/5/10/20.png")

    def test_esri_xy_swap(self):
        self.assertIn("/5/20/10", build_url("esri-imagery", 5, 10, 20))

    def test_subdomain_rotation(self):
        subs = MAP_SERVICES["carto-voyager"]["subdomains"]
        for i, sub in enumerate(subs):
            self.assertIn(f"https://{sub}.", build_url("carto-voyager", 5, 10, 20, i))

    def test_subdomain_wraparound(self):
        subs = MAP_SERVICES["carto-voyager"]["subdomains"]
        self.assertEqual(build_url("carto-voyager", 1, 0, 0, 0),
                         build_url("carto-voyager", 1, 0, 0, len(subs)))

    def test_all_services_build(self):
        for key in MAP_SERVICES:
            url = build_url(key, 10, 100, 200)
            self.assertIsInstance(url, str)
            self.assertTrue(url.startswith("http"))


class TestRegistryIntegrity(unittest.TestCase):
    def test_every_service_has_required_keys(self):
        for key, info in MAP_SERVICES.items():
            self.assertIn("name", info)
            self.assertIn("url", info)
            self.assertIn("attribution", info)
            self.assertTrue(callable(info["url"]))

    def test_every_watermark_key_is_known_service(self):
        for key in WATERMARK_REGIONS:
            self.assertIn(key, MAP_SERVICES)

    def test_all_services_have_watermark_profile(self):
        for key in MAP_SERVICES:
            self.assertIn(key, WATERMARK_REGIONS)


class TestRemoveWatermarks(unittest.TestCase):
    def test_no_regions(self):
        self.assertEqual(remove_watermarks(b"data", []), b"data")

    def test_none_regions(self):
        self.assertEqual(remove_watermarks(b"data", None), b"data")

    def test_corrupt_input(self):
        self.assertEqual(remove_watermarks(b"not image", [(0, 0, 10, 10)]), b"not image")

    def test_real_png_no_regions(self):
        original = _make_png()
        self.assertEqual(remove_watermarks(original, []), original)

    def test_masked_pixel_transparent(self):
        from PIL import Image
        masked = remove_watermarks(_make_png_with_mark(), [(0, 240, 256, 16)])
        img = Image.open(io.BytesIO(masked)).convert("RGBA")
        self.assertEqual(img.getpixel((10, 250))[3], 0)
        self.assertEqual(img.getpixel((10, 10))[3], 255)

    def test_output_is_png(self):
        from PIL import Image
        masked = remove_watermarks(_make_png_with_mark(), [(0, 0, 10, 10)])
        img = Image.open(io.BytesIO(masked)); img.load()
        self.assertEqual(img.format, "PNG")


class TestHTTPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile, shutil
        cls.tmpdir = tempfile.mkdtemp(prefix="tiletest_")
        cls.cache = os.path.join(cls.tmpdir, "cache"); os.makedirs(cls.cache)
        cls.tile_z, cls.tile_x, cls.tile_y = 5, 10, 10
        cls.tile_data = _make_png(color=(0, 128, 0, 255))
        tf = tile_path(cls.cache, cls.tile_z, cls.tile_x, cls.tile_y)
        os.makedirs(os.path.dirname(tf), exist_ok=True)
        with open(tf, "wb") as f: f.write(cls.tile_data)
        TileHandler.cache_dir = cls.cache
        TileHandler.proxy = False
        TileHandler.mask_service = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), TileHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start(); time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.httpd.shutdown(); cls.httpd.server_close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _url(self, path): return f"http://127.0.0.1:{self.port}{path}"

    def test_index_html(self):
        with urllib.request.urlopen(self._url("/")) as resp:
            body = resp.read()
        self.assertIn(b"<html", body.lower())
        self.assertIn(b"leaflet", body.lower())

    def test_index_port(self):
        with urllib.request.urlopen(self._url("/")) as resp:
            self.assertIn(f"localhost:{self.port}", resp.read().decode())

    def test_existing_tile(self):
        with urllib.request.urlopen(self._url(f"/{self.tile_z}/{self.tile_x}/{self.tile_y}.png")) as resp:
            self.assertEqual(resp.headers["Content-Type"], "image/png")
            self.assertEqual(resp.read(), self.tile_data)

    def test_missing_tile_404(self):
        try:
            urllib.request.urlopen(self._url("/0/0/999.png")); self.fail("should 404")
        except urllib.error.HTTPError as e: self.assertEqual(e.code, 404)

    def test_invalid_coords_400(self):
        try:
            urllib.request.urlopen(self._url("/abc/def/ghi.png")); self.fail("should 400")
        except urllib.error.HTTPError as e: self.assertEqual(e.code, 400)

    def test_cors(self):
        with urllib.request.urlopen(self._url(f"/{self.tile_z}/{self.tile_x}/{self.tile_y}.png")) as resp:
            self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")


class TestHTTPMasking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile, shutil
        cls.tmpdir = tempfile.mkdtemp(prefix="tiletest_mask_")
        cls.cache = os.path.join(cls.tmpdir, "cache"); os.makedirs(cls.cache)
        cls.tile_data = _make_png_with_mark()
        z, x, y = 3, 2, 2
        tf = tile_path(cls.cache, z, x, y)
        os.makedirs(os.path.dirname(tf), exist_ok=True)
        with open(tf, "wb") as f: f.write(cls.tile_data)
        TileHandler.cache_dir = cls.cache
        TileHandler.proxy = False
        TileHandler.mask_service = "google"
        cls.tile_z, cls.tile_x, cls.tile_y = z, x, y
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), TileHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start(); time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.httpd.shutdown(); cls.httpd.server_close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _url(self, path): return f"http://127.0.0.1:{self.port}{path}"

    def test_served_tile_masked(self):
        from PIL import Image
        with urllib.request.urlopen(self._url(f"/{self.tile_z}/{self.tile_x}/{self.tile_y}.png")) as resp:
            data = resp.read()
        self.assertNotEqual(data, self.tile_data)
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        self.assertEqual(img.getpixel((100, 243))[3], 0)
        self.assertEqual(img.getpixel((10, 10))[3], 255)


class TestParseBounds(unittest.TestCase):
    def test_valid(self): self.assertEqual(_parse_bounds("10,20,20,30"), [10.0, 20.0, 20.0, 30.0])
    def test_negative(self): self.assertEqual(_parse_bounds("-45,-90,45,90"), [-45.0, -90.0, 45.0, 90.0])
    def test_too_few(self):
        with self.assertRaises(ValueError): _parse_bounds("10,20,30")
    def test_too_many(self):
        with self.assertRaises(ValueError): _parse_bounds("1,2,3,4,5")
    def test_lat_oob(self):
        with self.assertRaises(ValueError): _parse_bounds("91,0,0,0")
    def test_lon_oob(self):
        with self.assertRaises(ValueError): _parse_bounds("0,181,0,0")
    def test_min_gt_max(self):
        with self.assertRaises(ValueError): _parse_bounds("50,0,40,10")
    def test_non_numeric(self):
        with self.assertRaises(ValueError): _parse_bounds("a,b,c,d")


class TestDownloadAll(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="dltest_")
        self.cache = os.path.join(self.tmpdir, "cache"); os.makedirs(self.cache)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_preexisting_skipped(self):
        x0, x1, y0, y1 = compute_tile_range(1.0, 1.0, 2.0, 2.0, 1)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                p = tile_path(self.cache, 1, x, y)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f: f.write(b"\x89PNG fake")
        _download_all("osm", 1, "1.0,1.0,2.0,2.0", self.cache, num_workers=2)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                with open(tile_path(self.cache, 1, x, y), "rb") as f:
                    self.assertEqual(f.read(), b"\x89PNG fake")

    def test_single_tile_range(self):
        x0, x1, y0, y1 = compute_tile_range(1.0, 1.0, 1.001, 1.001, 2)
        self.assertEqual(x0, x1); self.assertEqual(y0, y1)


class TestIntegration(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="integtest_")
        self.cache = os.path.join(self.tmpdir, "cache"); os.makedirs(self.cache)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_server_serves_downloaded_tile(self):
        z = 5
        x0, x1, y0, y1 = compute_tile_range(10, 10, 11, 11, z)
        self.assertEqual(x0, x1); self.assertEqual(y0, y1)
        tile_data = _make_png()
        p = tile_path(self.cache, z, x0, y0)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f: f.write(tile_data)
        TileHandler.cache_dir = self.cache
        TileHandler.proxy = False; TileHandler.mask_service = None
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), TileHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            time.sleep(0.1)
            url = f"http://127.0.0.1:{port}/{z}/{x0}/{y0}.png"
            with urllib.request.urlopen(url) as resp:
                self.assertEqual(resp.headers["Content-Type"], "image/png")
                self.assertEqual(resp.read(), tile_data)
        finally:
            httpd.shutdown(); httpd.server_close()


class TestParseZoom(unittest.TestCase):
    def test_single(self):
        self.assertEqual(_parse_zoom("10"), [10])

    def test_range(self):
        self.assertEqual(_parse_zoom("10-14"), [10, 11, 12, 13, 14])

    def test_range_reversed(self):
        self.assertEqual(_parse_zoom("14-10"), [10, 11, 12, 13, 14])

    def test_int_arg(self):
        self.assertEqual(_parse_zoom(5), [5])

    def test_invalid(self):
        with self.assertRaises(ValueError):
            _parse_zoom("abc")

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            _parse_zoom("25")

    def test_range_out_of_range(self):
        with self.assertRaises(ValueError):
            _parse_zoom("1-25")


class TestProgressFile(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="progresstest_")
        self.cache = os.path.join(self.tmpdir, "cache"); os.makedirs(self.cache)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_path(self):
        self.assertTrue(_progress_path(self.cache).endswith(".progress.json"))

    def test_load_missing(self):
        self.assertIsNone(_progress_load(self.cache))

    def test_save_and_load(self):
        state = {"service": "osm", "bounds": "0,0,1,1",
                 "zooms": {"10": {"total": 4, "done": 2, "failed": [[0, 0]]}}}
        _progress_save(self.cache, state)
        loaded = _progress_load(self.cache)
        self.assertEqual(loaded["service"], "osm")
        self.assertEqual(loaded["zooms"]["10"]["failed"], [[0, 0]])

    def test_clear(self):
        _progress_save(self.cache, {"service": "osm", "bounds": "", "zooms": {}})
        self.assertTrue(os.path.exists(_progress_path(self.cache)))
        _progress_clear(self.cache)
        self.assertFalse(os.path.exists(_progress_path(self.cache)))


class TestParallelProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile, shutil
        cls.tmpdir = tempfile.mkdtemp(prefix="proxtest_")
        cls.cache = os.path.join(cls.tmpdir, "cache"); os.makedirs(cls.cache)
        cls.tile_data = _make_png(color=(0, 200, 0, 255))
        z, x, y = 4, 5, 5
        tf = tile_path(cls.cache, z, x, y)
        os.makedirs(os.path.dirname(tf), exist_ok=True)
        with open(tf, "wb") as f: f.write(cls.tile_data)
        TileHandler.cache_dir = cls.cache
        TileHandler.proxy = False
        TileHandler.mask_service = None
        cls.tile_z, cls.tile_x, cls.tile_y = z, x, y
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), TileHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start(); time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.httpd.shutdown(); cls.httpd.server_close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _url(self, path): return f"http://127.0.0.1:{self.port}{path}"

    def test_consecutive_requests_ok(self):
        url = self._url(f"/{self.tile_z}/{self.tile_x}/{self.tile_y}.png")
        results = []
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(lambda: urllib.request.urlopen(url).status) for _ in range(4)]
            for f in concurrent.futures.as_completed(futs):
                results.append(f.result())
        self.assertEqual(results, [200, 200, 200, 200])


class TestMultiZoomDownload(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="mztest_")
        self.cache = os.path.join(self.tmpdir, "cache"); os.makedirs(self.cache)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_preexisting_skipped_both_zooms(self):
        for z in (1, 2):
            x0, x1, y0, y1 = compute_tile_range(1.0, 1.0, 2.0, 2.0, z)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    p = tile_path(self.cache, z, x, y)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "wb") as f: f.write(b"\x89PNG fake")
        for z in (1, 2):
            _download_all("osm", z, "1.0,1.0,2.0,2.0", self.cache, num_workers=2)
        for z in (1, 2):
            x0, x1, y0, y1 = compute_tile_range(1.0, 1.0, 2.0, 2.0, z)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    with open(tile_path(self.cache, z, x, y), "rb") as f:
                        self.assertEqual(f.read(), b"\x89PNG fake")


@unittest.skipUnless(_HAVE_TIFF, "tifffile not installed")
class TestGeotiffExport(unittest.TestCase):
    def test_export_and_readback(self):
        import tempfile
        import numpy as np
        tmpdir = tempfile.mkdtemp(prefix="geotifftest_")
        try:
            cache = os.path.join(tmpdir, "cache"); os.makedirs(cache)
            # Create a 1x1 tile at z=2 (x=1, y=1)
            z = 2; tx = ty = 1
            p = tile_path(cache, z, tx, ty)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f: f.write(_make_png(size=256, color=(10, 20, 30, 255)))
            # Compose using the same logic as the viewer
            composed = Image.new("RGB", (256, 256 - 22), (220, 220, 220))
            img = Image.open(p).convert("RGB").crop((0, 0, 256, 256 - 22))
            composed.paste(img, (0, 0))
            out = os.path.join(tmpdir, "out.tif")
            lon0 = tile_to_lon(tx, z)
            lat0 = tile_to_lat(ty, z)
            deg_per_px_x = 360.0 / (256 * 2 ** z)
            deg_per_px_y = abs(lat0 - tile_to_lat(ty + 1, z)) / composed.size[1]
            extratags = [
                (33575, "d", 3, [deg_per_px_x, deg_per_px_y, 0.0]),
                (33576, "d", 6, [0.0, 0.0, 0.0, lon0, lat0, 0.0]),
            ]
            tifffile.imwrite(out, np.array(composed), photometric="rgb", extratags=extratags)
            arr = tifffile.imread(out)
            self.assertEqual(arr.shape[:2], composed.size[::-1])
            # Top-left pixel should match
            self.assertEqual(tuple(arr[0, 0][:3]), (10, 20, 30))
        finally:
            import shutil; shutil.rmtree(tmpdir, ignore_errors=True)


def cmd_test(args):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_classes = [
        TestGeometry, TestComputeTileRange, TestTilePath, TestBuildUrl,
        TestRegistryIntegrity, TestRemoveWatermarks, TestHTTPServer,
        TestHTTPMasking, TestParseBounds, TestDownloadAll, TestIntegration,
        TestParseZoom, TestProgressFile, TestParallelProxy,
        TestMultiZoomDownload, TestGeotiffExport,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# =========================================================================== #
# SECTION 7: main / argparse
# =========================================================================== #

def cmd_server(args):
    mask_service = None
    if args.mask and args.mask != "off":
        mask_service = "__auto__" if args.mask == "auto" else args.mask
    os.makedirs(args.cache, exist_ok=True)
    run_server(args.host, args.port, args.cache, args.proxy, args.service,
               mask_service, getattr(args, "remove_text", False))


def cmd_gui(args):
    if _have_gtk():
        app = TileServerGUI()
        app.run()
    else:
        print("No GUI toolkit (PyGObject/GTK) available — starting in CLI mode.")
        print("Install with:  sudo apt install python3-gi gir1.2-gtk-3.0")
        print()
        # Fall back to CLI server
        run_server("127.0.0.1", 8080, "./tiles", False, "osm", None)


def main():
    parser = argparse.ArgumentParser(
        prog="tile_app",
        description="All-in-one map tile downloader, server, viewer, and test suite."
    )
    sub = parser.add_subparsers(dest="command")

    # gui (default)
    sub.add_parser("gui", help="Launch the GTK GUI (default)")

    # server
    p_srv = sub.add_parser("server", help="Start the tile server")
    p_srv.add_argument("-c", "--cache", default="./tiles")
    p_srv.add_argument("-p", "--port", type=int, default=8080)
    p_srv.add_argument("-H", "--host", default="127.0.0.1")
    p_srv.add_argument("--proxy", action="store_true")
    p_srv.add_argument("-s", "--service", default="osm", choices=list(MAP_SERVICES.keys()))
    p_srv.add_argument("--mask", default=None, choices=list(WATERMARK_REGIONS.keys()) + ["auto", "off"])
    p_srv.add_argument("--cli", action="store_true", help="Run server without GUI")
    p_srv.add_argument("--remove-text", action="store_true", help="OCR text removal")
    p_srv.set_defaults(func=cmd_server)

    # download
    p_dl = sub.add_parser("download", help="Download tiles")
    p_dl.add_argument("bounds", help="min_lat,min_lon,max_lat,max_lon")
    p_dl.add_argument("zoom", help="zoom level or range (e.g. 10 or 10-14)")
    p_dl.add_argument("-s", "--service", default="osm", choices=list(MAP_SERVICES.keys()))
    p_dl.add_argument("-o", "--output", default="./tiles")
    p_dl.add_argument("-j", "--jobs", type=int, default=10)
    p_dl.set_defaults(func=cmd_download)

    # clean
    p_cl = sub.add_parser("clean", help="Remove invalid tiles from cache")
    p_cl.add_argument("cache_dir")
    p_cl.add_argument("--delete", action="store_true")
    p_cl.set_defaults(func=cmd_clean)

    # view
    p_vw = sub.add_parser("view", help="Open the tkinter tile viewer")
    p_vw.add_argument("-d", "--dir", help="Tiles directory")
    p_vw.add_argument("-z", "--zoom", type=int)
    p_vw.set_defaults(func=cmd_view)

    # test
    p_ts = sub.add_parser("test", help="Run the test suite")
    p_ts.add_argument("-v", "--verbose", action="store_true")
    p_ts.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if args.command is None:
        # Default: launch GUI
        cmd_gui(args)
        return

    if hasattr(args, "func"):
        args.func(args)
    elif args.command == "gui":
        cmd_gui(args)


if __name__ == "__main__":
    main()
