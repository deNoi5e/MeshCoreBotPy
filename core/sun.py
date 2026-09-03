"""
Восход и заход Солнца на сегодня.

Как и core/moon.py, источник данных — не внешний API, а прямой
астрономический расчёт по алгоритмам Жана Мёуса («Astronomical
Algorithms», 2-е изд.): гл. 25 (положение Солнца, низкая точность,
~0.01°) и гл. 12/13 (звёздное время, горизонтальные координаты).

Тот же мотив, что и у Луны: восход/заход Солнца — детерминированная
эфемерида, узел mesh-сети должен отвечать на неё и без интернета.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .moon import OMSK_LAT, OMSK_LON, _DELTA_T, _equatorial, _gmst, _jd, _jd_to_datetime, _sun_apparent

# Видимый радиус диска Солнца (~16') + атмосферная рефракция у горизонта (34').
_SUN_H0 = -0.8333


def _sun_altitude(jd_ut: float, lat: float, lon: float) -> float:
    """Высота центра Солнца над горизонтом (град)."""
    t = (jd_ut + _DELTA_T / 86400.0 - 2451545.0) / 36525.0
    lam, _dist = _sun_apparent(t)
    ra, dec = _equatorial(lam, 0.0, t)
    h = math.radians(_gmst(jd_ut) + lon - ra)
    lat_r, dec_r = math.radians(lat), math.radians(dec)
    return math.degrees(math.asin(
        math.sin(lat_r) * math.sin(dec_r)
        + math.cos(lat_r) * math.cos(dec_r) * math.cos(h)))


def _rise_set(day_start_utc: datetime, lat: float, lon: float,
              tz_offset: float) -> tuple[datetime | None, datetime | None]:
    """Восход и заход Солнца в пределах местных суток, начинающихся в day_start_utc.

    За полярным кругом в конкретный день восхода либо захода может не
    быть (полярный день/ночь) — тогда возвращается None.
    """
    jd0 = _jd(day_start_utc)
    step = 10.0 / 1440.0  # 10 минут
    steps = int(round(1.0 / step))
    rise = setting = None

    prev = None
    for i in range(steps + 1):
        jd = jd0 + i * step
        cur = _sun_altitude(jd, lat, lon) - _SUN_H0
        if prev is not None and prev[1] * cur < 0:
            frac = prev[1] / (prev[1] - cur)
            moment = _jd_to_datetime(prev[0] + frac * step)
            if cur > 0 and rise is None:
                rise = moment
            elif cur < 0 and setting is None:
                setting = moment
        prev = (jd, cur)

    tz = timezone(timedelta(hours=tz_offset))
    return (rise.astimezone(tz) if rise else None,
            setting.astimezone(tz) if setting else None)


@dataclass
class SunInfo:
    rise: datetime | None
    set: datetime | None
    day_length: timedelta | None


def get_sun_info(when: datetime | None = None, *,
                 lat: float = OMSK_LAT, lon: float = OMSK_LON,
                 tz_offset: float = 6.0) -> SunInfo:
    now = when or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    tz = timezone(timedelta(hours=tz_offset))
    local_midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0,
                                                 microsecond=0)
    rise, setting = _rise_set(local_midnight.astimezone(timezone.utc),
                              lat, lon, tz_offset)

    day_length = (setting - rise) if (rise and setting) else None
    return SunInfo(rise=rise, set=setting, day_length=day_length)


def format_sun(info: SunInfo) -> str:
    rise = info.rise.strftime("%H:%M") if info.rise else "—"
    setting = info.set.strftime("%H:%M") if info.set else "—"
    if info.day_length is not None:
        total_min = int(info.day_length.total_seconds() // 60)
        length = f"{total_min // 60}ч{total_min % 60:02d}м"
    else:
        length = "—"
    return f"☀ Восход {rise}, заход {setting}\nДень {length}"


async def get_sun(lat: float = OMSK_LAT, lon: float = OMSK_LON,
                  tz_offset: float = 6.0) -> str:
    """Готовый текст для команды /sun.

    Корутина ради единообразия с остальными источниками данных — сети здесь
    нет, расчёт занимает единицы миллисекунд и не блокирует цикл событий.
    """
    return format_sun(get_sun_info(lat=lat, lon=lon, tz_offset=tz_offset))
