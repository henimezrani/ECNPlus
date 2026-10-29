"""Daily activity from classified attempts, without changing reading totals."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .summary import integer, timestamp


def study_day(value, timezone):
    return (value.astimezone(timezone) - timedelta(hours=5)).date()


def build_calendar(attempts, courses, settings, current=None):
    zone = ZoneInfo(settings['timezone'])
    current = (current or datetime.now(zone)).astimezone(zone)
    today = current.date()
    readings = [r for i, r in enumerate(settings['readings']) if i == 0 or r['starts_on']]
    reading_ids = {r['id'] for r in readings}
    course_names = {c['id']: c['name'] for c in courses}
    groups = {}
    for attempt in attempts:
        if (not attempt['included'] or attempt['reading_id'] not in reading_ids
                or attempt['course_id'] not in course_names
                or not integer(attempt['answered_questions'], 1)):
            continue
        day = study_day(timestamp(attempt['created_at']), zone).isoformat()
        key = (attempt['reading_id'], day, attempt['series_id'])
        previous = groups.get(key)
        if not previous or attempt['answered_questions'] > previous['answered_questions']:
            groups[key] = attempt

    activity = {r['id']: {} for r in readings}
    for (reading_id, day, _), attempt in groups.items():
        by_course = activity[reading_id].setdefault(day, {})
        course = by_course.setdefault(attempt['course_id'], {
            'id': attempt['course_id'], 'name': course_names[attempt['course_id']],
            'series': 0, 'questions': 0,
        })
        course['series'] += 1
        course['questions'] += attempt['answered_questions']

    result = []
    for i, reading in enumerate(readings):
        days = []
        for day, by_course in sorted(activity[reading['id']].items()):
            rows = sorted(by_course.values(), key=lambda c: c['name'].casefold())
            days.append({'date': day, 'courses': rows,
                         'series': sum(c['series'] for c in rows),
                         'questions': sum(c['questions'] for c in rows)})
        start = date.fromisoformat(reading['starts_on']) if reading['starts_on'] else None
        end = min(today, date.fromisoformat(readings[i + 1]['starts_on']) - timedelta(days=1)) if i + 1 < len(readings) else today
        if days:
            # Early starts on a reading's first day belong to the previous
            # calendar date, while retaining their original reading assignment.
            first, last = date.fromisoformat(days[0]['date']), date.fromisoformat(days[-1]['date'])
            start = min(start, first) if start else first
            end = max(end, last)
        result.append({**reading, 'range_start': start.isoformat() if start else None,
                       'range_end': end.isoformat(), 'days': days,
                       'totals': {'active_days': len(days), 'series': sum(d['series'] for d in days),
                                  'questions': sum(d['questions'] for d in days)}})
    return {'timezone': settings['timezone'], 'cutoff_hour': 5, 'today': today.isoformat(),
            'study_today': study_day(current, zone).isoformat(), 'readings': result}
