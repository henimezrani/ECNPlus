"""Collector commands. Synchronization is explicit; reporting is offline."""

import argparse
import csv
import getpass
import json
import os
import signal
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .api import ApiError, AuthError, Client, authorization
from .storage import Store, identifier, lock, now, write_json
from .summary import integer, normalize, read_settings, summarize, timestamp

PROJECT = Path(__file__).resolve().parent.parent


def task(kind, path, **context):
    return {'kind': kind, 'path': path, 'context': context}


def validate(kind, body):
    if kind == 'detail':
        if not isinstance(body, dict) or not isinstance(body.get('_id'), str):
            raise ValueError('Expected a series detail object with an ID')
        return
    if not isinstance(body, list) or not all(isinstance(item, dict) for item in body):
        raise ValueError('Expected a JSON array of objects')
    items = body
    if kind == 'series':
        for group in body:
            if not isinstance(group.get('data'), list):
                raise ValueError('Expected university groups containing data arrays')
            if not all(isinstance(item, dict) for item in group['data']):
                raise ValueError('Expected series objects inside university groups')
            if integer(group.get('total')) and group['total'] > len(group['data']):
                raise ValueError('Catalogue is paginated/incomplete: group total exceeds returned rows')
        items = [item for group in body for item in group['data']]
    key = 'qcm' if kind == 'results' else '_id'
    if not all(isinstance(item, dict) and isinstance(item.get(key), str) and item[key] for item in items):
        raise ValueError('Missing stable record IDs in API response')
    if len({item[key] for item in items}) != len(items):
        raise ValueError('Duplicate record IDs in one API response')
    if kind in ('modules', 'courses') and not all(isinstance(i.get('name'), str) and i['name'] for i in items):
        raise ValueError('Missing catalogue names')


def fetch_batch(store, run_id, client, tasks, workers):
    # Only a resumed run skips completed requests. A new sync fetches everything.
    tasks = list({t['path']: t for t in tasks}.values())
    store.enqueue(run_id, tasks)
    pending = [t for t in tasks if store.response(run_id, t['path']) is None]
    print(f"  {len(tasks)} requests: {len(tasks) - len(pending)} saved, {len(pending)} to fetch", flush=True)

    def fetch(t):
        body = client.get(t['path'])
        try:
            validate(t['kind'], body)
        except ValueError as error:
            return body, str(error)
        return body, None

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(fetch, t): t for t in pending}
    last_progress = time.monotonic()
    try:
        for count, future in enumerate(as_completed(futures), 1):
            current = futures[future]
            try:
                body, error = future.result()
                store.save(run_id, current, body=body, error=error)
                if error:
                    print(f"  Invalid response: {current['path']} — {error}", flush=True)
            except AuthError as error:
                store.save(run_id, current, error=str(error), status_code=error.status)
                raise
            except ApiError as error:
                store.save(run_id, current, error=str(error), status_code=error.status)
                print(f"  Failed: {current['path']} — {error}", flush=True)
            if count % 25 == 0 or count == len(pending) or time.monotonic() - last_progress > 5:
                print(f"  {count}/{len(pending)} processed", flush=True)
                last_progress = time.monotonic()
    except BaseException:
        client.stopped.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def collect(store, run_id, client, workers):
    print('Discovering modules', flush=True)
    fetch_batch(store, run_id, client, [task('modules', 'speciality')], workers)
    modules = store.response(run_id, 'speciality')
    if modules is None:
        return
    print('Discovering courses', flush=True)
    fetch_batch(store, run_id, client, [task('courses',
        'subject/by-spec/' + quote(module['name'], safe='') + '?archived=false',
        module_id=module['_id'], module_name=module['name']) for module in modules], workers)
    courses = normalize(store, run_id)['courses']
    print(f"Discovering series and result references for {len(courses)} courses", flush=True)
    tasks = []
    for course in courses:
        context = {'course_id': course['id'], 'course_name': course['name']}
        tasks.append(task('series', 'qcm/by-university/' + quote(course['name'], safe='') + '?archived=false', **context))
        tasks.append(task('results', 'attempts/results-by-subject/' + quote(course['name'], safe=''), **context))
    fetch_batch(store, run_id, client, tasks, workers)
    discovered = normalize(store, run_id)
    course_names = {c['id']: c['name'] for c in discovered['courses']}
    details = [task('detail', 'qcm/' + quote(s['id'], safe=''), series_id=s['id'],
                    course_id=s['course_id'], course_name=course_names.get(s['course_id'], ''))
               for s in discovered['series'] if s['question_count'] is None]
    if details:
        print('Checking series with missing sizes or absent from the active catalogue', flush=True)
        fetch_batch(store, run_id, client, details, workers)
    discovered = normalize(store, run_id)
    print(f"Fetching attempt histories for all {len(discovered['series'])} discovered/retained series", flush=True)
    histories = [task('history', 'attempts/recent-attempts/' + quote(s['id'], safe=''),
                      series_id=s['id'], course_id=s['course_id'],
                      course_name=course_names.get(s['course_id'], '')) for s in discovered['series']]
    fetch_batch(store, run_id, client, histories, workers)


def md(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def build_report(store, run_id, settings):
    dataset = normalize(store, run_id)
    result = summarize(dataset, settings)
    collection = result['collection']
    lengths = Counter(len(r['body']) for r in store.responses(run_id)
                      if r['kind'] == 'history' and not r['error'])
    collection['returned_history_length_distribution'] = dict(sorted(lengths.items()))
    collection['generated_at'] = now()
    collection['run'] = dataset['run']
    expected_courses = sum(m.get('subjectCount', m.get('subjectsCount', 0)) for m in dataset['modules']
                           if integer(m.get('subjectCount', m.get('subjectsCount', 0))))
    if expected_courses != len(dataset['courses']):
        result['issues'].append({'code': 'catalogue_course_count_mismatch', 'severity': 'review',
                                 'expected': expected_courses, 'collected': len(dataset['courses'])})
        collection['issue_counts']['catalogue_course_count_mismatch'] = 1
    collection['catalogue_course_count_hint'] = expected_courses
    collection['expected_initial_courses'] = 75
    collection['phase_ready'] = (dataset['run']['status'] == 'complete' and
                                 len(dataset['courses']) >= 75 and
                                 not any(i['severity'] == 'review' for i in result['issues']))
    return dataset, result, collection


def export(store, run_id, settings, data_dir):
    dataset, result, collection = build_report(store, run_id, settings)
    snapshot = Path(data_dir) / 'snapshots' / identifier()
    snapshot.mkdir(parents=True, mode=0o700)
    write_json(snapshot / 'dataset.json', dataset)
    write_json(snapshot / 'summary.json', {k: v for k, v in result.items() if k not in ('issues', 'groups', 'attempts', 'collection')})
    write_json(snapshot / 'collection.json', collection)
    write_json(snapshot / 'issues.json', result['issues'])
    write_json(snapshot / 'details.json', {'groups': result['groups'], 'attempts': result['attempts']})
    names = {c['id']: c['name'] for c in dataset['courses']}
    with (snapshot / 'courses.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ['course', 'course_id', 'totals_status', 'available_series', 'available_questions', 'fresh_histories']
        fields += [r['id'] + '_' + key for r in settings['readings'] for key in result['total']]
        fields += ['total_' + key for key in result['total']]
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for course in result['courses']:
            row = {'course': course['name'], 'course_id': course['id'],
                   **{k: course[k] for k in fields[2:6]}}
            row.update({r + '_' + k: v for r, values in course['readings'].items() for k, v in values.items()})
            row.update({'total_' + k: v for k, v in course['total'].items()})
            writer.writerow(row)
    with (snapshot / 'review.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ['course', 'code', 'series_id', 'attempt_id', 'created_at', 'updated_at', 'answered_questions', 'details']
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for issue in result['issues']:
            if issue['severity'] != 'review' and issue['code'] not in ('boundary_crossing', 'answered_exceeds_current_size'):
                continue
            row = {key: issue.get(key, '') for key in fields}
            row['course'] = names.get(issue.get('course_id'), '')
            row['details'] = json.dumps(issue, ensure_ascii=False)
            writer.writerow(row)
    course_info = {c['id']: c for c in dataset['courses']}
    module_names = {m['_id']: m['name'] for m in dataset['modules']}
    reading_names = {r['id']: r['name'] for r in settings['readings']}

    def course_link(issue):
        course = course_info.get(issue.get('course_id'), {})
        label = md(course.get('name', 'Unknown course'))
        module = module_names.get(course.get('module_id'))
        if module and issue.get('series_id'):
            parts = [module, course['name'], issue['series_id']]
            url = 'https://app.ecnplus.tn/series/' + '/'.join(quote(part, safe='') for part in parts)
            return '[' + label + '](' + url + ')'
        return label

    def local_date(value):
        return timestamp(value).astimezone(ZoneInfo(settings['timezone'])).strftime('%d/%m/%Y %H:%M')

    review_lines = ['# Source anomalies and review notes', '',
        'Dates below use Tunisia time. Course links open the relevant ECNPlus series overview.', '',
        '## Attempts with dates in different readings', '',
        'These attempts are included using their start date. An explicit assignment in readings.json can override that reading if needed.', '',
        '| Course / series link | Attempt ID | Answered | Started | Last updated | Readings | Resolution |',
        '|---|---|---:|---|---|---|---|']
    for issue in result['issues']:
        if issue['code'] != 'boundary_crossing':
            continue
        span = md(reading_names[issue['start_reading']] + ' → ' + reading_names[issue['end_reading']])
        resolution = reading_names.get(issue.get('resolved_reading'), 'Pending review')
        review_lines.append(f"| {course_link(issue)} | {issue['attempt_id']} | {issue['answered_questions']} | "
                            f"{local_date(issue['created_at'])} | {local_date(issue['updated_at'])} | {span} | {md(resolution)} |")
    review_lines += ['', '## Answered count exceeds current series size', '',
        'The actual answered questions are included in full, and these series count as completed. The platform’s smaller current size does not cap the historical answered count.', '',
        '| Course / series link | Attempt ID | Answered | Current size |', '|---|---|---:|---:|']
    for issue in result['issues']:
        if issue['code'] == 'answered_exceeds_current_size':
            review_lines.append(f"| {course_link(issue)} | {issue['attempt_id']} | {issue['answered_questions']} | {issue['current_size']} |")
    others = [i for i in result['issues'] if i['severity'] == 'review'
              and i['code'] not in ('boundary_crossing', 'answered_exceeds_current_size')]
    if others:
        review_lines += ['', '## Other issues', '']
        review_lines += ['- ' + course_link(i) + ' — ' + md(json.dumps(i, ensure_ascii=False)) for i in others]
    (snapshot / 'review.md').write_text('\n'.join(review_lines) + '\n', encoding='utf-8')
    active = [r for i, r in enumerate(settings['readings']) if i == 0 or r['starts_on'] is not None]
    lines = ['# Study progress', '', f"Generated: {collection['generated_at']}",
             f"Source run: {run_id} — **{dataset['run']['status']}**", '',
             'Each reading cell is **completed series / in-progress series / answered questions**.',
             'Totals add the readings; a repeated series in another reading counts again.',
             'Source anomalies and any remaining review items are listed in review.md and details.json.', '',
             '| Course | ' + ' | '.join(md(r['name']) for r in active) + ' | Total | Status |',
             '|---|' + '---|' * (len(active) + 2)]

    def cell(values):
        return f"{values['completed_series']} / {values['in_progress_series']} / {values['answered_questions']}"

    for course in result['courses']:
        lines.append('| ' + md(course['name']) + ' | ' + ' | '.join(cell(course['readings'][r['id']]) for r in active)
                     + ' | ' + cell(course['total']) + ' | ' + course['totals_status'] + ' |')
    lines.extend(['| **All courses** | ' + ' | '.join(cell(result['readings'][r['id']]) for r in active)
                  + ' | ' + cell(result['total']) + ' | See collection report |', '',
                  'Unconfigured readings are omitted. Provisional totals contain known work only; missing requests are not evidence of no activity.'])
    (snapshot / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    collection_lines = ['# Collection verification', '', f"Run: {run_id} — **{dataset['run']['status']}**", '',
        f"Modules: {collection['modules']}; courses: {collection['courses']} (initial target 75; catalogue hint {expected_courses}); series: {collection['series']}; observed attempts: {collection['attempts']}.",
        f"Successful requests: {collection['requests_succeeded']}/{collection['requests_planned']}.",
        f"Previously observed attempts not returned this run, retained: {collection['retained_attempts_not_returned_this_run']}.",
        f"Returned history lengths (attempts per series → series count): {dict(lengths)}.", '',
        '**History limit:** ' + collection['history_completeness'],
        'A successful empty history means no attempts were returned; it does not prove there were never older attempts.', '',
        '## Issues', '', '| Issue | Count |', '|---|---:|']
    collection_lines += ['| ' + md(key) + ' | ' + str(value) + ' |' for key, value in sorted(collection['issue_counts'].items())]
    collection_lines += ['', 'Boundary crossings and invalid records are identified in review.md and review.csv. Legacy total mismatches are diagnostic warnings; actual answers are never inflated.',
                         '', '## Failed or pending requests', '']
    for failed in collection['failed_or_pending_requests']:
        collection_lines.append('- ' + md(failed['context'].get('course_name', failed['context'].get('module_name', 'Catalogue')))
                                + ' — ' + md(failed['path']) + ': ' + md(failed['error'] or 'Not completed'))
    if not collection['failed_or_pending_requests']:
        collection_lines.append('None.')
    (snapshot / 'collection.md').write_text('\n'.join(collection_lines) + '\n', encoding='utf-8')
    manifest = {'schema_version': 1, 'snapshot': str(snapshot.relative_to(data_dir)),
                'source_run_id': run_id, 'source_status': dataset['run']['status'],
                'source_started_at': dataset['run']['started_at'],
                'generated_at': collection['generated_at'], 'phase_ready': collection['phase_ready']}
    attempt_pointer = Path(data_dir) / 'latest-attempt.json'
    previous_attempt = json.loads(attempt_pointer.read_text(encoding='utf-8')) if attempt_pointer.exists() else None
    if (not previous_attempt or
            store.run(previous_attempt['source_run_id'])['started_at'] <= dataset['run']['started_at']):
        write_json(attempt_pointer, manifest)
    if dataset['run']['status'] == 'complete':
        write_json(Path(data_dir) / 'latest.json', manifest)
    return snapshot, result, collection


def print_result(snapshot, result, collection):
    total = result['total']
    print(f"\nSource status: {result['source_status']}")
    print(f"Courses: {collection['courses']}; series: {collection['series']}; observed attempts: {collection['attempts']}")
    print(f"Recorded totals: {total['completed_series']} completed series, {total['in_progress_series']} in progress, {total['answered_questions']} answered questions")
    print(f"Requests successful: {collection['requests_succeeded']}/{collection['requests_planned']}")
    review = sum(i['severity'] == 'review' for i in result['issues'])
    print(f"Records/issues requiring review: {review}. Totals with unresolved coverage or records are provisional.")
    print('Saved reports: ' + str(snapshot))


def sync(args, settings):
    data_dir = Path(args.data_dir).resolve()
    client = Client(authorization(args.credential_dir,
                                 allow_environment=not args.ignore_environment_token),
                    args.timeout, args.retries)
    with lock(data_dir):
        # Verify the account before mixing any observations into its local store.
        profile = client.get('auth/profile')
        if not isinstance(profile, dict) or not isinstance(profile.get('_id'), str):
            raise ValueError('Could not verify the account identity')
        store = Store(data_dir)
        try:
            store.check_account(profile['_id'])
            run_id = store.start(args.resume)
            status = 'partial'
            error_message = None
            try:
                collect(store, run_id, client, args.workers)
                tasks = store.tasks(run_id)
                if tasks and all(t['success'] for t in tasks):
                    status = 'complete'
            except AuthError as error:
                status, error_message = 'auth_failed', str(error)
            except KeyboardInterrupt:
                status, error_message = 'interrupted', 'Interrupted. Completed requests are saved; use sync --resume.'
            finally:
                store.finish(run_id, status)
            snapshot, result, collection = export(store, run_id, settings, data_dir)
            print_result(snapshot, result, collection)
            if error_message:
                print(error_message, file=sys.stderr)
            if status != 'complete':
                print('The last complete snapshot was preserved. Run sync --resume to retry missing requests.', file=sys.stderr)
            return 0 if status == 'complete' else 2
        finally:
            store.close()


def rebuild(args, settings):
    data_dir = Path(args.data_dir).resolve()
    with lock(data_dir):
        pointer = data_dir / ('latest-attempt.json' if args.latest_attempt else 'latest.json')
        if not pointer.exists() and not args.latest_attempt:
            pointer = data_dir / 'latest-attempt.json'
        if not (data_dir / 'source.sqlite3').exists():
            raise ValueError('No saved dataset. Run sync first.')
        store = Store(data_dir)
        try:
            if pointer.exists():
                run_id = json.loads(pointer.read_text(encoding='utf-8'))['source_run_id']
            else:
                # Recover when collection was checkpointed but export was interrupted.
                previous = store.db.execute(
                    "SELECT id FROM runs WHERE status='complete' ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
                latest = store.latest_run()
                if latest is None:
                    raise ValueError('No saved collection run. Run sync first.')
                run_id = previous[0] if previous and not args.latest_attempt else latest['id']
            snapshot, result, collection = export(store, run_id, settings, data_dir)
            print_result(snapshot, result, collection)
        finally:
            store.close()
    return 0


def configure():
    value = getpass.getpass('Paste the ECNPlus Authorization value (input is hidden): ').strip()
    if value.lower().startswith('bearer '):
        value = value[7:].strip()
    if not value or any(c.isspace() for c in value):
        raise ValueError('Paste only the token or the complete Bearer value')
    from .runtime import update_env
    update_env({'ECNPLUS_AUTHORIZATION': 'Bearer ' + value})
    print('Credential saved in local .env. It is not included in collected data or reports.')
    return 0


def status(args):
    data_dir = Path(args.data_dir).resolve()
    for label, name in [('Last complete dataset', 'latest.json'), ('Last exported attempt', 'latest-attempt.json')]:
        path = data_dir / name
        if not path.exists():
            print(label + ': none')
            continue
        manifest = json.loads(path.read_text(encoding='utf-8'))
        print(f"{label}: {manifest['source_status']} ({manifest['source_run_id']})")
        print('  Reports: ' + str(data_dir / manifest['snapshot']))
    database = data_dir / 'source.sqlite3'
    if database.exists():
        store = Store(data_dir)
        try:
            current = store.latest_run()
            if current:
                print(f"Last collection run: {current['status']} ({current['id']})")
        finally:
            store.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect and report local ECNPlus study data (GET requests only).')
    parser.add_argument('--data-dir', default=str(PROJECT / 'data'), help='Private local data directory')
    parser.add_argument('--settings', default=str(PROJECT / 'readings.json'), help='Editable reading boundaries and reviewed attempt overrides')
    parser.add_argument('--credential-dir', default=str(PROJECT), help='Directory containing the local .env credential file')
    parser.add_argument('--ignore-environment-token', action='store_true', help='Use only the selected local credential file')
    parser.add_argument('--profiles-dir', help='Local root directory for the dashboard profiles')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('configure', help='Save/replace the credential using a hidden prompt')
    sync_parser = commands.add_parser('sync', help='Refresh the complete catalogue and all available series histories')
    sync_parser.add_argument('--resume', action='store_true', help='Retry only failed/pending requests in the last unfinished run')
    sync_parser.add_argument('--workers', type=int, choices=range(1, 5), default=3)
    sync_parser.add_argument('--timeout', type=int, default=20)
    sync_parser.add_argument('--retries', type=int, choices=range(0, 6), default=2)
    rebuild_parser = commands.add_parser('rebuild', help='Recalculate saved data offline after editing readings.json')
    rebuild_parser.add_argument('--latest-attempt', action='store_true', help='Use the most recent partial export even if a complete dataset exists')
    commands.add_parser('status', help='Show saved dataset and collection status without network access')
    serve_parser = commands.add_parser('serve', help='Open the local study dashboard (no dependencies or build)')
    serve_parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    # Restrict new private data, SQLite checkpoints and exports to this OS user.
    os.umask(0o077)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == 'configure':
            return configure()
        if args.command == 'status':
            return status(args)
        if args.command == 'serve':
            if not 0 <= args.port <= 65535:
                raise ValueError('--port must be between 0 and 65535')
            from .web import serve
            return serve(args.data_dir, args.settings, args.port, args.profiles_dir)
        settings = read_settings(args.settings)
        if args.command == 'sync':
            if args.timeout < 1 or args.timeout > 120:
                raise ValueError('--timeout must be between 1 and 120 seconds')
            return sync(args, settings)
        return rebuild(args, settings)
    except (ApiError, ValueError, OSError, KeyError, TypeError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted. Saved requests can be resumed with sync --resume.', file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
