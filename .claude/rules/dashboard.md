## Dashboard

A local FastAPI web app wrapping the bot runtime with live telemetry, run history, screenshot browsing, config editing, and lifecycle controls.

```bash
uv run python dashboard.py   # starts at http://127.0.0.1:8000
```

### Key Source Files

- [dashboard.py](dashboard.py) — entry point
- [src/dashboard/server.py](src/dashboard/server.py) — FastAPI routes, SSE stream
- [src/dashboard/data_service.py](src/dashboard/data_service.py) — aggregates YAML/runtime data
- [src/dashboard/runtime.py](src/dashboard/runtime.py) — file-backed snapshot, events, control, process state, screenshot indexing
- [src/dashboard/static/](src/dashboard/static/) — frontend (index.html, app.js, styles.css)

### Runtime Data (`data/output/dashboard/`)

| File | Purpose |
|---|---|
| `events.jsonl` | Append-only structured event log (all runs) |
| `snapshot.json` | Latest live state |
| `control.json` | Pause/stop signals from UI |
| `process.json` | Background bot process metadata |
| `screenshots.json` | Screenshot metadata index |
| `screenshots/latest.png` | Latest screenshot for live panel |

### Bot Controls

Pause/stop use file-backed signals checked by the bot during execution. Controls only affect runs launched via `/api/control/start` or the `Start Run` button — not processes started manually via `main.py`.

### Config Editing

Editable via UI: `config/search_config.yaml` and a safe subset of `config/app_config.py`. `.env` secrets are never edited by the dashboard.

The search config editor normalizes `24_hours` ↔ `day_24_hours` for the date-posted field.

### API Routes

Pages: `/`, `/runs/<run_id>`

Control: `POST /api/control/{start,pause,resume,stop}`

Data: `/api/summary`, `/api/live`, `/api/jobs`, `/api/runs`, `/api/runs/<run_id>`, `/api/runs/<run_id>/{jobs,events,screenshots,export}`

Config: `GET/PUT /api/config`, `PUT /api/config/{search,app}`

Other: `/api/events/stream` (SSE), `/api/screenshot`, `/api/screenshot-file?path=...`, `/api/process`, `/api/meta`
