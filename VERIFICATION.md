# Technical verification

The collector and dashboard use the existing counting rules: Tunisia-local reading boundaries, attempt start-date assignment, the highest answered count per series within a reading, and full retention of answered counts above a series’ current size. The platform’s recent-attempts endpoint does not guarantee lifetime completeness.

Earlier local checks covered date and completion boundaries, duplicate attempts, SQLite checkpoint/resume, account mismatch, concurrent-process exclusion, partial snapshot preservation, offline recalculation, fixed-origin read-only requests, static-file restrictions and the dashboard’s search, course details and reading filters. These checks used temporary fixtures and locally collected data. Personal study results are not included in this record.

The local dashboard uses the Python standard library and buildless HTML/CSS/JavaScript. Windows additionally requires `tzdata`. Local profile histories remain separate from hosted Neon histories. No tests were added to the repository.

## Render + Neon verification — 29 October 2026

Implemented a Flask/Gunicorn hosted adapter, a versioned Postgres schema, profile-scoped collector storage, encrypted ECNPlus token storage, server-side access-code sessions, a Render Free Blueprint and French deployment instructions. The stdlib local server remains available without the hosted packages. Source credentials and study databases are not deployment artifacts.

Ad-hoc scripts outside this repository verified the following against a disposable branch of the actual Neon project:

- Running the versioned migration twice is idempotent. Importing local SQLite history reproduces every course's totals, every reading total and the observed attempt count exactly. A second import refuses to overwrite a populated profile.
- Unauthenticated profile APIs return 401. A session for one profile cannot read another profile. Hosted cookies use Secure, HttpOnly, SameSite=Strict and profile-scoped paths. Logout closes access. Wrong codes, wrong origin and wrong request tokens are rejected. Private files are not served.
- Stored ECNPlus tokens are encrypted and are not returned by dashboard responses. Access codes are absent from the shipped JavaScript.
- Concurrent writes to one profile are refused while another profile remains usable. An expired job lease cannot be recovered while its collector still owns the database lock; recovery succeeds once that connection closes. A mismatched ECNPlus account is rejected.
- The actual collector and worker, with deterministic HTTP fixtures, produce a partial checkpoint after a failed history request. Resume fetches only that missing request and account verification. A later complete run retains an older attempt omitted by the new response, even after pruning the older raw run. Start dates and over-size answered counts retain their existing semantics.
- Reading changes rebuild stored data without contacting ECNPlus. State remains available after creating a new app/database object.

Additional local checks passed: Python and JavaScript syntax, installed dependency consistency, stdlib HTTP access/profile isolation/logout, and updating the standalone token without deleting unrelated `.env` settings. The actual Gunicorn WSGI entrypoint started with Render-style environment variables and served its health check and profile HTML. Browser checks with an isolated empty local profile confirmed wrong-code feedback, successful opening, session persistence on reload and locking the space. No test files were added to the repository.

These are implementation and self-verification results, not an independent review or a Render production acceptance. A real hosted ECNPlus refresh and iPad/Safari acceptance remain to be performed after the Render service is created. The deployment guide documents free-service sleep and manual checkpoint resumption.

## Live deployment verification — 29 October 2026

Render deploy `dep-dbhra5jl550s73dvkn90` reached `live` from application commit `c5239ea7253242636782fd8ff687cdb92f1c5c2b`. The service is on the Free plan in Frankfurt, with the prepared private environment settings and the default TCP readiness check. The public HTTP `/healthz` endpoint also returns success.

Live HTTPS checks passed for all three profiles: unauthorized API reads are refused; the configured code establishes a secure profile session; another profile remains inaccessible; logout closes access. The deployed imported history matches the local course summaries and reading totals exactly. The other profiles return their expected empty state. A live course-detail response was also verified. Browser verification loaded the course cards with no reported console warnings or errors. No tokens or reading settings were changed during these checks.

An actual new ECNPlus collection from Render and physical iPad/Safari acceptance remain user checks after supplying a current token. Deployment used the Render deploy skill and connected Render tools; the checked-in Blueprint is a reference rather than an active Blueprint-managed resource.
