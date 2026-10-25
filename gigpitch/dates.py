from datetime import date, timedelta


def add_working_days(start: date, days: int) -> date:
    """Return the date `days` working days (Mon-Fri) after `start`."""
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d
