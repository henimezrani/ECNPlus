"""Pure reading rules and normalization. No network access."""

import json
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return result


def read_settings(path):
    with open(path, encoding="utf-8") as stream:
        settings = json.load(stream)
    return validate_settings(settings)


def validate_settings(settings):
    """Validate both the settings file and edits submitted by the local dashboard."""
    if not isinstance(settings, dict):
        raise ValueError('Reading settings must be a JSON object')
    if settings.get('timezone') != 'Africa/Tunis':
        raise ValueError("readings.json timezone must be Africa/Tunis")
    readings = settings.get('readings', [])
    if not isinstance(readings, list) or not all(isinstance(r, dict) for r in readings):
        raise ValueError('Readings must be an ordered list of objects')
    if [r.get('id') for r in readings] != ['reading_' + str(i) for i in range(1, 6)]:
        raise ValueError("Keep five ordered reading IDs: reading_1 through reading_5")
    previous = None
    gap = False
    for i, reading in enumerate(readings):
        if not isinstance(reading.get('name'), str) or not reading['name'].strip():
            raise ValueError("Every reading needs a name")
        start = reading.get('starts_on')
        if i == 0:
            if start is not None:
                raise ValueError("The first reading must have starts_on: null (all earlier activity)")
            continue
        if start is None:
            gap = True
            continue
        if gap:
            raise ValueError("Configure reading starts in order, without skipping a reading")
        parsed = date.fromisoformat(start)
        if parsed.isoformat() != start or previous and parsed <= previous:
            raise ValueError("Reading starts must be YYYY-MM-DD in strictly increasing order")
        previous = parsed
    active = {r['id'] for i, r in enumerate(readings) if i == 0 or r['starts_on'] is not None}
    overrides = settings.setdefault('attempt_reading_overrides', {})
    if not isinstance(overrides, dict) or any(not isinstance(k, str) or v not in active for k, v in overrides.items()):
        raise ValueError("Attempt overrides must map attempt IDs to configured reading IDs")
    return settings


def reading_for(value, settings):
    day = timestamp(value).astimezone(ZoneInfo(settings['timezone'])).date().isoformat()
    selected = settings['readings'][0]['id']
    for reading in settings['readings'][1:]:
        if reading['starts_on'] is not None and day >= reading['starts_on']:
            selected = reading['id']
    return selected


def is_complete(answered, size):
    if not integer(size, 1) or not integer(answered):
        return None
    # Integer arithmetic keeps the strict 80% boundary exact.
    # Historical answers can exceed the platform's current size; retain them all.
    return answered >= size if size <= 10 else answered * 5 > size * 4


def normalize(store, run_id):
    """Merge observed attempts by ID, retaining history omitted by later responses."""
    seed = store.seed(run_id) if hasattr(store, 'seed') else None
    modules = {m['_id']: m for m in seed['modules']} if seed else {}
    courses = {c['id']: c for c in seed['courses']} if seed else {}
    series = {s['id']: s for s in seed['series']} if seed else {}
    attempts = {a['id']: a for a in seed['attempts']} if seed else {}
    seen_paths = {}
    conflicts = set(seed['conflicting_series_ids']) if seed else set()
    target = store.run(run_id)
    for response in store.responses():
        if store.run(response['run_id'])['started_at'] > target['started_at']:
            continue
        if response['error'] or response['body'] is None:
            continue
        body, kind, context = response['body'], response['kind'], response['context']
        seen_paths[response['path']] = response['run_id']
        if kind == 'modules':
            modules = {item['_id']: item for item in body}
        elif kind == 'courses':
            for item in body:
                courses[item['_id']] = {
                    'id': item['_id'], 'name': item['name'], 'module_id': context['module_id'],
                    'series_count_hint': item.get('seriesCount'),
                    'questions_count_hint': item.get('seriesQuestionsCount'),
                    'catalogue_run': response['run_id'],
                }
        elif kind in ('series', 'detail'):
            items = [body] if kind == 'detail' else [item for group in body for item in group['data']]
            for item in items:
                item_id = item['_id']
                course_id = context['course_id']
                subject = item.get('subject')
                actual_course = subject.get('_id') if isinstance(subject, dict) else subject
                if actual_course and actual_course != course_id:
                    conflicts.add(item_id)
                previous = series.get(item_id)
                if previous and previous['course_id'] != course_id:
                    conflicts.add(item_id)
                size = item.get('count') if kind == 'detail' else item.get('questionCount')
                series[item_id] = {
                    'id': item_id, 'course_id': course_id,
                    'question_count': size if integer(size, 1) else None,
                    'legacy_question_count': item.get('questionsCount'),
                    'size_source': 'detail.count' if kind == 'detail' else 'catalogue.questionCount',
                    'catalogue_run': response['run_id'],
                }
        elif kind == 'results':
            # Results can reference a series omitted by the active catalogue.
            # Its detail request establishes the size; do not infer it from results.
            for item in body:
                series.setdefault(item['qcm'], {
                    'id': item['qcm'], 'course_id': context['course_id'],
                    'question_count': None, 'legacy_question_count': None,
                    'size_source': None, 'catalogue_run': response['run_id'],
                })
        elif kind == 'history':
            for item in body:
                attempt_id = item['_id']
                previous = attempts.get(attempt_id)
                if previous and previous['series_id'] != context['series_id']:
                    conflicts.add(context['series_id'])
                    conflicts.add(previous['series_id'])
                if previous:
                    try:
                        if timestamp(item.get('updatedAt')) < timestamp(previous.get('updated_at')):
                            continue
                    except (ValueError, TypeError):
                        pass
                attempts[attempt_id] = {
                    'id': attempt_id, 'series_id': context['series_id'],
                    'course_id': context['course_id'],
                    'correct': item.get('correct'), 'partial': item.get('partiel'),
                    'incorrect': item.get('fausse'),
                    'legacy_question_count': item.get('questionsCount'),
                    'created_at': item.get('createdAt'), 'updated_at': item.get('updatedAt'),
                    'last_seen_run': response['run_id'],
                }
    current_tasks = store.tasks(run_id)
    history_success = {t['context']['series_id'] for t in current_tasks if t['kind'] == 'history' and t['success']}
    return {
        'schema_version': 1, 'run': target,
        'modules': list(modules.values()), 'courses': list(courses.values()),
        'series': list(series.values()), 'attempts': list(attempts.values()),
        'tasks': current_tasks, 'conflicting_series_ids': sorted(conflicts),
        'fresh_history_series_ids': sorted(history_success),
        'retained_attempt_count': sum(a['last_seen_run'] != run_id for a in attempts.values()),
    }


def empty_totals():
    return {'completed_series': 0, 'in_progress_series': 0,
            'unknown_completion_series': 0, 'answered_questions': 0}


def summarize(dataset, settings):
    readings = settings['readings']
    courses = {c['id']: c for c in dataset['courses']}
    series = {s['id']: s for s in dataset['series']}
    conflicts = set(dataset['conflicting_series_ids'])
    issues, classified, groups = [], [], {}
    observed_overrides = set()

    def issue(code, severity='warning', **values):
        issues.append({'code': code, 'severity': severity, **values})

    for item in series.values():
        size = item['question_count']
        common = {'course_id': item['course_id'], 'series_id': item['id']}
        if size is None:
            issue('unknown_series_size', 'review', **common)
        if item.get('legacy_question_count') != size:
            issue('legacy_series_total_ignored', **common,
                  legacy_total=item.get('legacy_question_count'), trusted_size=size)

    for attempt in dataset['attempts']:
        record = {**attempt, 'answered_questions': None, 'reading_id': None,
                  'included': False, 'exclusion_reason': None}
        classified.append(record)
        common = {'course_id': attempt['course_id'], 'series_id': attempt['series_id'],
                  'attempt_id': attempt['id']}
        metrics = [attempt[k] for k in ('correct', 'partial', 'incorrect')]
        if not all(integer(v) for v in metrics):
            record['exclusion_reason'] = 'invalid_answer_counts'
            issue('invalid_answer_counts', 'review', **common)
            continue
        answered = sum(metrics)
        record['answered_questions'] = answered
        if answered == 0:
            record['exclusion_reason'] = 'no_answers'
            continue
        if attempt['series_id'] in conflicts or attempt['course_id'] not in courses:
            record['exclusion_reason'] = 'ambiguous_course'
            issue('ambiguous_course', 'review', **common)
            continue
        try:
            start, end = timestamp(attempt['created_at']), timestamp(attempt['updated_at'])
            if end < start:
                raise ValueError('End before start')
            first = reading_for(attempt['created_at'], settings)
            last = reading_for(attempt['updated_at'], settings)
        except (ValueError, TypeError):
            record['exclusion_reason'] = 'invalid_attempt_dates'
            issue('invalid_attempt_dates', 'review', **common,
                  created_at=attempt['created_at'], updated_at=attempt['updated_at'])
            continue
        override = settings['attempt_reading_overrides'].get(attempt['id'])
        if override:
            observed_overrides.add(attempt['id'])
        selected = override or first
        if first != last:
            issue('boundary_crossing', 'info', **common,
                  created_at=attempt['created_at'], updated_at=attempt['updated_at'],
                  start_reading=first, end_reading=last, answered_questions=answered,
                  resolved_reading=selected)
        record['reading_id'] = selected
        record['included'] = True
        size = series.get(attempt['series_id'], {}).get('question_count')
        if attempt.get('legacy_question_count') != size:
            issue('legacy_attempt_total_ignored', **common,
                  legacy_total=attempt.get('legacy_question_count'), trusted_size=size)
        complete = is_complete(answered, size)
        if integer(size, 1) and answered > size:
            issue('answered_exceeds_current_size', 'info', **common,
                  answered_questions=answered, current_size=size)
        key = (attempt['series_id'], selected)
        if key not in groups:
            groups[key] = {'course_id': attempt['course_id'], 'series_id': attempt['series_id'],
                           'reading_id': selected, 'answered_questions': answered,
                           'question_count': size, 'completed': complete,
                           'selected_attempt_id': attempt['id'], 'attempt_ids': [attempt['id']]}
        else:
            group = groups[key]
            group['attempt_ids'].append(attempt['id'])
            if answered > group['answered_questions']:
                group.update(answered_questions=answered, completed=complete,
                             selected_attempt_id=attempt['id'])
    for unused in settings['attempt_reading_overrides'].keys() - observed_overrides:
        issue('unused_attempt_override', attempt_id=unused)

    rows = []
    fresh = set(dataset['fresh_history_series_ids'])
    for course in sorted(courses.values(), key=lambda c: c['name'].casefold()):
        course_series = [s for s in series.values() if s['course_id'] == course['id']]
        counts = {r['id']: empty_totals() for r in readings}
        for group in groups.values():
            if group['course_id'] != course['id']:
                continue
            totals = counts[group['reading_id']]
            totals['answered_questions'] += group['answered_questions']
            field = ('completed_series' if group['completed'] is True else
                     'in_progress_series' if group['completed'] is False else 'unknown_completion_series')
            totals[field] += 1
        total = {key: sum(v[key] for v in counts.values()) for key in empty_totals()}
        fresh_count = sum(s['id'] in fresh for s in course_series)
        course_tasks = [t for t in dataset['tasks'] if t['context'].get('course_id') == course['id']]
        coverage_complete = (course['catalogue_run'] == dataset['run']['id'] and
                             fresh_count == len(course_series) and
                             all(t['success'] for t in course_tasks) and
                             any(t['kind'] == 'series' for t in course_tasks))
        needs_review = any(i['severity'] == 'review' and i.get('course_id') == course['id'] for i in issues)
        rows.append({**course, 'available_series': len(course_series),
                     'available_questions': (sum(s['question_count'] for s in course_series)
                                             if all(s['question_count'] is not None for s in course_series) else None),
                     'fresh_histories': fresh_count, 'coverage_complete': coverage_complete,
                     'needs_review': needs_review,
                     'totals_status': 'provisional' if needs_review or not coverage_complete else 'confirmed_for_available_history',
                     'readings': counts, 'total': total})
        hint = course.get('series_count_hint')
        if integer(hint) and hint != len(course_series):
            issue('catalogue_series_count_mismatch', course_id=course['id'],
                  catalogue_hint=hint, collected=len(course_series))
        question_hint = course.get('questions_count_hint')
        if integer(question_hint) and rows[-1]['available_questions'] != question_hint:
            issue('catalogue_question_count_mismatch', course_id=course['id'],
                  catalogue_hint=question_hint, collected=rows[-1]['available_questions'])
    aggregate = {r['id']: empty_totals() for r in readings}
    for row in rows:
        for reading_id, values in row['readings'].items():
            for key, value in values.items():
                aggregate[reading_id][key] += value
    failures = [t for t in dataset['tasks'] if not t['success']]
    return {
        'schema_version': 1, 'source_run_id': dataset['run']['id'], 'settings': settings,
        'source_status': dataset['run']['status'], 'courses': rows,
        'readings': aggregate,
        'total': {key: sum(v[key] for v in aggregate.values()) for key in empty_totals()},
        'issues': issues, 'groups': list(groups.values()), 'attempts': classified,
        'collection': {
            'modules': len(dataset['modules']), 'courses': len(courses), 'series': len(series),
            'attempts': len(dataset['attempts']), 'requests_planned': len(dataset['tasks']),
            'requests_succeeded': len(dataset['tasks']) - len(failures),
            'failed_or_pending_requests': failures,
            'retained_attempts_not_returned_this_run': dataset['retained_attempt_count'],
            'issue_counts': dict(Counter(i['code'] for i in issues)),
            'history_completeness': 'All returned history is retained. The recent-attempts endpoint does not guarantee lifetime completeness.',
        },
    }
