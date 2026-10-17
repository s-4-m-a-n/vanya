# Implementation stages

Progress tracker for [PRD.md](PRD.md). No Jira or GitHub tracking: this file is the source of truth.
Hackathon MVP target (PRD §12): F1–F5 with a simulated expert queue, plus F6 and/or F7.

| # | Stage | PRD | Status |
|---|---|---|---|
| 0 | Foundation | §8, §9 | ✅ Done |
| 1 | Capture and identify | F1 | ✅ Done |
| 2 | Verification pipeline and integrity checks | F3, §6 | ✅ Done |
| 3 | Rarity and gamification | F2, §6 | ✅ Done |
| 4 | Sensitive-location protection | F5 | ✅ Done |
| 5 | Organisation dashboard | F4 | ✅ Done |
| 6 | Alerts and Gemini analyst | F6, F7 | ✅ Done |
| 7 | Demo scenario and GCP migration | §12 | ⏳ Next |

---

## Stage 0 — Foundation ✅

- FastAPI backend (`backend/`), Cloud Run–ready Dockerfile, docker compose with PostGIS.
- Ports and adapters with provider switches: storage (local ⇄ GCS), events (in-process ⇄ Pub/Sub).
- SQLAlchemy models plus Alembic migrations (PostGIS enabled in the first migration).
- Auth: register/login/me with JWT and Argon2; roles `observer`, `verifier`, `analyst`, `admin`; admin role management.
- Taxonomy reference table, with 48 sample species (animals, birds, plants) and localised names.
- Signed media URLs locally, mirroring GCS V4 signed URLs: photos are never public.
- 32 tests against a real PostGIS test DB, including a migration down/up round trip and mocked GCS/Pub/Sub contract tests.

## Stage 1 — Capture and identify (F1) ✅

- `observations` + `id_candidates` tables; lat/lon columns with a PostGIS geometry derived from them (portable to BigQuery's `ST_GEOGPOINT`).
- `POST /api/v1/observations` (multipart): 15 MB limit; JPEG/PNG/WebP/HEIC; EXIF GPS, capture time and camera extracted; stored copy re-encoded without EXIF; idempotent on `client_uuid` for offline retries.
- Location: device GPS first, EXIF GPS as fallback, otherwise rejected. Time: capture time, EXIF time (read in the device's timezone when the camera omits one), or receipt time.
- Species ID via **Gemini 3 Flash (preview) on OpenRouter**, behind an LLM port (`LLM_PROVIDER=openrouter` → `vertex` on GCP). Returns top-k candidates with confidence, a suggested habitat and a short explanation in the user's language. Only coarse location (~11 km) is sent to the model.
- Candidate names are checked against the **GBIF backbone**: synonyms map to accepted names, made-up names are dropped, and insects/fish/fungi are flagged out of scope (PRD §3). New in-scope species are added to `taxa` with `source=gbif`.
- If identification fails, capture still succeeds and the observer picks the species manually.
- Location consent stored on the user (`PATCH /auth/me`); capture is blocked without it.
- `PUT /observations/{id}/species`, `GET /observations/mine`, `GET/DELETE /observations/{id}`; reviewers can read, only owners/admins can change. Choosing a species publishes `observation.submitted` for Stage 2.
- **Citizen PWA** (`web/`, React + Vite + TypeScript): sign-in, consent and safety guidance, camera or gallery capture, GPS with accuracy, an offline queue in IndexedDB that syncs on reconnect/foreground/every 30 s, candidate picker with confidence bars, species search, offline thumbnails, English/Hindi/Spanish.
- Tests: 82 backend (including mocked OpenRouter/Vertex/GBIF contracts) + 7 for the offline sync engine. Verified end to end in a browser at phone size.
- Live check on 2026-10-17 with Gemini 3 Flash via OpenRouter and real photos (CC BY-SA, Wikimedia Commons):
  two house sparrows in flight, Kathmandu → *Passer domesticus* 0.95; common myna, Kolkata, Hindi UI → *Acridotheres tristis* 0.98 ("मैना") with a Hindi explanation; a plain image → "no organism", no guesses. GBIF-validated alternatives were added to `taxa`.
- Cost ≈ $0.0015 per identification (~1,700 input tokens incl. the photo). Each response is capped at `LLM_MAX_OUTPUT_TOKENS` (4,096); without the cap OpenRouter reserves credit for the model's full 65k window.
- Gemini returns an English name with every candidate as well as the localised one, so species added from any locale get an English fallback.

Known limitations (accepted for MVP):
- Model time is ~3.4–3.8 s per photo, slightly over the PRD's ~3 s target; `minimal` reasoning didn't help (time is dominated by image input). The first sighting of a species not in `taxa` adds a GBIF lookup (cached afterwards).
- No background sync while the app is closed (the Background Sync API isn't available on iOS); queued captures upload when the app is next opened.
- Common names the model returns are stored for new GBIF-added species; vernacular names from GBIF could replace them later.
- Testing GPS on a real phone needs HTTPS: `HTTPS=1 npm run dev -- --host`.

## Stage 2 — Verification pipeline (F3, §6) ✅

**Routing** (`services/routing.py`, pure functions). Runs when a species is chosen, as an `observation.submitted` event handler, so it's background work locally and Pub/Sub-driven on GCP.
- **Auto-verified** only when the observer picked the AI's top candidate, confidence ≥ `AUTO_ACCEPT_MIN_CONFIDENCE` (0.85), the species is common here, and there are no hard integrity flags.
- **Expert review (1 reviewer):** low confidence, differs from the AI, no AI ID, the AI saw no organism, no local baseline, or any hard flag.
- **Rare claims (2 reviewers):** threatened (CR/EN/VU), extinct claims (EX/EW), locally uncommon, or out of range.
- **Auto-rejected:** the observer re-submitted their own exact photo (no reputation penalty).

**Regional commonness** (`services/commonness.py`). Uses GBIF occurrence counts within ±0.5° (common at ≥ 20 records) and ±2.5° (out of range at 0), plus our own verified records within 25 km / 250 km. Counts are cached per species and grid cell for 30 days in `occurrence_counts`, and seed taxa get their GBIF key on first use. Switch: `OCCURRENCE_BASELINE=gbif|none`.

**Integrity checks** (`services/integrity.py`). Hard flags force review; soft flags are shown to reviewers only.
- Hard:
  - The same photo bytes as someone else's (`duplicate_other`), or a near-duplicate via a 64-bit perceptual dHash with Postgres `bit_count` (`near_duplicate_other`).
  - Photo GPS more than 5 km from the device location (allowing for GPS accuracy).
  - Photo time more than 24 h from the device time.
  - Gemini photo flags (free with the ID call): `screenshot`, `illustration`, `watermark`, `captive_or_cultivated`.
- Soft: no camera metadata, editing software, GPS accuracy worse than 1 km, similar to the observer's own photo (burst shots).

**Expert review.**
- `GET /reviews/queue`: verifiers and admins; excludes own and already-reviewed records; rare first, then oldest; filtered by group and by the verifier's saved review area (`PATCH /auth/me`).
- `POST /reviews/{id}`: confirm (optionally as a different species), or reject with a reason.
- Consensus needs `required_reviews` matching confirmations that lead every other outcome; disagreeing experts wait for a tiebreak. Re-identifying a record as a threatened species raises it to 2 reviewers.
- The observer's own pick is kept in `observer_taxon_id` when experts correct it.

**Reputation:** auto-verified +1 · expert-verified +2 (rare +5) · corrected 0 · rejected −3 · rejected as duplicate or wrong place/time −10.

**Events:** `observation.in_review`, `observation.verified` and `observation.rejected`; Stage 3 XP will subscribe to these.

**GCP readiness:**
- `POST /internal/events/pubsub` is the Pub/Sub push endpoint. It checks a Google-signed OIDC token, and handler errors return 500 so Pub/Sub retries. Published messages now carry a `topic` attribute.
- In-process events run on a background thread pool (`EVENTS_INPROCESS_ASYNC`), mirroring Pub/Sub, so responses stay fast.

**CLI:** `set-role` (make someone a verifier), `route-pending` (re-route stuck submissions; schedule it on GCP), and `simulate-reviews`. Simulated reviewers are clearly labelled "Simulated expert N (demo)" (PRD §12); they reject records with hard flags and otherwise agree with any real expert's identification.

**Web app:**
- New statuses: Checking… / Awaiting expert / Verified / Not accepted. The app polls while a record is being checked.
- A verification panel shows rare finds, review progress, corrections ("you said X") and expert notes.
- Verifiers and admins get a **Review** tab: one card at a time with the observer and their reputation, the AI suggestions, why the record is in review and the checks run. Actions: confirm, different species, reject with a reason, or skip; a review area can be set from your location.
- English/Hindi/Spanish.

**Verified:**
- 164 backend tests (including pure routing/tally tables, duplicate hashing, mocked GBIF, push auth) + 7 web tests.
- End to end in a browser with live GBIF: a sparrow at Kathmandu was auto-verified. A myna labelled "tiger" went to review as a rare claim; the expert corrected it to Common Myna, and a simulated second expert agreed. The observer saw "You identified it as Tiger; experts confirmed a different species" with the expert's note.

**Fixed during testing:**
- Upload crashed the integrity check when identification failed.
- An expert's species correction left the old species in the response.
- 7 s confirm latency from live GBIF calls (routing is now background work, and GBIF calls in the background get a 20 s timeout).
- A stale "Rare find" badge after a correction to a common species.

**Known limitations:**
- Exact coordinates are visible to verifiers. Stage 4 adds generalisation and audit logs.
- The near-duplicate search is a full scan; fine at MVP scale. Add an LSH or BK-tree index before large volumes.
- The GBIF baseline reflects GBIF's own sampling bias (dense near cities). Sparse areas look "uncommon" and go to review, which is acceptable but adds load.
- Verifier rewards and accuracy tracking are not in yet.

## Stage 3 — Rarity and gamification (F2, §6) ✅

**Rarity score** (`services/rarity.py`): 0–1 per species and ~55 km grid cell, cached for 7 days in `rarity_scores`, with a scheduled recompute (`recompute-rarity`).
- `score = max(status, 0.75 × frequency + 0.25 × endemism)`.
- **Status:** CR/EX/EW 1.0, EN 0.8, VU 0.6, NT 0.3, DD 0.2, LC/NE 0.
- **Frequency:** the species' GBIF + verified records in the cell, compared with the **most-recorded species of its group there** (birds, mammals, reptiles, amphibians or plants), on a log scale. This cancels out both how heavily an area is sampled and how evenly a group's records spread.
- **Endemism:** range size from the number of countries with ≥3 GBIF records.
- **Tiers:** Common < 0.2 ≤ Uncommon < 0.4 ≤ Rare < 0.6 ≤ Very rare < 0.8 ≤ Exceptional.
- **Calibrated on real GBIF data:** rock dove in London and house sparrow in Kathmandu are common (0.0); dandelion in London 0.17 (common); bank myna in Kolkata 0.41; house crow in London 1.0 (a genuine vagrant there).

**XP ledger** (`xp_entries`, `services/xp.py`). A ledger re-synced on every verification event, so it's idempotent and the order of events doesn't matter.
- **Lifecycle:** provisional while in review → confirmed when verified → void when rejected. Confirmed awards are final.
- **Amounts:** base 10; rarity bonus `100 × rarity²` (capped at 200/day per observer); first verified sighting of a species +20; explorer bonus +15 in under-sampled cells (< 200 GBIF group records), per PRD goal 2.
- **Abuse limits:** repeats of the same species within 1 km and 24 h earn 2. Records inside `restricted_areas` earn 0 but are still kept as data. Disturbance is a rejection reason (Stage 2).
- **Experts' correction:** XP is recomputed for the confirmed species.

**Levels** use 10 named tiers (Seedling … Legend), then a level per 1,000 XP. **Badges:** 12 (first sighting, 10/50/100 species, birder, wildlife watcher, botanist, rare find, conservation witness, explorer, regular, trusted observer).

**Leaderboards:** `GET /leaderboards?period=week|month|all` (confirmed XP only), global or nearby (radius around a point). Observers can hide themselves (`leaderboard_visible`), and only display names and levels are shown. Progress lives at `GET /me/progress`.

**Analytics port (BigQuery on GCP):** `ANALYTICS_PROVIDER=postgres|bigquery`.
- Verified records are exported on `observation.verified` (no user identifiers) and removed on `observation.deleted`.
- `species_cells()` drives the rarity job.
- Grid cells use `floor`, so Python, Postgres and BigQuery agree; `round` differs on .5 boundaries.

**CLI:** `recompute-rarity` (schedule it), `add-restricted-area --geojson` and `list-restricted-areas`.

**Web app:**
- Each sighting shows "+N XP" (or "pending verification"), a breakdown, its rarity tier, and why XP was reduced or withheld.
- A new **Me** tab shows level and progress bar, pending XP, stats, the badge grid and leaderboards (week/month/all, global/near me), plus a visibility toggle.
- Screens keep polling until verification **and** the XP calculation have finished.

**Verified:**
- 233 backend tests (pure rule tables, ledger lifecycle, caps, repeats, restricted zones, leaderboards, BigQuery contract) + 9 web tests.
- In the browser with live GBIF: Kathmandu sparrow +30 XP confirmed; a "tiger" claim showed +94 XP pending (Exceptional); the weekly leaderboard ranked other test users correctly, with ties.

**Fixed during testing:**
- Frequency measured against group totals rated London dandelions "rare". Plant records are spread thin, and British dandelions are mostly recorded as an aggregate. Switched to the commonest-species reference.
- The app stopped polling before the XP calculation finished.
- Python vs SQL grid rounding mismatch.
- Slow test suite (Argon2 hashing per test user).

**Known limitations:**
- Species complexes (dandelions, brambles) are under-counted at species level in GBIF, which can inflate rarity slightly.
- Leaderboard totals are computed live; add a materialised view or cache before large scale.
- No verifier XP yet.
- Restricted areas are managed by CLI only (an admin UI comes with the Stage 5 dashboard).

## Stage 4 — Sensitive-location protection (F5) ✅

**Location levels** (`services/geoprivacy.py`). Each record has an effective level: the stricter of the species' sensitivity and the observer's own choice ("Hide my exact location publicly").

| Level | Public location | Applies to |
|---|---|---|
| Open | Rounded to ~110 m, to protect observers' homes | Everything else |
| Obscured | 0.2° (~22 km) grid cell | CR, EN, VU, EW, EX, DD; species whose status was never checked; unknown species |
| Hidden | Never shown publicly (reviewers see a 1° cell) | Admin override per species (e.g. poaching targets) |

`generalised_*` columns hold the only coordinates used outside the audited endpoint: public feed, leaderboards, review-queue filters, and reviewer views of protected records.

**Who sees exact coordinates:**
- The observer always.
- Everyone else only via `POST /observations/{id}/exact-location` with a purpose (verification, research, management, enforcement, other):
  - verifiers only while the record is in review;
  - analysts only verified records (to be narrowed to their organisation's regions in Stage 5);
  - admins any record.
- Every reveal is written to `location_access_log`: who, role, purpose, level, IP, user agent, time. There are no foreign keys, so the log outlives deleted users and records.
- Limit: 100 reveals per user per day (admins exempt). Admins read the log at `GET /admin/location-access`.

**Leaks closed:**
- The "near me" leaderboard and review-queue area filters now use generalised points and require a radius of at least 25 km, so shrinking the circle can't pinpoint a sighting.
- The species-ID prompt already sent only ~11 km coordinates; stored photos have no EXIF.

**Status checks:**
- IUCN Red List category via GBIF (`/species/{key}/iucnRedListCategory`). Unchecked species are looked up during routing, so a threatened species added from GBIF is routed and protected as threatened.
- Curated and admin statuses are never overwritten, including on re-seed.
- `check-seed-statuses` audited the seed list: 4 differences were fixed (green turtle EN→LC after IUCN's 2025 reassessment, kept obscured via a sensitivity override for nesting beaches; 3 plants NE→LC). All 48 now match.

**Public feed:** `GET /public/observations?bbox=` (GeoJSON, unauthenticated, cached for 5 min).
- Verified records only; generalised coordinates; date only; no observer identity, no photos.
- Hidden records are excluded; the bounding box is limited to 20°.

**Admin:**
- `PATCH /admin/taxa/{id}` sets sensitivity and/or a manual status, and re-protects that species' records.
- CLI: `set-sensitivity`, `refresh-statuses`, `check-seed-statuses`, `refresh-geoprivacy` (run after migrating existing data).

**Web app:**
- Capture has a "Hide my exact location publicly" option.
- The observer's sighting page says how it's shown publicly.
- The verifier review card shows "🔒 Protected species · Approximate area" with "Show exact location" (purpose picker, logged notice); the photo's GPS appears alongside for checking.

**Verified:**
- 273 backend tests (level table, generalisation maths, the role and status reveal matrix, audit survival, daily limit, public feed contents, hidden species, bbox validation, minimum radii, status lookup during routing, admin re-seed safety) + 9 web tests.
- In the browser: the owner saw exact coordinates and the verifier the cell; the reveal returned exact coordinates and wrote the audit row.

**Known limitations:**
- No organisation scoping for analysts yet (Stage 5).
- Photos themselves can reveal places (landmarks); reviewers see photos.
- IUCN data via GBIF needs a licence check before commercial use (PRD §13 Q2).
- National protection lists (e.g. India's Schedule I) aren't loaded; use per-species overrides.

## Stage 5 — Organisation dashboard (F4) ✅

**Organisation workspaces:** `organisations`, `organisation_members` (role admin or analyst), `regions_of_interest` (PostGIS MultiPolygons) and `watchlist_entries` (invasive or priority).
- Non-members get 404 (organisation ids can't be probed). Org admins manage members and regions; any member can edit the watchlist. Platform admins create organisations.
- Joining an organisation upgrades an observer to analyst.
- Region GeoJSON is validated (Polygon or MultiPolygon, closed rings, lon/lat ranges, vertex cap) and repaired with `ST_MakeValid`.

**Dashboard API:** `GET /orgs/{id}/dashboard/...`. All panels take the same filters (region, date range, group, species), so the numbers always agree.
- `summary`: verified sightings, species, observers, rare finds, threatened, invasive, top 10 species.
- `observations` (GeoJSON points) and `grid` (0.2°/0.5°/1° density cells).
- `trends`: per month, by group.
- `changes`: the biggest increases and decreases against the previous period of equal length, flagged as observer-effort-sensitive leads, not population trends.
- `rare`, `invasive` (driven by the watchlist) and `new-records` (first verified record of a species in the org's regions).

**Privacy carried through:**
- Region membership and every returned coordinate use the **generalised** point. Drawing a tiny region around a protected record can't pinpoint it (tested).
- Analysts' audited exact-location reveals are now limited to **verified records inside their organisations' regions**, closing the Stage 4 follow-up.

**Web dashboard** at `/dashboard` (desktop-first, lazy-loaded with Leaflet, excluded from the phone's offline cache):
- Filter row, headline tiles, and a Leaflet/OpenStreetMap map. Points are coloured by group; protected records are drawn as their uncertainty circle; there's a density-grid toggle.
- Map popups are built from DOM nodes with `textContent` (species names can come from an LLM) and offer an audited "show exact location" with a purpose.
- A clickable top-species list filters every panel; plus rare, invasive and new-record panels.
- A stacked monthly trend chart (palette validated for colour vision deficiency, legend, hover/focus tooltips, table view) and a changes table.
- Management tabs: regions (click-to-draw or GeoJSON upload), watchlist (species search), members.
- English, Hindi and Spanish.

**CLI:** `create-org`, `add-org-member`, `add-region --geojson`, `list-orgs`.

**Verified:**
- 291 backend tests (membership and 404 rules, region validation and repair, watchlist, every panel with in/out-of-region data, filters, tiny-region privacy, analyst reveal scoping) + 9 web tests.
- In the browser with 63 seeded sightings:
  - Panels and the map matched the expected counts.
  - Species filter, density grid, tooltip and table view worked.
  - The map popup reveal returned exact coordinates and wrote an audit row.
  - Click-to-draw saved a 744 km² region.

**Fixed during testing:**
- The map didn't fit to regions because React's development double-mount kept a stale "fitted" flag; added resize handling.
- Toggling drawing mode wiped Leaflet's container classes, because React re-rendered the className on the element Leaflet owns.
- "1 records" plural.
- The dashboard chunk was pre-cached on phones.

**Known limitations:**
- Dashboard aggregations run on Postgres. At scale they move to BigQuery via the analytics port: the SQL is portable (floor grids, `date_trunc`, counts), but the BigQuery export also needs the generalised coordinates.
- OpenStreetMap's public tiles are for development; production needs a tile provider or own tile server.
- Records near a region edge may fall in or out by their generalised point.
- No CSV/report export yet (Stage 6 reports).

## Stage 6 — Alerts and analyst (F6, F7) ✅

**Alerts (F6):** on `observation.verified`, every organisation whose regions contain the record's **generalised** point is checked for three kinds of alert:
- **invasive:** a watchlisted invasive species;
- **priority:** a watchlisted priority species;
- **new_record:** the first verified record of that species in the org's regions.

Rules:
- Per-organisation toggles (`alert_settings`). Alerts are de-duplicated per (org, record, kind), so redelivered events are harmless.
- Alert payloads hold only the generalised location, region name, species and dates.
- **Inbox:** `GET /orgs/{id}/alerts` (unread count, acknowledge).
- **Webhooks:** `alert.created` → signed POST (`X-Vanya-Signature: sha256=HMAC(secret, timestamp.body)`).
  - SSRF-safe: HTTPS only, every resolved address must be public (blocks private, loopback, link-local and metadata 169.254.169.254), checked on save and on each send; redirects are never followed.
  - Delivery attempts are logged in `alert_deliveries`. Failures raise in the handler so Pub/Sub retries on GCP.
  - The secret is shown once (rotate endpoint); there's a test-webhook button.

**Gemini analyst (F7):** `POST /orgs/{id}/analyst/ask`. The model never writes SQL:
1. Gemini turns the question into a structured **plan** from a fixed menu: counts, trend, compare_periods, top_species or unsupported, with taxonomy, region, date, IUCN and watchlist filters. It maps everyday words ("raptors") to families.
2. `validate_plan` allows only known values, bounded dates (10 years max), this org's region names and at most 50 rows.
3. `AnalystEngine` runs parameterised SQLAlchemy queries on verified records in the org's regions (generalised points).
4. Gemini writes the answer, highlights and caveats **from those rows only**, in the user's language.

The model only ever sees aggregate counts and species names: no coordinates, photos or observer identities. Every question is stored with its plan, data and answer (`analyst_queries`); limit 50 per user per day.

**Reports (F7):** `POST /orgs/{id}/reports` (week, month or quarter; or the `generate-reports` CLI on Cloud Scheduler).
- Built from aggregate "facts": totals, top species, changes, rare and threatened, invasive, new records, alert counts.
- Gemini returns **structured JSON** (title, summary, sections, recommendations), rendered as plain React text, so no model output becomes HTML.
- The print view saves as PDF.

**Web dashboard:** an alerts inbox with an unread badge; "Ask the analyst" with example questions, its interpretation, the answer, caveats, a "show the data" table and history; reports (generate, list, printable viewer); an Alerts settings tab (toggles, webhook, signing secret, test). English, Hindi and Spanish.

**Verified:**
- 328 backend tests: SSRF table (private, metadata, IPv6 loopback, LAN, credentials, http), signing, no-redirect, alert kinds, de-duplication, toggles, out-of-region, generalised payload, end-to-end via verification, webhook delivery and retry signal, plan validation bounds, engine queries (a raptor decline of −75% computed exactly), region grouping, unsupported questions, LLM failure, rate limit, members only, report facts with no coordinates, CLI.
- **Live** with Gemini 3 Flash in the browser:
  - "Which species were recorded less often in the last 3 months than before?" was answered correctly from the data (starling 5→1, −80%), with sensible caveats.
  - A monthly report was accurate and flagged the unusual tiger and raccoon records for checking.
  - Cost about $0.01.

**Fixed during testing:**
- Grouping by region crashed (wrong `join` arguments).
- Species rows had no common names.
- Report buttons were unreadable in dark mode.
- Reports said "Answered by" instead of "Written by".

**Known limitations:**
- Webhook URLs are re-resolved before each send, but a DNS answer could still change between the check and the connection; pinning the IP is the next hardening step.
- Webhook secrets are stored in the database (move to Secret Manager on GCP).
- No email or SMS channel (add one as another alert handler).
- Earth Engine environmental layers (F8) are not started (P1, post-MVP).

## Stage 7 — Demo and migration

- Seed script for the PRD §12 storyline, with all simulated data clearly labelled.
- GCP migration runbook, including the list of what's needed from you (see ARCHITECTURE.md).

---

## Decisions log

| Date | Decision |
|---|---|
| 2026-10-16 | Local-first on open-source tools; every GCP service sits behind an adapter with a `*_PROVIDER` switch. |
| 2026-10-16 | Project moved to `/home/suman/projects/DigitalHunter` (original drive was full). |
| 2026-10-16 | Citizen client is a PWA (React + Vite); it can later be wrapped with Capacitor for app stores. |
| 2026-10-16 | Backend: Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic; operational DB is Postgres/PostGIS (Cloud SQL on GCP). BigQuery is for analytics only. |
| 2026-10-16 | Progress is tracked in this file (no issue tracker). |
| 2026-10-16 | AI = Gemini 3 Flash (preview) via OpenRouter (`google/gemini-3-flash-preview`), used for species ID now and the analyst later; Vertex AI Gemini on GCP. Replaces running BioCLIP locally (machine RAM). |
| 2026-10-16 | v1 animal scope = birds plus mammals, reptiles and amphibians; GBIF classes outside that are out of scope. |
| 2026-10-17 | Regional commonness for routing = GBIF occurrence counts (cached) + our verified records; the Stage 3 rarity score builds on it. |
| 2026-10-17 | Verification runs as an event handler: background thread locally, Pub/Sub push on GCP. Clients poll while a record is "submitted". |
| 2026-10-17 | Integrity check details are shown only to reviewer roles, so observers can't learn to game them. |
| 2026-10-17 | Rarity frequency is relative to the group's most-recorded species in the cell (not group totals); calibrated on real GBIF data. |
| 2026-10-17 | XP is a ledger re-synced from observation status (idempotent), so Pub/Sub redelivery or out-of-order events are safe. |
| 2026-10-17 | Location protection: open ~110 m / obscured 0.2° / hidden; exact coordinates only via an audited, purpose-stated, rate-limited endpoint. Unchecked statuses count as sensitive. |
| 2026-10-17 | Green turtle: IUCN LC globally since 2025, but kept obscured (nesting beaches are egg-poaching targets). |
| 2026-10-17 | Invasiveness is per organisation (watchlist), since it depends on place; the watchlist will also drive Stage 6 alerts. |
| 2026-10-17 | Dashboard filters and coordinates use generalised points; exact locations only via the audited endpoint, scoped to the analyst's regions. |
| 2026-10-17 | Gemini analyst uses plan → validated, parameterised query → grounded answer; never text-to-SQL. Only aggregates reach the model. |
| 2026-10-17 | Reports are structured JSON from the model, rendered as text (no Markdown or HTML from the LLM). |
| 2026-10-17 | Product renamed from DigitalHunter to **Vanya** ("wild" in Nepali/Hindi; avoids hunting connotations). Logo: plant, bird, animal and fish in a camera viewfinder. Internal identifiers keep the `dh` codename. |
| 2026-10-17 | UI refresh: Plus Jakarta Sans, Lucide icons, forest-green/amber tokens with dark mode; settings moved into the Me tab; sightings filters and a "needs ID" prompt. |

## Open questions (from PRD §13)

1. Pilot region and organisation partner: affects demo seed data (needed by Stage 7).
2. Rarity/status sources and licences (IUCN, national lists, GBIF): sample statuses must be re-verified.
3. Species-ID model: pretrained open model (BioCLIP) now; fine-tuning decision later.
4. Rewards beyond points.
