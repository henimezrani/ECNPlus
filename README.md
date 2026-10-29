# ECN Companion

A dashboard for all 75 courses, with completed series and actual answered questions across each reading. It uses **Python 3.10+ and plain HTML/CSS/JavaScript**. On macOS and Linux, no package installation, framework, build step, or database server is required. Windows also needs the small `tzdata` package for Tunisia time-zone rules. The collector makes read-only GET requests to ECNPlus; the dashboard opens saved data immediately.

## Hosted version: Render + Neon

For access from an iPad or any browser without installing Python, use the free hosted deployment described in [DEPLOYMENT.md](DEPLOYMENT.md). It serves the UI and API together on Render and stores profile data and encrypted ECNPlus tokens in Neon. Local mode remains available below and does not require the hosted dependencies.

## Start here

Open a terminal in the cloned project folder:

```sh
cd "/path/to/ECNPlus"
python3 -m ecnplus configure
python3 -m ecnplus serve
```

Open **http://127.0.0.1:8765** and leave the terminal running. The home page lets you choose Yasmine, Houssein, or Autre. Press Ctrl+C to stop the dashboard. You can choose another port with `python3 -m ecnplus serve --port 8766`. It binds only to this computer's loopback address.

On this Mac, you can also double-click **Start Dashboard.command** in Finder to launch it from the correct folder.

## Run on Windows

1. Install [GitHub Desktop](https://desktop.github.com/) (which includes Git) and [Python 3.10 or newer](https://www.python.org/downloads/windows/). In the Python installer, enable the Python launcher (`py`). Install Google Chrome if it is not already present.
2. In GitHub Desktop, choose **File → Clone repository**, enter `https://github.com/henimezrani/ECNPlus.git`, and choose a private location in her Windows user folder, such as `Documents\ECN Companion`. The repository contains the application only; it does not include an ECNPlus token or anyone's saved study history.
3. Double-click **Start Dashboard.bat** in the cloned folder. On first launch, it checks for Tunisia time-zone data and installs the small `tzdata` package if needed; this step requires an internet connection. It starts the local server and opens Chrome at **http://127.0.0.1:8765/**. Leave the terminal window open while using the dashboard. If Chrome is not installed, Windows opens the default browser instead. If automatic setup cannot install `tzdata`, open Command Prompt in the project folder and run `py -3 -m pip install --user -r requirements-windows.txt`.
4. Open her profile at **/yasmine**, **/houssein**, or **/autre**. In **Paramètres → Connexion à ECNPlus**, paste that user's complete Authorization value and choose **Synchroniser**. Repeat for each profile that needs data. Each profile stores its own token, settings, and study history on this computer. The first collection can take several minutes. Replace a profile's token in its Settings if it expires.

Keep the cloned project folder in her own Windows profile rather than a shared/public folder. Profile data is stored separately under `%LOCALAPPDATA%\ECN Companion\profiles\<profile>`; Windows permissions come from the account's access-control list. The app does not set Unix-style owner-only file modes on Windows. These profiles are convenience separation, not login security: anyone who can use this Windows account can open each profile and its dashboard. The server listens only on this computer, not the local network.

To copy the Authorization value, open an authenticated ECNPlus page, open Developer Tools → Network, select a successful request to `api.ecnplus.tn`, and copy the **value** of its `Authorization` request header. Paste the complete `Bearer …` value. A bare token is accepted too. Cookies and other browser headers are not required by the verified API requests.

## Dashboard

- The home page has three independent spaces: **Yasmine** (`/yasmine`), **Houssein** (`/houssein`), and **Autre** (`/autre`). Each has its own saved study history, reading settings, and ECNPlus token. A simple access-code screen opens each space; no account registration is required.
- Overview totals and all course cards show completed series and answered questions for every configured reading and overall. Modules and university/year statistics stay out of the main view.
- Search accepts French names with or without accents. Choose a focus reading to filter courses with activity, no activity, or unfinished series; sort by name, answered questions, or completed series.
- Click a course to see its attempts, start dates in Tunisia time, actual answered counts, reading assignments and completion. Reading tabs filter these details. Repeated attempts within one reading are retained for reference, with only the highest count contributing to totals.
- **Synchroniser** updates the catalogue and histories in the background, with visible progress. The existing totals remain usable until the job finishes. An incomplete sync offers **Reprendre la synchronisation**, and expired tokens can be replaced in **Paramètres**. Opening the page does not trigger any ECNPlus requests.
- **Paramètres → Enregistrer et recalculer** changes reading dates and recalculates the cached dataset offline. Fourth and fifth readings appear when their start dates are configured. A failed calculation preserves the previous settings and dataset.

The server serves only the dashboard's static assets and explicit API routes. It does not expose profile `.env` files, raw databases, project files, or directory listings. Settings, token and sync actions require the page's local request token and same-origin requests. The ECNPlus credential is never returned to the browser. Access codes are checked by the server before it returns profile data or accepts changes. Signed HttpOnly cookies keep each space unlocked for up to 24 hours; restarting the local server also invalidates local sessions.

### Simple access codes

In local mode, copy `access-codes.example.json` to the private, ignored `access-codes.json` file and edit its three values. Without this file the defaults are **1111**, **2222**, and **3333** in profile order. Restart the local server after changing codes. Choose **Verrouiller mon espace** to close its session. Codes are no longer shipped in JavaScript.

In hosted mode, the three codes come from the Render `ACCESS_CODE_*` variables. HTTPS cookies and a stable server secret preserve sessions across restarts. See [DEPLOYMENT.md](DEPLOYMENT.md). The codes do not replace anyone's ECNPlus token.

You can also keep using the collector without the dashboard:

```sh
python3 -m ecnplus sync
python3 -m ecnplus status
```

On macOS and Linux, standalone collector credentials are stored in `.env` with owner-only permissions. On Windows, the standalone collector stores `.env` under the project folder and inherits that folder's access-control list. Dashboard credentials are stored separately inside each profile's local data folder. `.env`, collected data, and original evidence attachments are excluded by `.gitignore`. The standalone collector uses `ECNPLUS_AUTHORIZATION` before its project `.env`; the dashboard deliberately reads each profile's own credential file and ignores that environment variable, so one user's token cannot silently be used for another profile. Credentials are never written to response data or reports. Replace a dashboard credential in that profile's Settings when authorization expires or ECNPlus rejects it.

Synchronization discovers modules and courses, reads each course’s series catalogue and result references, and retrieves the attempt history of **every discovered series**, including unattempted ones. Result references and previously saved records also preserve series absent from the active catalogue. Series with missing sizes get an additional detail request. Requests use three workers by default, with a shared rate limit and bounded retries. Progress is printed throughout.

A new `sync` refreshes everything. Opening a report does not contact ECNPlus. The stored dataset must belong to the same account as the supplied credential; use a separate data directory for another account.

## Interrupted or incomplete synchronization

```sh
python3 -m ecnplus sync --resume
```

Every completed request is checkpointed. `--resume` retries failed and pending requests from the latest run if it is unfinished, while skipping its successful requests. It will not resume an older failed run after a newer run has completed. Pressing Ctrl+C saves completed work; a hard process stop also leaves checkpoints available for resumption. Use a regular `sync` for a fresh refresh after an old unfinished run.

Failures name the course/series and endpoint in the collection report. A partial synchronization publishes its own report but preserves the last complete dataset. If some requests fail, reported activity is provisional, not proof of zero activity. Exit status is 0 for successful collection, 2 for incomplete/interrupted collection, and 1 for configuration/authentication/startup errors. An authorization failure during collection exits 2 and can be resumed after replacing the token.

Optional controls:

```sh
python3 -m ecnplus sync --workers 2 --timeout 20 --retries 2
python3 -m ecnplus --data-dir /private/tmp/ecnplus-other-account sync
```

TLS verification stays enabled. The collector uses the normal trusted certificate store, adds `certifi` roots if that package is installed, and supports the standard `SSL_CERT_FILE` setting. On this Mac it also uses `/etc/ssl/cert.pem`. If another Python installation lacks trusted roots, repair its certificate configuration or install `certifi`; do not disable TLS verification.

## Saved files and reports

Run `python3 -m ecnplus status` to see the report folders. Each export is an immutable folder under `data/snapshots/` containing:

| File | Purpose |
|---|---|
| `report.md` | Readable course cards’ future totals: completed series, in-progress series, answered questions, each reading and overall |
| `courses.csv` | The same course summaries for Excel, with coverage and unknown-completion counts |
| `collection.md` / `collection.json` | Coverage, request failures, source limitations and issue counts |
| `review.md` / `review.csv` | Source anomalies and any remaining review items; the readable report includes Tunisia dates and links to each series overview |
| `issues.json` | Full diagnostic warnings and review items |
| `dataset.json` | Normalized catalogue and attempt records, including source run and request outcomes |
| `details.json` | Classified attempts and selected series/reading groups explaining each total |
| `summary.json` | Machine-readable course and overall summaries for the future dashboard |

`data/source.sqlite3` stores the raw JSON responses, timestamps, request contexts and run/checkpoint status. Request headers and profile details are not stored. It retains older observed attempts even when a newer history response omits them. Newer versions of the same attempt replace its earlier progress; repeated requests do not duplicate activity.

`data/latest.json` points to the most recently exported **complete collection**. `data/latest-attempt.json` points to the most recently exported collection or rebuild, including partial ones. A complete collection means the requested data was retrieved; unresolved date/count issues can still make statistical totals provisional. Read `phase_ready` in `collection.json` and the review report before accepting the dataset for the dashboard.

The source endpoint is named “recent attempts.” Neither an empty result nor a successful sync guarantees that the API exposes the account’s entire lifetime history. All returned history is kept, and reports state this limitation explicitly.

## Reading rules and offline recalculation

Edit `readings.json`, then run:

```sh
python3 -m ecnplus rebuild
```

This performs **no network requests** and needs no token. It recalculates the last complete collection, or the latest partial collection if no complete collection exists. To explicitly recalculate the latest partial export instead:

```sh
python3 -m ecnplus rebuild --latest-attempt
```

The five reading IDs stay `reading_1` through `reading_5`; names and start dates are editable. The first start is `null` and covers all earlier activity. Later starts are local dates in `Africa/Tunis`, ordered without gaps. A `null` fourth or fifth start means that reading is not configured yet. Initial boundaries are:

- Reading 1: through 31 August 2026 inclusive.
- Reading 2: 1 September through 14 October 2026 inclusive.
- Reading 3: from 15 October 2026 until a fourth reading is configured.

An attempt is assigned by its start timestamp converted to Tunisia time, including when its last update falls in another reading. These date crossings are informational and remain included. To explicitly assign an attempt to a different reading, add its attempt ID to `attempt_reading_overrides`, for example:

```json
"attempt_reading_overrides": {
  "REPLACE_WITH_REVIEWED_ATTEMPT_ID": "reading_2"
}
```

Then rebuild. Keep the other settings alongside this object. The report continues to identify the crossing and records the explicit resolution.

For each **series ID + reading**, retain the highest answered count across its attempts. Different series with the same university/year remain separate internally. A series repeated in different readings contributes separately to each reading and to the overall sum. University, year, scores, rankings and module statistics are not part of the main report.

Answered questions are the sum of an attempt’s valid nonnegative `correct`, `partiel` and `fausse` counts. Unfinished activity contributes its actual answered count. Legacy `questionsCount` fields are diagnostic only: observed responses include stale, zero and negative totals. A series size comes from catalogue `questionCount`, or detail `count` if needed.

A series with **more than 10 questions** is complete only when strictly more than 80% were answered. A series with **10 or fewer** requires at least its full size answered. Therefore 16/20 is incomplete, 17/20 is complete, 8/10 is incomplete, 10/10 is complete, and 83/87 counts as one completed series and **83** answered questions. If the platform records more answers than its current series size, retain **all actual answers** and count the series as completed: 104/100 contributes **104** questions. This source anomaly is informational. Zero-answer attempts contribute nothing. Missing sizes preserve trustworthy answered counts but leave completion unknown; invalid answer counts/dates are excluded and flagged.

## Acceptance and next handoff

Accept the collection after reviewing coverage for all 75 courses, failed requests, and `review.csv`. The first sync can take several minutes because it visits every series history. Subsequent syncs still refresh the complete discovered set; resumption exists for interrupted work.

The dashboard reads `latest.json` and its `summary.json` without calling ECNPlus on opening. Its Sync action invokes the existing collector in a background process; course details use `details.json`. Static assets live in `dist/`, the local HTTP/API adapter is `ecnplus/web.py`, and `python3 -m ecnplus serve` is the single launch command. No automated schedule is configured.

Verification uses temporary checks outside this project; no test files are added here. Original screenshots, HAR and workbook are evidence, not runtime inputs.
