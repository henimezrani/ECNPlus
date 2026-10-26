# Part 1 verification — 26 October 2026

## Implemented

- Python 3.10+ local CLI: credential configuration, full synchronization, checkpoint/resume, offline recalculation and saved-status inspection.
- Raw API responses in SQLite; immutable normalized/export snapshots with complete and latest-attempt pointers.
- Reading rules, per-series/per-reading maximum answered count, strict completion thresholds, start-date assignment and explicit attempt overrides.
- JSON, CSV and readable reports. Credentials and personal source evidence are excluded by `.gitignore`.

## Live collection evidence

The initial collection and its one-request retry completed with **1,857/1,857 successful requests**, **75 courses**, **1,690 series**, and **634 returned attempts**. The first pass received HTTP 429 for one history; resumption fetched only that missing response. An export failure from the earlier CSV field mapping was corrected; checkpointed source data was retained and the reports were regenerated without a full re-fetch.

The current report snapshot is resolved through `data/latest.json`. Offline regeneration has the same source run and totals:

| Reading | Completed series | In progress | Completion unknown | Answered questions |
|---|---:|---:|---:|---:|
| First | 311 | 30 | 0 | 12,100 |
| Second | 205 | 24 | 0 | 7,919 |
| Third | 24 | 10 | 0 | 1,069 |
| Total | 540 | 64 | 0 | 21,088 |

The user's updated rules are applied automatically: thirteen attempts with dates in different readings use their start reading, and eleven attempts with more answers than the current size retain all actual answers and count as completed. These source anomalies are informational; there are **zero outstanding review cases**, no unknown-completion groups, and `phase_ready` is true. The readable anomaly report includes local dates, attempt IDs and links to the relevant ECNPlus overview pages. No per-attempt overrides were needed.

The API returned histories of lengths 0, 1, 2 and 3. A complete collection means every planned request succeeded, not that ECNPlus guarantees full lifetime history. Previously observed attempts are retained on future synchronizations even when omitted by a later response.

## Verification method

Luna Medium ran temporary synthetic checks outside the project. No test files were added to this repository/workspace. Checks cover:

- Tunisia date boundaries and local midnight; unset later readings; offline boundary changes and explicit overrides.
- 16/20 vs 17/20; 8/10 vs 10/10; 83/87 preserving 83 actual answers; small series.
- Same-reading maximum (30 vs 12); cross-reading repeats (47 + 47); zero/empty histories; invalid counts and dates.
- Checkpoint retry/resume, attempt retention after newer empty responses, and JSON/CSV/Markdown export structure.
- Preservation of the last complete pointer after partial/interrupted/auth-failed runs; offline recovery after missing export manifests.
- Profile authentication failure, mid-run authentication failure and resumption, account mismatch, concurrent-process locking.
- Fixed-origin GET requests, authorization headers, rejected unsafe paths and blocked redirects, using mocked HTTP calls.
- Chronology: an old unfinished run cannot supersede a newer complete run; rebuilding an older complete snapshot does not rewind the latest-attempt pointer.

Luna also checked the saved live artifacts: all **604 selected series/reading groups** match the maximum included attempt count, with no duplicate assignment. The 47-answer example contributes two completed groups and 94 answers across readings 1 and 3. All thirteen date-crossing cases use their start reading; all eleven over-size cases retain their actual answers and count as completed. CSV row counts, review links, Tunisia-local dates and final Python syntax passed. New temporary checks verified cross-reading start-date assignment, explicit overrides, 104/100 and 3/2 completion with full counts, and unchanged normal thresholds and invalid-data safeguards. The updated reports were rebuilt once offline, without API calls. Earlier privacy verification confirmed credentials/profile fields absent from saved responses/exports and private file permissions.

This is implementation and verification evidence. It is not a separate product acceptance review.

## Local dashboard verification

Implemented the dashboard with Python's built-in HTTP server and buildless HTML/CSS/JavaScript. Launch with `python3 -m ecnplus serve`, then open http://127.0.0.1:8765. The existing collector and counting rules remain the source of the displayed totals.

Luna Medium verified these paths using temporary copies/fixtures, without changing the real settings or making new ECNPlus requests:

- Python syntax and JavaScript syntax; saved overview totals and all 75 course-detail responses (634 attempts total).
- Static-file allowlisting and traversal rejection; rejected invalid Host, missing local request token, malformed/oversized requests and unknown actions.
- Offline settings recalculation, rejection of invalid dates without changing settings, and duplicate-job exclusion.
- Local credential storage at mode 0600 without returning the token to the client.
- Background sync progress/success and expired-token failure handling with a fake collector process; the previous complete-data pointer remained intact after the simulated failure.
- Empty-data and incomplete-checkpoint responses.

Browser checks through CUA confirmed all 75 cards, all reading/overall totals, accent-insensitive search, filters, sorting, no-results recovery, course details and reading tabs, settings dates, an empty masked token field, and modal dismissal. Desktop (1280×720) and mobile (390×844) views had no horizontal overflow; no console errors were reported. The browser checks did not start a new live synchronization or submit a real credential/settings edit. Sync integration was checked with simulated subprocess output; the collector's initial live retrieval is documented above.

## Resumable handoff

1. Inspect `data/latest.json`, then the referenced `report.md`, `collection.md` and `review.md`.
2. Reading-boundary cases use their start date automatically. Edit `readings.json` only when changing boundaries or assigning an explicit exception, then recalculate with `python3 -m ecnplus rebuild`.
3. Counts above the current series size retain all actual answers and count as completed; these source anomalies are informational.
4. Launch `python3 -m ecnplus serve` for the dashboard. It reads the selected saved snapshot; Sync refreshes explicitly and Settings recalculates locally. The HTTP adapter is `ecnplus/web.py`; buildless assets are in `dist/`.
