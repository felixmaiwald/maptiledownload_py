# Tile Downloader 3

All-in-one map tile downloader, server, viewer, and test suite.
Single-file application — no external project imports.

## Quick start

```bash
# Launch the GTK GUI (default)
python3 tile_app.py

# Or use a subcommand
python3 tile_app.py server --cli -p 8080
python3 tile_app.py download 48.0,11.2,48.5,12.0 10 -s osm -o ./tiles
python3 tile_app.py view -d ./tiles
```

## Requirements

| Dependency | Required for | Install |
|------------|-------------|---------|
| Python 3.8+ | always | — |
| Pillow | always | `pip install Pillow` |
| PyGObject + GTK 3 | GUI | `sudo apt install python3-gi gir1.2-gtk-3.0` |
| numpy, opencv-python, pytesseract | OCR text removal (optional) | `pip install numpy opencv-python pytesseract` |
| tesseract-ocr | OCR text removal (optional) | `sudo apt install tesseract-ocr` |
| tifffile | GeoTIFF export in viewer (optional) | `pip install tifffile` |
| tkinter | `view` subcommand (optional) | `sudo apt install python3-tk` |

## Subcommands

### `gui` (default)

Launches the GTK graphical interface.

```bash
python3 tile_app.py
python3 tile_app.py gui
```

Features:
- Browse for cache directory
- Set host / port
- Pick from 31 map services
- Toggle proxy mode (fetch missing tiles on demand)
- Toggle watermark / logo removal
- Toggle OCR text removal
- Start / stop server
- Open map in browser (auto-starts server and opens browser on launch)
- Download panel: pick service, bounds, zoom (single or range), output dir — live progress bar

If GTK is not available, falls back to CLI server mode automatically.

### `server`

Start the HTTP tile server.

```bash
python3 tile_app.py server [--cli] [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-c, --cache` | `./tiles` | Tile cache directory |
| `-p, --port` | `8080` | Port to listen on |
| `-H, --host` | `127.0.0.1` | Bind address (`0.0.0.0` for all) |
| `--proxy` | off | Fetch missing tiles on demand |
| `-s, --service` | `osm` | Map service for proxy & watermark profile |
| `--mask` | off | Strip logos: service name, `auto`, or `off` (see below) |
| `--remove-text` | off | OCR-based text removal (slower) |
| `--cli` | off | Run without GUI |

Valid `--mask` values: any service key from the services table below (e.g. `google`, `bing`, `here-normal`), `auto` (blanks bottom 16px strip), or `off`.

Serves tiles at `http://localhost:<port>/<z>/<x>/<y>.png` (standard slippy-map URL scheme).
Demo Leaflet map at `http://localhost:<port>/` (starts centered on Munich, z=10).

### `download`

Download tiles for a bounding box and zoom level.

```bash
python3 tile_app.py download BOUNDS ZOOM [options]
```

| Argument | Description |
|----------|-------------|
| `BOUNDS` | `min_lat,min_lon,max_lat,max_lon` |
| `ZOOM` | Zoom level (1–21) or range (e.g. `10-14`) |

| Option | Default | Description |
|--------|---------|-------------|
| `-s, --service` | `osm` | Map service |
| `-o, --output` | `./tiles` | Output directory |
| `-j, --jobs` | `10` | Parallel download workers |

A live progress bar is shown on stderr. If a download is interrupted, re-running
the same command resumes automatically: a `<cache>/.progress.json` sidecar tracks
which tiles failed, and already-cached tiles are skipped. The sidecar is removed
once all tiles for every requested zoom level succeed.

A warning is printed if the selected service is marked offline, broken, or
requiring an API key.

Examples:

```bash
# Download Munich at zoom 10 from OpenStreetMap
python3 tile_app.py download 48.0,11.2,48.5,12.0 10 -s osm -o ./tiles

# Download zoom levels 10 through 14
python3 tile_app.py download 48.0,11.2,48.5,12.0 10-14 -s osm -o ./tiles

# Download satellite tiles from Google at zoom 14, 20 workers
python3 tile_app.py download 52.0,8.0,53.0,9.0 14 -s google-satellite -o ./tiles -j 20
```

### `clean`

Scan cache and remove invalid files (HTML error pages, "access blocked", etc.).

```bash
python3 tile_app.py clean CACHE_DIR [--delete]
```

Without `--delete`, runs in dry-run mode (shows what would be deleted).

### `view`

Open the tkinter tile viewer with distance measurement.

```bash
python3 tile_app.py view [-d DIR] [-z ZOOM]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-d, --dir` | `./tiles` | Tiles directory to load on startup |
| `-z, --zoom` | highest available | Zoom level to display initially |

Features:
- Composes all tiles from the selected zoom level into one scrollable canvas
- Per-zoom slider to switch between cached zoom levels
- Download panel for fetching new areas (supports zoom ranges like `10-14`)
- Export GeoTIFF button (requires `tifffile`) — writes a georeferenced TIFF with ModelPixelScale/ModelTiePoint tags (EPSG:4326), openable in QGIS
- Click two points to measure geographic distance (haversine)

Note: the viewer does not apply watermark masking or OCR text removal — it displays tiles as-is from disk. Use the server with `--mask` or `--remove-text` for processed tiles.

### `test`

Run the built-in test suite (54 tests).

```bash
python3 tile_app.py test [-v]
```

Covers geometry, URL building, registry integrity, watermark removal, HTTP server, masking, bounds parsing, download logic, integration, zoom range parsing, progress sidecar, parallel proxy, multi-zoom download, and GeoTIFF export.

## Map services

31 services are available. 30 work out of the box; 1 requires an API key:

| Key | Name | Status |
|-----|------|--------|
| `osm` | OpenStreetMap (Standard) | working |
| `osm-hot` | OSM Hot (Humanitarian) | working |
| `osm-cycle` | OSM Cycle | working |
| `osm-fr` | OSM French (OSMFR) | working |
| `osm-sia` | OSM German (OSM DE) | working |
| `hikebike` | OpenRiverboatMap | working |
| `opentopomap` | OpenTopoMap | working |
| `openseamap` | OpenSeaMap (maritime) | working |
| `stamen-terrain` | Stamen Terrain (Stadia Maps) | working |
| `stamen-toner` | Stamen Toner (Stadia Maps) | working |
| `stamen-watercolor` | Stamen Watercolor (Stadia Maps) | working |
| `thunderforest-cycle` | Thunderforest Cycle | working |
| `thunderforest-transport` | Thunderforest Transport | working |
| `carto-voyager` | CARTO Voyager | working |
| `carto-positron` | CARTO Positron (light) | working |
| `carto-darkmatter` | CARTO Dark Matter | working |
| `esri-imagery` | Esri World Imagery (satellite) | working |
| `esri-topo` | Esri World Topo | working |
| `esri-street` | Esri World Street | working |
| `esri-delorme` | Esri DeLorme (reference overlay) | working |
| `google` | Google Maps (road) | working |
| `google-satellite` | Google Maps (satellite) | working |
| `google-hybrid` | Google Maps (hybrid) | working |
| `google-terrain` | Google Maps (terrain) | working |
| `bing` | Bing Maps (aerial, quadkey) | working |
| `bing-road` | Bing Maps (road, quadkey) | working |
| `here-normal` | HERE Maps (normal day) | needs API key |
| `here-satellite` | HERE Maps (satellite) | needs API key |
| `yandex` | Esri Shaded Relief | working |
| `wikimedia` | Esri Ocean | working |
| `openweather` | OpenWeatherMap (temperature) | API key set |

### Services requiring an API key

- **HERE Maps** (`here-normal`, `here-satellite`): Sign up at [platform.here.com](https://platform.here.com) (free tier, no credit card). Edit the `_here_normal_day_url` / `_here_satellite_url` functions in `tile_app.py` and replace `YOUR_API_KEY` with your key.
- **OpenWeatherMap** (`openweather`): An API key is already configured. To use your own, sign up at [openweathermap.org](https://openweathermap.org/api) (free tier) and replace the `_OPENWEATHER_API_KEY` value in `tile_app.py`. New keys can take 1–2 hours to activate.

### Replaced services

Several original services went offline and were replaced with working alternatives:

| Key | Original service | Replacement |
|-----|-----------------|-------------|
| `osm-sia` | OSM Siaga (DNS dead) | OSM German mirror (`openstreetmap.de`) |
| `hikebike` | Hike & Bike (discontinued) | OpenRiverboatMap (`openstreetmap.fr/openriverboatmap`) |
| `yandex` | Yandex Maps (no public tile URL) | Esri Shaded Relief |
| `wikimedia` | Wikimedia Intl (DNS dead) | Esri Ocean |
| `stamen-*` | Stamen (DNS dead) | Stadia Maps (`stadiamaps.com`, works with `Referer: localhost` header) |
| `osm-cycle` | OSM Cycle (broken URL) | Thunderforest Cycle (`thunderforest.com/cycle`) |
| `bing` / `bing-road` | Bing Maps (broken URL scheme) | Fixed — now uses quadkey conversion |

### Bing quadkey notes

Bing Maps uses a quadkey tile addressing system instead of standard `z/x/y` coordinates. This app converts `z/x/y` to quadkeys automatically via the `_tile_xy_to_quadkey` function. The conversion works correctly at all zoom levels, but note that Bing's tile server (`ecn.tN.tiles.virtualearth.net`) does not require an API key for basic tile access — this may change in the future as Bing Maps for Enterprise is being retired (free access until June 30, 2028).

## Demo map page

The server serves a Leaflet-based demo page at `http://localhost:<port>/` with:

- **Layer switcher** (bottom-right): switch between all 31 services plus the local cache layer (32 layers total)
- **Town name search** (top-right): type a place name, press Enter — jumps to the location via Nominatim forward geocode
- **Click-to-copy**: click anywhere on the map to see lat/lon with a Copy button
- **Reverse geocode**: the click popup also shows the nearest town/city name via Nominatim reverse geocode

## Cache layout

Tiles are stored on disk as:

```
<cache_dir>/<z>/<x>_<y>.png
```

Example: `./tiles/10/536_341.png` for z=10, x=536, y=341.

A `<cache_dir>/.progress.json` file tracks download progress for resume support.

## Watermark removal

Logos and attribution overlays are blanked out for known services (Google, Bing, HERE, Esri). For other services, `--mask auto` blanks the bottom 16px strip.

OCR-based text removal (`--remove-text` or GUI checkbox) uses Tesseract to detect text on each tile and OpenCV inpainting to paint over it. This is slower and works best on roadmap-style tiles.

## Troubleshooting

**OpenWeatherMap returns 401**
New API keys can take 1–2 hours to activate after signup. Verify your key is active:
```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('https://api.openweathermap.org/data/2.5/weather?lat=48.13&lon=11.59&appid=YOUR_KEY').status)"
```

**Stadia Maps (stamen-*) returns 401 when fetched manually**
Stadia Maps requires a `Referer: http://localhost:PORT/` header. This is handled automatically by `fetch_tile` and `download_tile`. If fetching URLs manually (e.g. with curl), add `--referer http://localhost:8080/`.

**HERE Maps returns 401 or 410**
The old `cit.api.here.com` endpoints are dead. The URLs now point to `maps.hereapi.com` which requires a valid API key. Replace `YOUR_API_KEY` in the `_here_normal_day_url` / `_here_satellite_url` functions.

**GTK not available / GUI won't start**
Install PyGObject:
```bash
sudo apt install python3-gi gir1.2-gtk-3.0
```
If GTK is still not available, the app falls back to CLI server mode automatically.

**GeoTIFF export button is disabled**
Install tifffile:
```bash
pip install tifffile
```

**Port already in use**
Use a different port with `-p`:
```bash
python3 tile_app.py server --cli -p 8081
```

## Notes

- The demo map page starts centered on **Munich** (lat 48.1374, lon 11.5980) at zoom 10.
- All HTTP responses include CORS headers (`Access-Control-Allow-Origin: *`).
- Tile validation checks PNG/JPEG/GIF/WebP magic bytes — HTML error pages are never cached.
- Proxy mode fetches tiles in parallel (no global lock).
- When using `--proxy` against `tile.openstreetmap.org`, respect the [OSM Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/).

## File structure

```
tile-downloader3/
├── tile_app.py          # all-in-one application (run this)
├── tiles/               # tile cache (created on first download)
└── README.md            # this file
```

## License

This project is provided as-is for educational and personal use. Map tiles are served from third-party providers — respect their respective usage policies and attribution requirements.
