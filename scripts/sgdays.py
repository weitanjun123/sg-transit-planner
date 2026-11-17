"""Singapore public holidays (MOM gazetted, incl. Monday-in-lieu) and per-month weekday / weekend-or-holiday day counts.
LTA's passenger-volume files put public holidays under "WEEKENDS/HOLIDAY", so per-day averages must do the same.
Sources: MOM public holidays 2026 and 2027 (press release 18 Jun 2026). Add each new year when MOM publishes it.
"""
import datetime as dt

PUBLIC_HOLIDAYS = {
    # 2026
    "2026-01-01", "2026-02-17", "2026-02-18", "2026-03-21", "2026-04-03", "2026-05-01", "2026-05-27",
    "2026-05-31", "2026-06-01", "2026-08-09", "2026-08-10", "2026-11-08", "2026-11-09", "2026-12-25",
    # 2027
    "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-03-10", "2027-03-26", "2027-05-01",
    "2027-05-17", "2027-05-20", "2027-08-09", "2027-10-28", "2027-12-25",
}
KNOWN_YEARS = {2026, 2027}


def day_counts(ym):
    """ym 'YYYYMM' or 'YYYY-MM' -> {'weekday': n, 'weekend': n, 'holidays_known': bool}"""
    ym = ym.replace("-", "")
    y, m = int(ym[:4]), int(ym[4:])
    d, wk, we = dt.date(y, m, 1), 0, 0
    while d.month == m:
        if d.weekday() >= 5 or d.isoformat() in PUBLIC_HOLIDAYS:
            we += 1
        else:
            wk += 1
        d += dt.timedelta(days=1)
    return {"weekday": wk, "weekend": we, "holidays_known": y in KNOWN_YEARS}
