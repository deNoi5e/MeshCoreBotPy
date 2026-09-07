"""
Генератор таблицы ретрограда Меркурия для core/mercury.py.

Разовый скрипт: тянет эфемериды Меркурия из JPL Horizons и печатает готовый
Python-литерал с интервалами ретроградного движения, который вставляется
в core/mercury.py. Сам бот в сеть за этим не ходит — см. docstring модуля
core/mercury.py о том, почему выбрана таблица, а не запрос по требованию.

Как определяется ретрограда: это чисто кинематическое свойство —
геоцентрическая эклиптическая долгота планеты убывает (планета «идёт назад»
по зодиаку). Значит нужен знак dλ/dt, а моменты станций — его нули.

Алгоритм: на каждый год берётся λ(t) с шагом 1 час (QUANTITIES='31',
центр '500@399' — геоцентр). Из центральных разностей считается скорость,
ищутся смены её знака, и нуль уточняется линейной интерполяцией между
двумя часовыми отсчётами. Вблизи станции скорость почти линейна по времени,
поэтому такая интерполяция даёт точность порядка секунд — на порядки лучше,
чем нужно (в выводе бота фигурируют минуты).

Запуск:
    python scripts/gen_mercury_table.py 2026 2040
"""

from __future__ import annotations

import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import certifi

_API = "https://ssd.jpl.nasa.gov/api/horizons.api"

# Horizons отдаёт дату как "2026-Sep-02 00:00"; strptime("%b") зависит от
# локали процесса, поэтому месяцы разбираются своим словарём.
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}

_ROW = re.compile(
    r"^\s*(\d{4})-(\w{3})-(\d{2})\s+(\d{2}):(\d{2})\s+([\d.]+)\s+([-\d.]+)\s*$")

# Меркурий как наблюдаемое тело; '500@399' — геоцентр Земли.
_TARGET = "199"
_CENTER = "500@399"


def _fetch(start: datetime, stop: datetime, step: str) -> str:
    params = {
        "format": "text",
        "COMMAND": f"'{_TARGET}'",
        "OBJ_DATA": "'NO'",
        "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": f"'{_CENTER}'",
        "START_TIME": f"'{start:%Y-%m-%d %H:%M}'",
        "STOP_TIME": f"'{stop:%Y-%m-%d %H:%M}'",
        "STEP_SIZE": f"'{step}'",
        "QUANTITIES": "'31'",          # ObsEcLon / ObsEcLat
    }
    url = f"{_API}?{urllib.parse.urlencode(params)}"
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=120, context=ssl_ctx) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Horizons код {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


def _parse(text: str) -> list[tuple[datetime, float]]:
    """Строки между $$SOE/$$EOE -> [(момент UTC, эклиптическая долгота)]."""
    rows: list[tuple[datetime, float]] = []
    inside = False
    for line in text.splitlines():
        if "$$SOE" in line:
            inside = True
            continue
        if "$$EOE" in line:
            break
        if not inside:
            continue
        m = _ROW.match(line)
        if not m:
            continue
        year, mon, day, hour, minute, lon, _lat = m.groups()
        when = datetime(int(year), _MONTHS[mon], int(day), int(hour),
                        int(minute), tzinfo=timezone.utc)
        rows.append((when, float(lon)))
    if not rows:
        raise RuntimeError("Horizons не вернул эфемериды (см. ответ API)")
    return rows


def _delta_lon(a: float, b: float) -> float:
    """Приращение долготы с раскруткой через 360°."""
    d = b - a
    if d > 180.0:
        d -= 360.0
    elif d < -180.0:
        d += 360.0
    return d


def _stations(rows: list[tuple[datetime, float]]) -> list[tuple[datetime, bool]]:
    """Моменты станций: (когда, началась ли ретрограда).

    Скорость берётся центральной разностью и относится к середине часового
    интервала; нуль скорости уточняется линейной интерполяцией.
    """
    speeds: list[tuple[datetime, float]] = []
    for i in range(1, len(rows)):
        (t0, l0), (t1, l1) = rows[i - 1], rows[i]
        dt = (t1 - t0).total_seconds()
        speeds.append((t0 + (t1 - t0) / 2, _delta_lon(l0, l1) / dt))

    found: list[tuple[datetime, bool]] = []
    for i in range(1, len(speeds)):
        (t0, v0), (t1, v1) = speeds[i - 1], speeds[i]
        if v0 == 0.0 or (v0 > 0) == (v1 > 0):
            continue
        frac = v0 / (v0 - v1)
        moment = t0 + (t1 - t0) * frac
        found.append((moment.replace(microsecond=0), v1 < 0))
    return found


def collect(year_from: int, year_to: int) -> list[tuple[datetime, bool]]:
    """Станции Меркурия за годы [year_from, year_to] включительно."""
    stations: list[tuple[datetime, bool]] = []
    for year in range(year_from, year_to + 1):
        # Год с нахлёстом в сутки по краям: скорость считается разностями,
        # без нахлёста станция у самой границы года потерялась бы.
        start = datetime(year, 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
        stop = datetime(year + 1, 1, 1, tzinfo=timezone.utc) + timedelta(days=1)
        print(f"  {year}...", file=sys.stderr, flush=True)
        rows = _parse(_fetch(start, stop, "1 h"))
        for moment, retro in _stations(rows):
            # Из-за нахлёста одна и та же станция может прийти дважды.
            if stations and abs((moment - stations[-1][0]).total_seconds()) < 3600:
                continue
            stations.append((moment, retro))
        time.sleep(1.0)     # не частим с публичным API JPL
    return stations


def to_intervals(stations: list[tuple[datetime, bool]]) -> list[tuple[datetime, datetime]]:
    """Станции -> замкнутые интервалы ретрограды (начало, конец).

    Незамкнутые края (ретрограда, начавшаяся до сканируемого периода или
    не кончившаяся внутри него) отбрасываются: половинчатый интервал сделал
    бы таблицу неверной именно на границе, где ей и так нельзя доверять.
    """
    intervals: list[tuple[datetime, datetime]] = []
    start: datetime | None = None
    for moment, retro in stations:
        if retro:
            start = moment
        elif start is not None:
            intervals.append((start, moment))
            start = None
    return intervals


def render(intervals: list[tuple[datetime, datetime]], year_from: int) -> str:
    """Готовый к вставке в core/mercury.py фрагмент — обе константы сразу.

    _TABLE_FROM печатается вместе с интервалами не для красоты: модуль по нему
    решает, где начинается его осведомлённость, и разъехавшись с таблицей
    он начал бы отвечать на даты, которых не сканировали.
    """
    lines = [f'_TABLE_FROM = "{year_from}-01-01"',
             "",
             "# Сгенерировано scripts/gen_mercury_table.py "
             f"({datetime.now(timezone.utc):%Y-%m-%d}), источник — JPL Horizons.",
             "# Моменты станций в UTC, с точностью до минуты.",
             "_RETRO_INTERVALS: tuple[tuple[str, str], ...] = ("]
    for begin, end in intervals:
        lines.append(f'    ("{begin:%Y-%m-%dT%H:%M}", "{end:%Y-%m-%dT%H:%M}"),')
    lines.append(")")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    year_from, year_to = int(sys.argv[1]), int(sys.argv[2])
    print(f"Запрос эфемерид Меркурия {year_from}-{year_to} у JPL Horizons:",
          file=sys.stderr)
    intervals = to_intervals(collect(year_from, year_to))
    print(f"Найдено интервалов ретрограды: {len(intervals)}", file=sys.stderr)
    print(render(intervals, year_from))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
