# Estrannaise HRT Monitor - Standalone

A standalone web app version of the [ha-estrannaise](https://github.com/PersephoneKarnstein/ha-estrannaise) Home Assistant integration. Same pharmacokinetic E2 modeling from [estrannaise.js](https://github.com/WHSAH/estrannaise.js).

All data is stored locally in a SQLite database and never touches the network.

## Quick start

```bash
# Build and run
docker compose up -d

# Open in browser
open http://localhost:8080
```

## Features

- Plotly E2 level chart (past + predicted)
- Log doses and blood test results
- Confidence bands from blood test calibration
- Menstrual cycle overlay
- Target / danger threshold bands
- Multiple regimen support
- All data stored locally in SQLite (`/data/estrannaise.db`)
- No network requests, Plotly.js bundled locally

## What it does

Estrannaise estimates your blood estradiol (E2) levels over time using a three-compartment pharmacokinetic model. You configure your dosing regimen (ester, method, dose, interval), and it renders a Plotly chart showing past levels and future predictions. Blood test results can be logged to calibrate the model to your individual response.

Multiple dosing regimens can be configured as separate integration entries and their contributions are summed additively on a single chart.

## Supported esters and methods

| Ester | Methods |
|---|---|
| Estradiol Benzoate | Intramuscular, Subcutaneous |
| Estradiol Valerate | Intramuscular, Subcutaneous |
| Estradiol Enanthate | Intramuscular, Subcutaneous |
| Estradiol Cypionate | Intramuscular, Subcutaneous |
| Estradiol Undecylate | Intramuscular, Subcutaneous |
| Estradiol (base) | Transdermal Patch, Oral |

Subcutaneous injections for EB, EV, EEn, and EC use the same PK model as intramuscular, as published studies show virtually identical pharmacokinetics between the two routes for oil-based depot injections. Estradiol Undecylate subcutaneous has its own community-derived model parameters.

Oral micronized estradiol is modeled using the same three-compartment framework with parameters calibrated to match published clinical data (Kuhnz 1993, Femtrace FDA review). The absorption rate constant is set very large (k1=100 day⁻¹) so the model effectively reduces to a Bateman (1-compartment absorption-elimination) curve. Oral dosing is only available for plain Estradiol, not esterified forms.

## Configuration

Settings are stored in `/data/config.json` inside the container (persisted via Docker volume).

Use the **Settings** button in the app to:
- Set units (pg/mL or pmol/L)
- Add/edit/remove dosing regimens
- Clear all data

### Timezone

Set your timezone in `docker-compose.yml` so dose time-of-day anchoring is correct:

```yaml
environment:
  - TZ=America/New_York   # or Europe/London, Asia/Tokyo, etc.
```

## Architecture

```
estrannaise-standalone/
├── backend/
│   ├── main.py        # FastAPI app, REST endpoints
│   ├── database.py    # SQLite async wrapper (aiosqlite)
│   ├── pk.py          # PK math engine (ported from const.py)
│   ├── scheduler.py   # Auto-dose generation (ported from coordinator.py)
│   └── config.py      # JSON config file management
├── frontend/
│   ├── index.html     # Single-page app (vanilla JS + Plotly)
│   └── static/
│       └── plotly-2.35.2.min.js
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/state` | Full state (E2, doses, blood tests, PK params) |
| GET | `/api/config` | App configuration |
| POST | `/api/config` | Update configuration |
| GET | `/api/doses` | List all doses |
| POST | `/api/doses` | Log a dose `{model, dose_mg, timestamp?}` |
| DELETE | `/api/doses/{id}` | Delete a dose |
| GET | `/api/blood-tests` | List all blood tests |
| POST | `/api/blood-tests` | Log a blood test `{level_pg_ml, timestamp?, notes?}` |
| DELETE | `/api/blood-tests/{id}` | Delete a blood test |
| DELETE | `/api/data` | Clear ALL data (irreversible) |

## Migrating from HA

If you want to carry over existing data from the HA integration's SQLite database:

1. Copy `<ha_config>/estrannaise.db` to the Docker volume's `/data/estrannaise.db`
2. The schema is compatible — doses and blood tests will load immediately
3. Note: the standalone app uses a single database without `config_entry_id` filtering,
   so all doses/tests across all HA entries will be merged (which is the desired behavior)

## How the PK model works

The integration uses a three-compartment pharmacokinetic model from [estrannaise.js](https://github.com/WHSAH/estrannaise.js), with parameters estimated via MAP estimation and MCMC in Esterlabe.jl (unreleased). For each dose, the blood E2 contribution at time $t$ (days after dosing) is:

$$E_2(t) = \frac{d ~ k_2 ~ k_3}{(k_1 - k_2)(k_1 - k_3)} \left( k_1 ~ e^{-k_1 t} - k_2 ~ e^{-k_2 t} - k_3 ~ e^{-k_3 t} \right)$$

where $d$, $k_1$, $k_2$, $k_3$ are ester/method-specific parameters. The total $E_2$ at any time is the sum of contributions from all past doses across all configured regimens.

Transdermal patches use the same three-compartment model, extended with a wear duration: the patch delivers a constant input during wear, then the residual compartments decay after removal. Patch PK parameters are calibrated for mcg/day input (e.g., a 100 mcg/day patch passes 100 to the model, not 0.1 mg).

Blood test calibration computes an exponentially-weighted average scaling factor (recent tests weighted more), clamped between 0 and 2, so predictions gradually align with your measured levels.

## Privacy

All data stays local. The SQLite database is stored at `/data/estrannaise.db` in Docker volume. Plotly.js is bundled locally (no CDN). No network requests are made.

## Future Work

- [ ] Light/Dark mode toggle
- [ ] Manage existing data
  - [ ] Delete/Modify logged blood test
  - [ ] Delete previous dose (manual/automatic)
- [ ] Unite time format (Logging uses AM/PM, Graph/Regimens uses 24h)
- [ ] Abiltiy to export data
- [ ] Investigate Auto-generate mode

## Credits

- PK model and parameters: [estrannaise.js](https://github.com/WHSAH/estrannaise.js) by WHSAH
- Original HA integration: [ha-estrannaise](https://github.com/PersephoneKarnstein/ha-estrannaise) by PersephoneKarnstein
- Charting: [Plotly.js](https://plotly.com/javascript/) (bundled locally)
