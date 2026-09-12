 Sentinel Nexus — working context

Running record of what this platform is, what has been verified against the real
grid, and what changed in each working session. Updated every turn.

Last updated: **2026-09-12**

---

## 1. What the system is

A unified CCTV interoperability and intelligence layer over the Sentinel
government CCTV grid: **30 live Ahmedabad ITMS traffic cameras** plus one
demonstration checkpoint feed (`OWN-01`), 31 in the registry.

One Python process serves everything — REST API, live alert WebSocket, MJPEG
previews and the static command-centre UI. No Node toolchain, no bundler, no
build step. That is a deployment constraint, not a preference: the platform is
specified to run on a low-end machine on an isolated operator network.

```
backend/app/
  api/routes.py         36 REST endpoints + 1 WebSocket
  config.py             all settings, env-driven, profile presets
  connectors/           RTSP / HLS / catalogue federation
  pipeline/             worker (PTS sampling) -> detect -> track -> plate -> ocr -> overlay
  services/             ingest, live, search, alerts, watchlist, health, gis, registry,
                        importer, reports, security
backend/static/
  index.html            command centre (map-first)
  wall.html             video wall  <- added 2026-09-09
  js/                   app, api, store, ui, map, basemap (dark + light map
                        palettes; light unused, see §11), wall (dock),
                        wall-page, theme (wall only), tour, panels/*
  css/                  tokens (dark + light palettes), app, wall
```

Run: `python run.py`, or
`cd backend && ../.venv-clean/Scripts/python.exe -m uvicorn app.main:app --port 8000`.
Demo accounts `admin` / `operator` / `analyst`, password `sentinel-<role>`.

---

## 2. Verification against the live grid — 2026-09-09

All numbers below are measured, not asserted. Sweep script opened every camera
in the registry, decoded up to 6 frames, ran the detector on each and attempted
ANPR on every vehicle box wide enough to try.

### 2.1 Cameras — working

`103.250.160.189:8554` answers. RTSP over TCP with Basic auth from
`SENTINEL_RTSP_USER` / `SENTINEL_RTSP_PASSWORD`.

| | count |
|---|---|
| Cameras probed | 30 (the grid; `OWN-01` is a local file feed) |
| Delivered frames | **26** |
| Delivered nothing within 50 s | 4 — CAM-08, CAM-21, CAM-23, CAM-30 |
| Marginal | CAM-16 — 1 frame at 50.6 s |

Open latency ranged **1.48 s (CAM-17) to 905 s (CAM-11)**. That upper figure is
new: the README's documented range was 1.8–275 s, and CAM-11 took just over
fifteen minutes to hand over its first frame. It is the reason the UI never
treats a blank tile as a dead camera.

A second, narrower check ran the real endpoint rather than a script —
`POST /api/health/probe?force=true` over six cameras, each one opening an actual
capture behind the same stream semaphore the ingest workers use:

| camera | measured | open latency | frames | frame std |
|---|---|---|---|---|
| CAM-01 | ONLINE | 4.4 s | 3 | 68.5 |
| CAM-04 | ONLINE | 11.0 s | 3 | 56.3 |
| CAM-05 | ONLINE | 3.8 s | 3 | 49.2 |
| CAM-12 | **DEGRADED** | 15.5 s | 3 | 5.0 |
| CAM-13 | ONLINE | 9.8 s | 3 | 49.7 |
| CAM-02 | ONLINE | 3.6 s | 3 | 54.9 |

CAM-12 is the case the health service exists for: it opens, it delivers frames,
and the frames carry no structure (std 5.0 against a floor of 8.0). It reports
itself up while being unusable, and the probe says so instead of believing it.

Registry status is *reported*, not measured, and the two disagree: CAM-10 is
registered DEGRADED and delivered 29 detections across 6 frames, while several
registered-ONLINE cameras delivered nothing. The Health panel exists for exactly
this and should be trusted over the registry column.

### 2.2 ANPR — the chain runs; the grid yields nothing

* Detector: **YOLO11s on ONNX Runtime**, confidence floor 0.25. Confirmed
  loaded and running — the `/api/stats` model chip reads `yolo11s · ONNX`.
* Vehicles were found on **12 of 26** cameras that delivered frames. Best:
  CAM-13 and CAM-15 at ~4-5 detections/frame, largest box 1122 px (CAM-26).
* Plate OCR was **attempted 71 times** across 8 cameras (every vehicle box
  ≥ 90 px wide, up to 2 per frame).
* **Plates read from the live grid: 0.**

This matches the database exactly, and it is not a bug in the chain. Isolated
from the grid entirely, on a synthetic plate rendered to an image, the chain
returns `PlateRead(text='GJ01AB1234', raw='GJ01AB1234', confidence=0.987,
valid=True)` — localisation, restoration, PaddleOCR and the Indian layout
normaliser all correct. The chain is proven to work — `OWN-01`, the daylight checkpoint feed, produced 4 plates at
0.88–0.99 confidence (`MPE3389`, `SEZ229`, `INS6012`). The live grid is
wide-angle night overview PTZ cameras where plates run 20–40 px with motion blur
and headlight bloom, and nothing in that image contains a readable registration.

The honest statement is: **ANPR works, and this grid is not an ANPR grid.**
Attribute-based sightings (type, direction, time, place) are what carry
cross-camera correlation here, which is why they are recorded separately from
plates.

### 2.3 Sightings in the database — working and searchable

`backend/data/sentinel.db` (SQLite + WAL).

| table | rows |
|---|---|
| `sightings` | **9,681** |
| `cameras` | 31 |
| `watchlist` | 6 |
| `alerts` | 1 |
| `users` | 3 |
| `audit_log` | 271 |

Sightings by camera: CAM-05 2,964 · CAM-01 2,841 · CAM-13 2,266 · CAM-04 1,602 ·
OWN-01 8.
By type: car 2,409 · bus 1,877 · bicycle 1,865 · motorcycle 1,819 · truck 1,711.
With a plate: **4** — all from OWN-01, per §2.2.
Event timestamps: 9,669 of 9,681 carry a burned-in overlay clock, spanning
2026-06-14 to 2026-09-02.

A **sustained ingest run** was also driven through the API rather than a script,
to prove the write path end to end rather than only the read path:
`POST /api/ingest/start` on CAM-01, CAM-04 and CAM-05, left running, then
`POST /api/ingest/stop`.

| camera | frames | sightings | plates | scenery suppressed |
|---|---|---|---|---|
| CAM-01 | 1,153 | 362 | 0 | 19 |
| CAM-04 | 297 | 36 | 0 | 0 |
| CAM-05 | 707 | 219 | 0 | 0 |
| **total** | **2,157** | **617** | **0** | **19** |

`sightings` went 9,681 → 10,299 over the run, which is the 617 written plus one
already in flight. Every row carries camera, overlay-clock event time, PTS,
vehicle type, direction, detection confidence, bbox, denormalised lat/lon and an
evidence JPEG on disk. Nineteen tracks were correctly dropped as static scenery
rather than recorded as vehicles that never passed.

Search paths all live and exercised through `/api/search`:
by plate (exact + single-character OCR-confusion fallback), by attributes
(type / camera / direction), and free text. Route reconstruction refuses to join
sightings across recording clusters, so a "journey" is never invented from
cameras whose footage does not overlap in time.

---

## 3. Changes — session of 2026-09-09

### 3.1 The map got a real basemap

**Before:** no basemap at all. A near-black canvas with a few thousand unstyled
OpenStreetMap line segments fetched from Overpass. The original reasoning was
sound — every free *raster* dark basemap either watermarks anonymous requests
(CARTO) or refuses application traffic (OSM volunteer servers answer 418) — but
the result was very hard to read.

**After:** vector tiles. **OpenFreeMap** serves the whole OpenMapTiles planet
with no API key, no account, no watermark and no rate limit, so the geography is
real *and* stays off anyone's billing account. Because the tiles are vector, the
styling is ours.

New file `static/js/basemap.js` builds a Sentinel dark style: water, waterway
(the Sabarmati is the landmark every camera on this grid sits near), landcover,
parks, landuse, a six-step neutral road hierarchy drawn with casings, buildings
past z14, dashed admin boundaries, and place labels.

The palette follows one rule: **the basemap may use greys, one desaturated blue
for water and one desaturated green for parkland, and never a signal hue.**
Accent blue, signal green, warn amber, critical red and trace purple each mean
exactly one operational thing in this interface. A road drawn in trace purple
would read as a vehicle's journey.

Precedence, highest first:
1. `SENTINEL_BASEMAP_URL` — an operator's own raster tile server
2. the keyless vector basemap (default)
3. neither — falls back to the cached Overpass geography, the old behaviour,
   for isolated networks. This also happens **automatically** if the tile server
   turns out to be unreachable at runtime (`map.on('error')` → `whenBasemapLost`).

Also fixed: two stacked attribution controls in the corner, one of them
crediting OpenStreetMap twice.

### 3.2 Cameras that are not on the map are no longer invisible

**22 of 31 cameras have no position anyone has vouched for.** The map correctly
refuses to draw them — a pin an operator might dispatch against must not be a
guess — but the consequence was that the map read as a nine-camera deployment.
That is the exact failure this platform exists to prevent.

New tray, bottom-left over the map (`panels/unplaced.js`), permanently visible
while any camera remains unplaced. Each card leads with the camera's own last
decoded frame, because "Dethali Char Rasta" and "CAM-23" are both unplaceable
from a name but a *picture* of a junction is not. Click a card, then click the
map: written through `PUT /api/cameras/{id}/location` with accuracy VERIFIED and
the operator's name, into `camera_metadata_history`. Nothing is ever placed
automatically and nothing is estimated.

### 3.3 The video wall became its own page

`/wall` → `static/wall.html` + `js/wall-page.js` + `css/wall.css`.

The old wall was a 210 px dock at the bottom of the map with a cap of 8. The
command centre is for investigating one thing and the wall is for watching the
estate; those want opposite layouts, and an operator typically wants the wall on
a second display. Both now exist — the dock stayed for a couple of feeds beside
the map, and the rail has separate controls for each.

What the page does, and why:

* **All 31 cameras present, none streaming.** Every camera is a card from the
  moment the page opens. A camera decodes only when clicked. Each viewer gets
  its own stream copy off this grid, so auto-playing 31 feeds would not produce
  a wall — it would produce 31 cameras that all fail to open.
* **Multiple cameras at once**, up to the server's real cap. That cap is a
  property of the grid, not a UI preference: at ten parallel opens five cameras
  returned no frames at all where the same five recovered when opened serially.
  New endpoint `GET /api/config/live` publishes `max_concurrent_streams` so the
  wall can show `2 / 4 streaming` and say why a further tile is refused rather
  than letting an operator guess.
* **Only live tiles claim to be live.** An idle card shows the camera's last
  decoded frame, heavily dimmed, with a `LAST FRAME` badge in the same corner a
  live tile puts its `LIVE` badge, and no green dot. A camera that has never
  delivered a decodable frame gets the plain hatched idle state.
* **Stopping a tile really stops the stream.** An MJPEG response stays open as
  long as the `<img>` holds it, so deactivating clears `src` before dropping the
  node — otherwise a slot stays occupied against the cap for minutes.
* Filters (all / live / online / ANPR-capable), free-text filter, column density
  1–5 or auto, a detections on/off toggle, auto-fill by plate score, stop-all,
  double-click to full-screen a tile (reusing the same `<img>`, so focus never
  costs a second slot), and the working set is remembered in `localStorage`.
* `?camera=CAM-04` deep-links from a wall card back to that camera on the map.

### 3.4 Whole-interface polish

* Camera pins re-cut for a lit basemap: dark halo, status glow, larger hit area,
  hollow dashed rings still meaning "approximate position".
* A vignette over the map edges so the translucent panels keep their contrast
  where the basemap is bright. Non-interactive, and MapLibre's own controls are
  lifted above it.
* Scale bar restyled — it was invisible against real geography.
* The heavy cached GIS layers (city grid is 3 MB, districts 5 MB) no longer load
  automatically when a basemap is present, since they duplicate what is already
  drawn. They switch themselves on if the basemap is lost.

---

## 4. Bugs found and fixed — session of 2026-09-09

**Wall tiles collapsed to a 49 px letterbox strip.** `.wcard` sets
`overflow: hidden` to clip video to its rounded corners, which makes it a scroll
container — and Chrome then refuses to let the height its stage derives from
`aspect-ratio: 16 / 9` contribute to the grid row's intrinsic size. Rows came out
91.6 px while the stage inside them measured a correct 226 px, so every tile
silently clipped its own video. Fixed with `grid-auto-rows: max-content` on
`#wall-grid`. Verified in-browser: card 92 px → 269 px.

Two earlier attempts at this did *not* work and are worth not repeating:
switching the card from flex-column to block, and removing `width: 100%` from
the stage. Both are still in place because they are correct on their own terms,
but neither was the cause.

**Two attribution controls** on the map, from `attributionControl: true` in the
map options *and* an explicitly added one. Suppressed the built-in.

**The layer control ran underneath the new unplaced tray.** Both are absolutely
positioned in the same left-hand column, one top-anchored and one bottom-
anchored, and the layer list was still sized as though it had the column to
itself — its geography toggles were unreachable. Capped with
`#stage:has(#unplaced:not([hidden])) #layers`.

**Two competing height caps on the layer control.** The rule capping `#layers`
against the unplaced tray had been written twice, once deriving the height from
the tray's own size and once hard-coding `52%`. The later, weaker one won.
Removed; the derived rule stands alone.

---

## 5. Verified in a real browser — 2026-09-09

Driven with Playwright against a live server on `:8011`, signing in through the
actual form rather than an injected token.

* Command centre: map renders over the vector basemap, 9 pins drawn, 9 rail
  controls, wall link present, **no JavaScript errors**.
* Wall page: 31 cards; two cameras activated simultaneously and both painted
  real video with detection boxes; slot chip read `2 / 4 streaming`; stopping one
  released its slot and the chip fell to `1 / 4`.
* `pytest` over `backend/`: **26 passed**, after every change above.
* The only network failures are `404` on `/api/cameras/{id}/thumbnail` for the
  four cameras that have never produced a decodable frame. That is the endpoint
  answering correctly and the idle card falling back as designed.

---

## 6. Standing constraints — do not regress these

* **Timing comes from PTS and the burned-in overlay clock only.** `CAP_PROP_FPS`
  lies on this fleet (CAM-06 reports 90,000 fps, CAM-30 reports 200) and arrival
  time lies too, because a buffered GOP replays faster than real time on connect.
* **Feeds loop.** At the loop point the scene cuts and PTS jumps backwards. Open
  tracks are closed rather than carried across the cut.
* **Cameras are not one synchronised network.** Their recordings span four dates
  and five time clusters. A route may only be built from cameras whose footage
  overlaps in time. The primary cluster is CAM-01, 02, 03, 04, 05, 09, 12, 13, 14.
* **Vehicle colour is never derived.** At night the sodium and LED lighting plus
  headlight bloom drive apparent hue more than the paint does, so a named colour
  would describe the illuminant.
* **Nothing is fabricated.** No invented plates, no guessed pin positions, no
  plausible-looking POI counts. Where a thing cannot be known, the interface says
  so — `no plate read`, `NO POS`, `Feed did not open`.
* **RTSP over TCP, never UDP.** UDP fails across NAT and yields corrupt frames
  that look exactly like model bugs.
* Credentials live in `.env` only and are injected at connection time. They are
  never written into `cameras.json` or the database.

---

## 7. Open items

* 22 cameras still unplaced. The tray makes this fixable in two audited clicks
  per camera, but somebody has to do it.
* CAM-08, CAM-21, CAM-23, CAM-30 delivered no frames in this sweep and CAM-16 is
  marginal. Worth a Health-panel probe to see whether that is persistent.
* Registry status disagrees with measured availability on several cameras;
  consider having the health probe write back more aggressively.
* `playwright` was installed into `.venv-clean` for browser verification. It is a
  dev-only dependency and is not in `requirements.txt`.
* The no-flash snippet is now duplicated in **both** `index.html` and
  `wall.html`, and duplicates `resolve()`/`applyTheme()` from `theme.js`. Three
  copies. They are marked `SYNC_NOTE` at every site; a fourth page means a
  fourth copy, because a module script cannot run before first paint.

---

## 8. Changes — session of 2026-09-12

### 8.1 The wall was reported broken; it was not, and here is what was

Reported as "the wall isn't working". Driven end to end with Playwright against
a live server on `:8011`, signing in through the real form, the page is sound:

| check | result |
|---|---|
| `/wall` and every asset it pulls | 200 |
| Cards rendered | **31 of 31** |
| Grid geometry at 1600 px | 4 columns, card 383 x 257, stage 381 x 214 |
| `GET /api/config/live` | `max_concurrent_streams: 4` |
| CAM-01 activated by click | painted **960 x 540** live video with detection boxes |
| Slot chip | `0 / 4` -> `1 / 4 streaming` |
| JavaScript errors | **none** |
| CAM-08 (delivered nothing in the 09-09 sweep) | opened and painted this time |

The only 4xx are `404` on `/api/cameras/{id}/thumbnail` for cameras that have
never produced a decodable frame. That is the endpoint answering correctly and
the idle card falling back as designed, exactly as in §5.

**What was actually broken was the way onto the page.** `/wall` with no token,
or with a token the server rejects, called `toSignIn()` — which was a bare
`location.href = '/'`. An operator clicked "Video wall", landed silently back on
the map, and had nothing to distinguish a signed-out session from a dead wall.
So it read as a dead wall. Reproduced both ways: no token and a garbage token
both bounced to `/` with zero explanation.

Fixed at both ends:

* The wall now leaves with `/?next=/wall&why=signin|expired` and
  `location.replace`, so the dead page does not sit in the back history.
* The gate reads `why` and says which of the two happened, in a new
  `.gate-note` above the form.
* On success the gate hands the operator **back to the wall**, rather than
  dropping them on the map to go find the link again.
* `next` is **allowlisted** (`/`, `/wall`), not validated. A check for "starts
  with `/`" still admits `//evil.example.com`, which browsers follow off-site —
  an open redirect on the one page where it is worth most. Verified: `?next=//example.com`
  shows no note and lands on `/`.

### 8.2 Light theme, and why the tiles stay dark

Asked for as an option, so it is an option: **dark remains the default and
"auto" is opt-in.** The obvious implementation — a bare `prefers-color-scheme`
query — would hand every operator whatever their OS says, and an OS in light
mode is not evidence about the room the wall is in. A workstation on a vendor
default tells you nothing. Somebody who never touches the control gets dark.

`css/tokens.css` gained a full `[data-theme="light"]` palette. The surfaces
invert; the signal hues keep their meanings but move *value*, because `#2fd6a0`
is a good "live" against near-black and a poor one against white. Each is
re-mixed to clear 4.5:1 on `--surface`: accent `#1667d2`, signal `#0a8a62`,
warn `#9a6205`, high `#bc4a19`, critical `#c81f3f`, trace `#6340c8`. Three
`--on-*` tokens carry text drawn *on* a filled hue, since near-black-on-bright
and white-on-dark have to flip together.

**The video tiles keep their dark ground under both themes**, via a fixed
`--video-ground` / `--video-scrim*` / `--on-video-*` set that the light block
deliberately does not redefine. Three reasons, all about the picture:

* Tiles letterbox rather than stretch (§3.3). White bars around a night feed
  are a light source pointed at the operator.
* This grid is overwhelmingly night PTZ footage. Against a light ground the eye
  adapts to the ground and the feed goes to mud.
* An idle tile shows its last frame at 26% brightness so it can never be taken
  for live. That only reads as *dimmed* over a dark ground; over a light one it
  reads as a fault.

So light mode turns the chrome light and leaves the pictures alone. That is the
intended result, not an oversight.

`js/theme.js` resolves `auto` to a concrete `light`/`dark` **in script**, so
tokens.css holds one light palette rather than two copies (one for the
attribute, one inside a media query) that would drift. The stored value is the
*preference*, not the result — storing the result would freeze an "auto" user
into whichever mode they were in when they chose it.

A `<head>` snippet in `wall.html` duplicates that resolution inline so the theme
lands **before first paint**; a module script cannot, and a light-mode operator
should not eat a black flash on every navigation. The duplication is marked at
both ends (`SYNC_NOTE`).

Control: a `Theme` segmented group in the wall bar beside `Columns`, because it
is the same kind of thing — how this operator wants the wall drawn, not what it
is showing.

Also tokenised, so they travel with the theme instead of staying frozen at the
dark palette: the six `.tag.*` border rgba literals (now `--*-line`), the dock
`.tile` ground, `.btn.primary`, `.btn.danger`, the rail badge and the waypoint.

**Scope at the time: the wall page only.** The command centre followed in §9.

### 8.3 Verified

* Theme: `dark` default; click Light -> `data-theme="light"`, `sentinel.theme`
  = `light`, persists across reload and is **already applied at
  `domcontentloaded`** (no flash). `auto` follows the emulated OS both ways.
* **A live tile survives the switch** — CAM-01 stayed streaming at `1 / 4`
  through the theme change; the stage stayed `rgb(4, 6, 10)` while the shell
  went `rgb(232, 236, 243)`.
* `pytest` over `backend/`: **26 passed**.

---

## 9. Changes — session of 2026-09-12, continued

### 9.1 The command centre got the light theme, map and all

§8.2 stopped at the wall because a light theme there means a light *basemap*,
and light chrome around a dark map is worse than dark chrome around one. That is
now done.

**`basemap.js` carries two palettes.** Everything else about the style — which
layers exist, what they filter on, how wide a road is at which zoom — is shared,
and the colours are read from a single `P` object that `setPalette()` points at
one of two tables. Adding light meant adding a table, not a second style.

The light road ramp is **rebuilt rather than inverted**, which is the part worth
recording. Inverting the dark ladder puts motorways at near-black and service
roads at pale grey — backwards, because under light the heaviest road must be
the *darkest*. The ramp also compresses: on white, six greys separate far more
clearly than on black, so the steps sit closer without the network turning into
a mat. The casing inverts outright — darker than its road under dark, *lighter*
under light, or every road gains a hard outline and the map reads as a wiring
diagram. Ground is `#eef1f5`, not white, for the same reason the dark ground is
not `#000`: a translucent panel needs something to be translucent against.

The palette rule is unchanged in both: neutrals, one desaturated blue for water,
one desaturated green for parkland, **never a signal hue**.

`mapView.restyle()` rebuilds the style on a theme change. Markers (camera pins,
POIs, trace waypoints) are attached to the map rather than the style and survive
untouched — which is the only reason the swap is tolerable. The trace source,
its two layers, and any GIS line layers are style objects and are destroyed, so
they are re-added and the GIS geometry replayed from a new `gisLineData` cache
rather than re-fetched (those files run to megabytes).

Map-surface CSS is tokenised to follow the basemap: the vignette now *lightens*
the map edges under light instead of darkening them (same job, opposite sign),
and the pin ring inverts so a pin still separates from pale parkland. The pin
*shadow* stays black at lower alpha — a shadow is an absence of light under both
themes, and inverting it turns every pin into a glowing dot.

Control: a cycle button at the foot of the rail rather than the wall's
three-way segmented control, because the rail is 56 px wide. Same three states,
same stored preference, so setting the theme on either page sets it on both —
verified in both directions.

### 9.2 Bug: the restyle silently dropped every custom layer

Worth recording in full, because the obvious fix and the obvious *second* fix
are both wrong on MapLibre 4.7.1, and the failure is invisible.

The basemap recoloured correctly, so everything looked right — while the trace
and every GIS layer were quietly gone until the next full reload.

* `map.once('style.load', ...)` **never fires for a `setStyle`.** That event is
  emitted only for the map's initial style. Measured: a `setStyle` emits `data`,
  `styledata` and `idle`, and no `style.load`.
* `map.once('styledata', ...)` fires **exactly once, and `isStyleLoaded()` is
  still `false` when it does.** So the usual "re-arm until the style is ready"
  loop waits for a second `styledata` that never comes, and hangs. This was the
  second wrong fix.

What works: listen on **both** `styledata` and `idle`, with a handler that is
idempotent, checks `isStyleLoaded()` itself, and detaches both listeners once it
has run. Guessing which single event to trust is what broke this twice.

Verified across a full dark → light → auto → dark cycle with a GIS layer on:
`trace` source, `trace-line` layer and `gis-highways` all present at every step,
9 pins throughout, no console errors.

### 9.3 The rail icons were three copies of one glyph

`cameras`, `events` and the preview-dock button all rendered the **same**
video-camera path, and `watch` was the same shield as the brand mark in the
corner. So a six-item rail carried four symbols, two of them repeated.

The rule applied: a glyph should depict the *subject* of its panel rather than a
generic idea of surveillance. Almost everything here is about cameras, so a
camera outline distinguishes nothing.

| rail item | was | now |
|---|---|---|
| Cameras | video camera | unchanged — the one place it is the subject |
| Camera registry | three bare lines | a table: header rule + column divider |
| ANPR events | **video camera (dupe)** | a registration plate |
| Vehicle tracing | bare diagonal arrow | two waypoints and the path between them |
| Watchlist & alerts | **shield (= brand mark)** | a bell — the unack badge hangs off it |
| Camera health | pulse | unchanged |
| Audit trail | clock | unchanged |
| Preview dock | **video camera (dupe)** | picture-in-picture, new `dock` glyph |
| Video wall | 2x2 grid | unchanged |
| Theme | — | new: half-filled circle |

Shapes are held to one or two closed forms plus at most three short strokes;
anything finer turns to mud at 18 px with a 1.8 stroke, which is what the rail
actually renders at. Verified: **10 distinct paths across 10 rail buttons.**

### 9.4 Hover labels on the rail

A 56 px column of glyphs is only self-explanatory to whoever chose the glyphs.
The buttons always carried a `title`, but the native tooltip waits about a
second, renders in the OS style rather than this one, and never appears on a
touch display — so in practice an operator learned the rail by clicking
everything once.

A CSS flyout on `data-label` now names each button immediately, in the
interface's own type, on hover **and on `:focus-visible`** so it is reachable
from the keyboard. `title` is dropped from the rail buttons — leaving it would
show the flyout and then the native tooltip a second later, one on top of the
other — and `aria-label` carries the accessible name. `#rail` had to stop
clipping for the flyout to be visible at all.

The theme button's label names its current state *and* what clicking will do
("Theme: Dark — click for Light"), which is what makes a cycle button usable;
without it the operator has to click to find out.

### 9.5 Verified

* Full theme cycle on the command centre: map recolours, **9 pins throughout**,
  trace and GIS layers survive every step, no console errors.
* Theme set on the command centre carries to the wall and back, both directions.
* A wall tile stayed live at `1 / 4 streaming` through a theme change, stage
  ground `rgb(4, 6, 10)` under both.
* 10 distinct rail glyphs; hover label opacity 1 with the right text.
* `pytest` over `backend/`: **26 passed**.

---

## 10. Two bugs from §9, reported and fixed

Both reported as "map is still dark when command centre in light mode and there
are 2 symbols on sidebar with same symbol". Both were real, and §9 had claimed
the second one fixed on a check that could not have caught it.

### 10.1 Static assets were served with no cache policy at all

**This is the one to remember.** Starlette's `StaticFiles` sends `ETag` and
`Last-Modified` but **no `Cache-Control`**. A browser given a validator and no
freshness directive falls back to *heuristic* caching — roughly a tenth of the
file's age — and inside that window it does not revalidate at all. It serves
what it has and asks nobody.

On a normal site that is a bandwidth optimisation. Here it is a correctness bug,
because this UI is a graph of ES modules that have to agree with each other. A
browser holding a stale `basemap.js` beside a fresh `theme.js` gives you an
interface that is half-updated and reports nothing: **a light command centre
with a dark map, and not one error in the console to say why.** Which is exactly
what was reported.

Fixed in `main.py`: `RevalidatingStatics` sets `Cache-Control: no-cache` on every
static response, and `_page()` does the same for `/` and `/wall` — a cached
`index.html` pinning an old module graph is the same bug one level up.

`no-cache` does not mean "do not store"; it means "store it, but ask before
reusing it", so a conditional request still answers **304 with an empty body**.
Verified: header present on assets and pages, conditional GET returns 304. On a
one-process deployment on an isolated network that round trip is free, and it is
the right side of the trade — a stale module costs an afternoon.

Note for anyone verifying a UI change from now on: a Playwright run always
fetches cold, so **it cannot reproduce this class of bug**. It was invisible to
every check in §8 and §9 for that reason.

### 10.2 Distinct path data is not distinct shape

§9.3 replaced the duplicated rail glyphs and verified the fix by asserting
"10 distinct paths across 10 rail buttons" — comparing `d` attributes as
strings. That check passes for two glyphs that look nothing alike *and* for two
that look identical, so it proved nothing.

`registry` was an outer box with a header rule and a column divider. `dock` is an
outer box with a smaller box inset in a corner. Different `d`, same silhouette:
at 18 px both read as "rectangle containing a rectangle", and they sat six
buttons apart in the same column.

`registry` is now a row of records — a marker and a line, three times — with **no
enclosing box**. Nothing else in the rail is an unboxed row of lines, so it is
separable by silhouette from `events` (a plate), `dock` (an inset panel) and
`wall` (a grid), which are the three other rectangular glyphs.

The check that counts is looking at the rendered rail at its real size. A
screenshot of `#rail` at `device_scale_factor=3` is the artefact to compare; the
`d` diff is not.

### 10.3 Verified

* Fresh load with `sentinel.theme=light`: map background `#eef1f5`, motorway
  `#8a99ad` — the light palette, from the first paint, with no toggle involved.
* Full cycle light → auto → dark → light: background and road colour track the
  theme every time, 9 pins and the trace source present throughout, no errors.
* All ten rail glyphs distinct **by eye** at 3x, not only by path data.
* `Cache-Control: no-cache` on `/static/*` and on both pages; conditional GET 304.
* `pytest` over `backend/`: **26 passed**.

---

## 11. The command centre is dark-only again

Requested after seeing §9 in use: **remove the light-mode option from the
command centre.** The wall keeps its three-way control unchanged.

This restores the position tokens.css argued from the start, and the reasoning
still holds: the command centre is a map-first surface watched for whole shifts
in a room kept dim so camera feeds stay readable, and a light map beside a
night-time feed destroys the adaptation the operator needs to see the feed. The
wall is the page with the other audience — a laptop in a lit office, a
projector, a screenshot pasted into a report — and that is where the option now
lives, alone.

Removed:

* the theme cycle button at the foot of the rail, and its label helper;
* the `themeEvents` listener that followed an OS flip mid-shift;
* the `theme.js` import in `app.js` — **the page no longer reads the stored
  preference at all**;
* the no-flash `<head>` snippet and the light `noscript` variant in
  `index.html`; `color-scheme` is back to `dark`.

`mapView.init` is now called with a literal `'dark'` rather than a resolved
preference, so the call site says which palette the map is on.

**The important case is the shared preference.** `sentinel.theme` is one key
across both pages, so "remove the option" could not just mean removing the
button — an operator who set the wall to light would still have got a light
command centre. Because `app.js` no longer imports `theme.js`, nothing sets
`data-theme` on this page and `:root` supplies dark. Verified with the
preference deliberately left at `light`: stored pref `light`, command centre
`data-theme` unset, topbar `rgb(12, 16, 23)`, map background `#0a0e14`, 9 pins,
9 rail buttons with no Theme among them — and the wall on the same browser still
`light` with its control reading `Dark / Light* / Auto` and 31 cards.

### What was kept, and why

The **light map palette in `basemap.js` and `mapView.restyle()` are still
there**, and nothing calls them. That is deliberate rather than an oversight:
these files are not yet committed to git, so deleting that code would destroy it
outright rather than park it in history, and it is working, tested, and cheap to
leave. Reinstating the option is a matter of putting the rail button back.

If it is decided the option is not coming back, the things to delete are:
`LIGHT`/`setPalette`/`paletteName` in `basemap.js` (collapsing `P` back to flat
constants), `restyle()` and the `gisLineData` cache in `map.js`, and the
`--vignette-*` / `--pin-*` / `--map-chip` overrides in the light block of
`tokens.css`. The rest of the light palette must stay — the wall uses it.

The note over `buildRail()` in `app.js` records all of this at the call site.

### 11.1 Verified

* Command centre dark with the stored preference set to `light`.
* Wall unaffected: light, control present, 31 cards.
* No console errors on either page.
* `pytest` over `backend/`: **26 passed**.

---

## 12. "Two buttons with the same camera symbol" — the stale-module bug again

Reported after §11. The current build has **one** camera glyph in the rail: every
`<path>` on screen was enumerated with the Cameras panel open and exactly one
carried the camcorder `d`. The two-camera rail is what a **stale `ui.js` beside
a fresh `app.js`** renders, and it was reproduced deliberately to be sure.

### 12.1 Why a half-stale build produced *that* symptom

`icon()` ended with `PATHS[name] || PATHS.target` — a missing glyph silently
became an existing, valid one. Follow it through a mixed cache:

* `app.js` is fresh, so the preview-dock button asks for `icon('dock')`.
* `ui.js` is stale, so it has no `dock` **and** still has `events` set to the
  same camcorder path as `camera`.
* The dock quietly renders the `target` glyph, and `events` renders a camera.

Result: a rail with the camera on **Cameras** and again on **ANPR events**, no
error anywhere, and a fallback that looks deliberate. Reproduced by serving the
previous `ui.js` through a route intercept: **2 camera glyphs on the stale
build, 1 on the current one.**

This is the same root cause as §10.1 — §10.1 stopped it recurring, but a browser
that had already cached the old modules keeps them until it revalidates once.
**One hard reload (Ctrl+Shift+R) clears it permanently**; after that
`Cache-Control: no-cache` does the work.

### 12.2 A missing icon now looks missing

The substitution was the real defect, because it converted a broken build into a
silently wrong interface. `icon()` now:

* renders a deliberately ugly hollow **dashed square** for an unknown name,
  never a real glyph;
* `console.warn`s with the name and the sentence "If the interface was just
  updated, reload with cache disabled" — loud, because the usual cause is a
  stale module rather than a typo, and an operator cannot diagnose that from a
  wrong picture;
* uses `hasOwnProperty` rather than truthiness, so a future empty-string path is
  reported rather than swallowed.

Verified: the stale build now emits `icon(): no glyph named "dock"`.

### 12.3 The lesson, third time

§9.3 checked glyphs by diffing `d` strings and missed a visual duplicate.
§10.2 fixed that by checking the rendered rail. This one was invisible to *both*,
because the code was right and the delivered bytes were not.

A UI claim is only as good as the bytes the operator's browser is running.
Playwright always fetches cold, so **no automated check in this repo can observe
a stale-cache failure** — the only instruments that can are the `Cache-Control`
header (§10.1) and a loud failure when a module disagrees with its callers
(§12.2). Both are now in place.

### 12.4 Verified

* Current build: exactly **1** camera glyph in the rail; 9 buttons, 9 distinct
  paths, all distinct by eye.
* Deliberately stale `ui.js`: **2** camera glyphs, and the new warning fires.
* `pytest` over `backend/`: **26 passed**.

---

## 13. The ANPR console — `/plates` (removed in §14)

Built from a 19.3 s screen recording (`demo.mp4`, 848x480) of a commercial VMS,
supplied as "make me a similar system for number plate reading".

### 13.1 What the reference system actually does

Cameras named `SEWA DHAM_cam 2 / _cam 4 / _HIFOCUS`, Delhi and UP plates,
2022-07-06 afternoon. One screen, two regions:

* a 2x2 live camera grid with burned-in timestamps;
* a right rail titled **Plate Comparison**, newest first, each card carrying a
  **cropped plate image**, a vehicle-class badge, the normalised registration
  (`DL5SW7854`, `UP14DV3345`, `DL6CP7232`, `UP13BA6337`), the source camera, a
  to-the-second timestamp, and two action icons.

The rail is the system. And "comparison" is not decoration: `UP13BA6337` appears
from `cam 4` at 17:06:48 **and** `HIFOCUS` at 17:06:47 — the same vehicle caught
by two cameras a second apart. `DL5SW7854` does the same at 17:06:31 / 17:06:30.
Cross-camera corroboration is the one claim such a console can check against
itself rather than assert, which is why it earned its own treatment here.

### 13.2 The gap was narrower than it looked

Sentinel already had the detector, tracker, plate localiser, OCR, the Indian
layout normaliser, multi-camera MJPEG, 10,307 sightings and cross-camera
tracing. Two things were genuinely missing.

**`plate_crop_path` was written as `None` on every row.** The column had been in
the model since the first migration and `ingest.py` set it to `None`
unconditionally — so for the platform's whole life every reading shipped a
photograph of a *car* with a registration asserted underneath it, and an
operator checking a reading by eye had nothing to check it against. `Track` now
carries `best_plate_crop` / `best_plate_confidence`, the OCR loop keeps the
region that produced the **best accepted** read, and `_write_sighting` writes it
at JPEG quality 92 (against 82 for the vehicle frame — this crop is 40 px tall
and is the one image anybody zooms into).

The rule pinned by `tests/test_plate_crops.py`: **a crop is kept only for an
accepted read.** A candidate OCR rejected is a picture of a bumper or a vent
that happened to be rectangular, and attaching one to a row would be inventing
evidence. The tests also pin `.copy()` — the candidate is a view into a frame
buffer the decoder is about to overwrite, so storing the view yields a crop of a
different vehicle entirely, under load, intermittently.

**There was no combined grid-and-rail view.** `services/plates.py` +
`GET /api/plates/recent` + `/plates`.

### 13.3 Corroboration, and the condition that matters

`services/plates.py` calls two reads corroboration only when they share a
normalised plate, come from *different* cameras, fall within `window_seconds`
(default 60) on the overlay clock, **and sit in the same recording cluster.**

That last condition is the one this grid forces. These cameras are not a
synchronised network — their footage spans four dates and five time clusters —
so two reads "four seconds apart" on cameras from different clusters are not
four seconds apart in the world, and calling that corroboration would be
inventing a journey. Cross-cluster matches are not dropped; they are returned
with `same_cluster: false` and the card renders them greyed and labelled
"different recording window".

### 13.4 The console

`/plates` — `plates.html`, `js/plates-page.js`, `css/plates.css`. Tiles, stages,
idle states, live badges and the focus view are reused from `wall.css` rather
than re-cut; they are the same components doing the same job.

* Grid left (all 31 cameras, streaming only when clicked, same server cap and
  slot chip as the wall), rail right at a fixed 380 px. The asymmetry is
  deliberate: the rail is scanned top-to-bottom at a fixed rhythm and must not
  reflow.
* Cards lead with the **crop**, and where none was kept they say
  "no crop kept — read predates crop capture" rather than substituting the
  vehicle frame, which would look like evidence without being any.
* `plate_raw` is shown whenever the layout normaliser changed it, because an
  operator disputing a reading needs to see what was actually recognised.
* A `timestamp_source` other than `overlay` is badged — the burned-in clock is
  the authoritative event time here and anything else deserves flagging.
* **Polling, stated.** `since_id` means the steady-state poll returns `[]`, and
  the footer prints when the last poll completed and whether it failed. A silent
  rail has two causes with opposite meanings — nothing is being read, or the
  page stopped talking to the server — and the operator must be able to tell
  them apart.

### 13.5 The empty rail is the expected state, so it explains itself

**Of 31 cameras, 1 has ever produced a reading**, and the 4 rows it produced all
predate crop capture. `summary()` surfaces `cameras_reading / cameras_total` for
exactly this reason: a console reading "0 plates" with no explanation looks
broken, and this one is not broken — the estate is night-time wide-angle PTZ
where a registration is 20-40 px across. The empty state says so, and says the
chain is proven at 0.99 on a rendered plate and 0.88-0.99 on the checkpoint feed.

`OWN-01`'s source file is **gone** (`stream_url` is empty), so no plate-producing
source currently exists and the rail cannot be repopulated by running anything.

`backfill_plate_crops.py` tried to recover crops for the 4 historical rows from
their evidence frame and stored bbox. It recovered **none**: no re-localised
candidate re-read as the plate on the row. That is the script working correctly
— it keeps a crop only when the re-read matches, because attaching the widest
rectangle it found would be attaching a picture to a claim it does not support.
The rows keep a null crop and the console states the absence.

`register_file_camera.py` is the supported way to give it something to read:
it points any camera at a local video file (`stream_url` takes a path; OpenCV
opens one as readily as an RTSP URL, which is why there is no "file connector"),
decodes a frame up front to fail fast on a bad codec, and warns below ~1000 px
wide that registrations will not resolve.

### 13.6 Verified

* `/plates` cold: bounced to the gate with "The ANPR console needs a signed-in
  session", signed in, landed **on `/plates`** — `/plates` had to be added to
  `NEXT_ALLOWED`, which until now only knew `/` and `/wall`.
* 31 tiles; CAM-01 activated and painted live video with detection boxes; slot
  chip `0 / 4` -> `1 / 4`; tile 388x255 with a 386x217 stage (no letterbox
  collapse).
* Rail: 4 cards, correct plates and confidences, all four correctly showing the
  stated no-crop state.
* Light theme: chrome inverts, crop ground stays `rgb(4, 6, 10)`.
* Rail glyphs: **10 buttons, 10 distinct paths, distinct by eye** — the new
  `anpr` glyph is corner brackets rather than a fifth rectangle, because two
  box-shaped glyphs have already had to be redrawn for looking alike (§10.2).
* `pytest`: **31 passed** (26 + 5 new).

---

## 14. The ANPR console was removed

Requested one turn after it was built: remove the console, keep plate reading.
So §13's page is gone and the capability underneath it is untouched.

Deleted: `static/plates.html`, `static/js/plates-page.js`, `static/css/plates.css`,
`app/services/plates.py`, the `/api/plates/recent` and `/api/plates/summary`
routes, the `/plates` page route, `platesRecent`/`platesSummary` in `api.js`, the
rail button, the `anpr` glyph, and `/plates` from `NEXT_ALLOWED`/`NEXT_NAMES`.

Verified gone and nothing else with it: `/plates` and `/api/plates/recent` both
404; `/` and `/wall` still 200; plate readings still served by
`/api/sightings/recent?with_plate_only=true`; a grep for every console symbol
across `app/` and `static/` returns nothing.

### 14.1 Kept deliberately

Two things from §13 are **not** part of the console and were left in place:

* **Plate-crop capture** (`Track.best_plate_crop`, the ingest OCR loop,
  `_write_sighting`, and `tests/test_plate_crops.py`). This fixed a standing
  defect — `plate_crop_path` had been written as `None` on every row since the
  first migration — and it is what makes a reading checkable by eye. It is
  independent of any particular page; the ANPR events panel and the evidence
  view both benefit.
* **`register_file_camera.py`**, which points a camera at a local video file.
  Useful whenever this platform has to be shown reading anything, console or no.

`backfill_plate_crops.py` is also still there. It recovered nothing (§13.5) and
is a one-off, so it is the obvious next thing to delete if the tree is being
tidied — say so and it goes.

### 14.2 A self-inflicted bug, caught by §12's own fix

Removing the `anpr` glyph was done by slicing `ui.js` from the entry's comment to
the next key. The next key was `logout`, and **nine glyphs sat in between** —
`reports`, `audit`, `search`, `close`, `chevron`, `play`, `pause`, `guide`,
`camera` — so all nine went with it. Index-based slicing of source is exactly
the wrong tool for removing one entry from a map.

It was caught immediately, and by the thing built for it: §12.2 replaced
`icon()`'s silent `PATHS[name] || PATHS.target` fallback with a dashed
missing-glyph square plus a console warning. The rail rendered **Cameras** and
**Audit trail** as identical missing-squares on the very next check. Under the
old fallback both would have quietly rendered `target`, and a rail with two
concentric-circle buttons would have shipped.

All nine restored verbatim. A new check now walks every `icon('…')` call site in
`static/js/**` and asserts the name is defined — **31 defined, 21 literal call
sites, 0 missing** — and the rendered rail is back to 9 buttons, 9 distinct
paths, distinct by eye.

### 14.3 Verified

* `/plates` 404, `/api/plates/recent` 404, no dangling references.
* Command centre: 9 rail buttons, **0 missing glyphs on screen**, 9 pins.
* Navigation: command centre -> wall (31 cards) -> live tile `1 / 4 streaming`
  -> back to command centre, no console errors.
* Wall theme control intact (`Dark / Light* / Auto`).
* `pytest`: **31 passed** — the plate-crop tests stay green, which is the point.
