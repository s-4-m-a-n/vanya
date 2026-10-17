# Vanya: Citizen-Science Biodiversity Monitor

*Vanya* (वन्य) means "wild, of the forest". The codebase keeps the earlier codename
`digitalhunter`/`dh` for internal identifiers (database, event topics, storage keys).

People photograph animals, birds and plants, get an AI species ID, and earn XP weighted by regional
rarity. Verified records feed an organisation dashboard with maps, invasive alerts and AI analysis.

- Product requirements: [docs/PRD.md](docs/PRD.md)
- Implementation stages and progress: [docs/STAGES.md](docs/STAGES.md)
- Architecture and GCP migration path: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Feature guide with screenshots: [docs/Vanya-Feature-Guide.pdf](docs/Vanya-Feature-Guide.pdf) (source: `docs/guide/`)

## Layout

```
backend/   FastAPI API + workers (Python 3.12, uv), Alembic migrations, tests
web/       Citizen PWA (React + Vite + TypeScript): capture, offline queue, species picker
docker/    Local infrastructure config (DB init scripts)
docs/      PRD, stage tracker, architecture
```

## Run locally

Requires Docker, [uv](https://docs.astral.sh/uv/) and Node.js.

Species ID uses Gemini through OpenRouter: put your key as `OPENROUTER_API_KEY` in
`backend/.env` (read by `uv run`) and in `.env` (read by docker compose). Without a key, captures still work and observers pick species manually;
set `SPECIES_ID_PROVIDER=stub` for deterministic fake IDs.

```bash
cp .env.example .env && cp .env.example backend/.env   # then set real secrets
docker compose up -d db                                # PostGIS on localhost:5433
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.cli seed-taxa
uv run python -m app.cli create-user --email admin@example.org --name Admin --role admin
uv run uvicorn app.main:app --reload                   # http://localhost:8000/docs
```

In a second terminal, the citizen app (proxies `/api` and `/media` to the API):

```bash
cd web
npm install
npm run dev                          # http://localhost:5173
```

### On your phone

Phones only allow GPS on HTTPS pages, so serve the app over HTTPS on your Wi-Fi:

```bash
cd web
HTTPS=1 npm run dev -- --host --port 5174
```

1. Put the phone on the same Wi-Fi as the computer.
2. Open `https://<computer's Wi-Fi IP>:5174` (Vite prints it as "Network"; find it with `hostname -I`).
3. Accept the certificate warning once: it's a local self-signed certificate. On Chrome tap
   **Advanced → Proceed**; on Safari **Show Details → visit this website**.
4. If it doesn't load, the computer's firewall may be blocking the port:
   `sudo ufw allow 5174/tcp`.

The API itself stays on `localhost:8000`; the web server forwards requests to it.

### Verifying sightings (Stage 2)

Make an account a verifier so it gets the **Review** tab, and use simulated demo experts when
you're testing alone:

```bash
cd backend
uv run python -m app.cli set-role --email you@example.org --role verifier
uv run python -m app.cli simulate-reviews --limit 20   # labelled "Simulated expert N (demo)"
uv run python -m app.cli route-pending                 # re-route anything stuck in "submitted"
```

### Rarity and rewards (Stage 3)

```bash
cd backend
uv run python -m app.cli recompute-rarity                 # schedule daily (Cloud Scheduler on GCP)
uv run python -m app.cli add-restricted-area --name "Heronry" --geojson zone.geojson \
  --reason "Breeding colony: no rewards"
uv run python -m app.cli list-restricted-areas
```

### Location protection (Stage 4)

```bash
cd backend
uv run python -m app.cli refresh-statuses                   # IUCN status for unchecked species
uv run python -m app.cli check-seed-statuses                # compare the seed list with IUCN
uv run python -m app.cli set-sensitivity --species "Manis crassicaudata" --level hidden
uv run python -m app.cli refresh-geoprivacy                 # after migrating existing data
```

### Organisation dashboard (Stage 5)

The dashboard lives at `/dashboard` in the same web app (e.g. http://localhost:5173/dashboard).
Set up an organisation and give someone access:

```bash
cd backend
uv run python -m app.cli create-org --name "Thames Wildlife Trust" --kind ngo
uv run python -m app.cli add-org-member --org "Thames Wildlife Trust" --email you@example.org --role admin
uv run python -m app.cli add-region --org "Thames Wildlife Trust" --name "London" --geojson london.geojson
```

Org admins can then draw or upload more regions, manage members, and build the watchlist in
the dashboard's **Manage** section.

### Alerts, analyst and reports (Stage 6)

In the dashboard: the **Alerts** inbox (invasive, priority and new-record alerts for your
regions), **Ask the analyst** (plain-language questions answered by Gemini from your verified
records), **Reports**, and alert and webhook settings under **Manage → Alerts**.
Scheduled reports for every organisation:

```bash
cd backend
uv run python -m app.cli generate-reports --days 30 --locale en
```

Or run the API in its container (the same image used on Cloud Run):

```bash
docker compose --profile app up -d
```

## Tests

```bash
cd backend
uv sync --extra gcp     # optional: enables the GCP adapter contract tests
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests use the `digitalhunter_test` database created by the compose init script and never
call OpenRouter or GBIF.

```bash
cd web
npm test && npm run typecheck && npm run build
```
