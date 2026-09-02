"""
Фаза Луны, её возраст, восход/заход и ближайшие новолуние/полнолуние.

Источник данных — не внешний API, а прямой астрономический расчёт по
алгоритмам Жана Мёуса («Astronomical Algorithms», 2-е изд.):

  * гл. 47 — положение Луны (усечённая теория ELP-2000/82, ряды 47.A/47.B),
    точность ~10" по долготе и ~4" по широте — многократно избыточно;
  * гл. 25 — положение Солнца (низкая точность, ~0.01°);
  * гл. 48 — освещённая доля диска;
  * гл. 49 — моменты новолуний и полнолуний (точность ~секунды);
  * гл. 12/13 — звёздное время и горизонтальные координаты для восхода/захода.

Почему расчёт, а не запрос к сервису: движение Луны — детерминированная
эфемерида, её незачем спрашивать по сети. Нет ключей, лимитов запросов,
скрапинга вёрстки и зависимости от чужого аптайма — модуль работает и при
оборванном интернете, что для узла в mesh-сети существенно.

Единственная нестрогость — ΔT (разница динамического и всемирного времени),
она задана константой; на масштабе отображаемых минут это несущественно.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Омск — узел бота стоит там же, куда смотрят /weather и /traffic.
OMSK_LAT, OMSK_LON = 54.9914, 73.3645

# ΔT = TT - UT на текущую эпоху, секунды (в 2020-х ≈ 69 c и почти не меняется).
_DELTA_T = 69.0

# Ряды 47.A: (D, M, M', F) -> коэффициенты долготы (1e-6 град) и
# расстояния (1e-3 км).
_TERMS_LR = (
    (0, 0, 1, 0, 6288774, -20905355), (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968), (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888), (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158), (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733), (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620), (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755), (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0), (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782), (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636), (2, 1, -1, 0, -7888, 24208),
    (2, 1, 0, 0, -6766, 30824), (1, 0, -1, 0, -5163, -8379),
    (1, 1, 0, 0, 4987, -16675), (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445), (4, 0, 0, 0, 3861, -11650),
    (2, 0, -3, 0, 3665, 14403), (0, 1, -2, 0, -2689, -7003),
    (2, 0, -1, 2, -2602, 0), (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322), (2, -2, 0, 0, 2236, -9884),
    (0, 1, 2, 0, -2120, 5751), (0, 2, 0, 0, -2069, 0),
    (2, -2, -1, 0, 2048, -4950), (2, 0, 1, -2, -1773, 4130),
    (2, 0, 0, 2, -1595, 0), (4, -1, -1, 0, 1215, -3958),
    (0, 0, 2, 2, -1110, 0), (3, 0, -1, 0, -892, 3258),
    (2, 1, 1, 0, -810, 2616), (4, -1, -2, 0, 759, -1897),
    (0, 2, -1, 0, -713, -2117), (2, 2, -1, 0, -700, 2354),
    (2, 1, -2, 0, 691, 0), (2, -1, 0, -2, 596, 0),
    (4, 0, 1, 0, 549, -1423), (0, 0, 4, 0, 537, -1117),
    (4, -1, 0, 0, 520, -1571), (1, 0, -2, 0, -487, -1739),
    (2, 1, 0, -2, -399, 0), (0, 0, 2, -2, -381, -4421),
    (1, 1, 1, 0, 351, 0), (3, 0, -2, 0, -340, 0),
    (4, 0, -3, 0, 330, 0), (2, -1, 2, 0, 327, 0),
    (0, 2, 1, 0, -323, 1165), (1, 1, -1, 0, 299, 0),
    (2, 0, 3, 0, 294, 0), (2, 0, -1, -2, 0, 8752),
)

# Ряды 47.B: (D, M, M', F) -> коэффициент широты (1e-6 град).
_TERMS_B = (
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237), (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198), (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463),
    (2, -1, 0, 1, 2211), (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870),
    (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794), (0, 0, 0, 3, -1749),
    (0, 1, -1, 1, -1565), (1, 0, 0, 1, -1491), (0, 1, 1, 1, -1475),
    (0, 1, 1, -1, -1410), (0, 1, 0, -1, -1344), (1, 0, 0, -1, -1335),
    (0, 0, 3, 1, 1107), (4, 0, 0, -1, 1021), (4, 0, -1, 1, 833),
    (0, 0, 1, -3, 777), (4, 0, -2, 1, 671), (2, 0, 0, -3, 607),
    (2, 0, 2, -1, 596), (2, -1, 1, -1, 491), (2, 0, -2, 1, -451),
    (0, 0, 3, -1, 439), (2, 0, 2, 1, 422), (2, 0, -3, -1, 421),
    (2, 1, -1, 1, -366), (2, 1, 0, 1, -351), (4, 0, 0, 1, 331),
    (2, -1, 1, 1, 315), (2, -2, 0, -1, 302), (0, 0, 1, 3, -283),
    (2, 1, 1, -1, -229), (1, 1, 0, -1, 223), (1, 1, 0, 1, 223),
    (0, 1, -2, -1, -220), (2, 1, -1, -1, -220), (1, 0, 1, 1, -185),
    (2, -1, -2, -1, 181), (0, 1, 2, 1, -177), (4, 0, -2, -1, 176),
    (4, -1, -1, -1, 166), (1, 0, 1, -1, -164), (4, 0, 1, -1, 132),
    (1, 0, -1, -1, -119), (4, -1, 0, -1, 115), (2, -2, 0, 1, 107),
)

_MOON_NEW = "\U0001F311"
_MOON_WAXING_CRESCENT = "\U0001F312"
_MOON_FIRST_QUARTER = "\U0001F313"
_MOON_WAXING_GIBBOUS = "\U0001F314"
_MOON_FULL = "\U0001F315"
_MOON_WANING_GIBBOUS = "\U0001F316"
_MOON_LAST_QUARTER = "\U0001F317"
_MOON_WANING_CRESCENT = "\U0001F318"

# Ширина «окон» вокруг точных сизигий и четвертей, в долях освещённости.
# Считать фазу по возрасту (восемь равных секторов по 1/8 лунации) нельзя:
# четверть тогда растягивается на ±1.8 суток и подписывается к диску,
# освещённому на две трети. По освещённости окна выходят примерно в сутки
# для новолуния/полнолуния и в ±9 часов для четвертей — как это и выглядит
# с земли.
_SYZYGY_WINDOW = 0.01
_QUARTER_WINDOW = 0.05


def _norm360(x: float) -> float:
    return x % 360.0


def _phase_name(illumination: float, waxing: bool) -> tuple[str, str]:
    """Эмодзи и название фазы по освещённости и направлению роста."""
    if illumination < _SYZYGY_WINDOW:
        return _MOON_NEW, "Ново"
    if illumination > 1 - _SYZYGY_WINDOW:
        return _MOON_FULL, "Полн"
    if abs(illumination - 0.5) < _QUARTER_WINDOW:
        return ((_MOON_FIRST_QUARTER, "Перв четв") if waxing
                else (_MOON_LAST_QUARTER, "Посл четв"))
    if waxing:
        return ((_MOON_WAXING_GIBBOUS, "Раст") if illumination > 0.5
                else (_MOON_WAXING_CRESCENT, "Растущий серп"))
    return ((_MOON_WANING_GIBBOUS, "Убыв Луна") if illumination > 0.5
            else (_MOON_WANING_CRESCENT, "Убыв серп"))


def _jd(dt: datetime) -> float:
    """Юлианская дата для момента в UTC (григорианский календарь)."""
    dt = dt.astimezone(timezone.utc)
    day = dt.day + (dt.hour + (dt.minute + (dt.second + dt.microsecond / 1e6)
                               / 60.0) / 60.0) / 24.0
    y, m = dt.year, dt.month
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + day + b - 1524.5)


def _jd_to_datetime(jd: float) -> datetime:
    """Обратное преобразование ЮД -> datetime в UTC."""
    jd += 0.5
    z = math.floor(jd)
    f = jd - z
    if z >= 2299161:
        alpha = math.floor((z - 1867216.25) / 36524.25)
        z += 1 + alpha - alpha // 4
    b = z + 1524
    c = math.floor((b - 122.1) / 365.25)
    d = math.floor(365.25 * c)
    e = math.floor((b - d) / 30.6001)
    day = b - d - math.floor(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    seconds = (day - math.floor(day)) * 86400.0
    return (datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
            + timedelta(seconds=seconds))


def _sun_apparent(t: float) -> tuple[float, float]:
    """Гл. 25: истинная эклиптическая долгота Солнца (град) и расстояние (км)."""
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = math.radians(357.52911 + 35999.05029 * t - 0.0001537 * t * t)
    e = 0.016708634 - 0.000042037 * t - 0.0000001267 * t * t
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m)
         + (0.019993 - 0.000101 * t) * math.sin(2 * m)
         + 0.000289 * math.sin(3 * m))
    v = m + math.radians(c)
    r_au = 1.000001018 * (1 - e * e) / (1 + e * math.cos(v))
    return _norm360(l0 + c), r_au * 149597870.7


def _moon_position(t: float) -> tuple[float, float, float]:
    """Гл. 47: геоцентрические долгота, широта (град) и расстояние (км)."""
    lp = _norm360(218.3164477 + 481267.88123421 * t - 0.0015786 * t ** 2
                  + t ** 3 / 538841 - t ** 4 / 65194000)
    d = _norm360(297.8501921 + 445267.1114034 * t - 0.0018819 * t ** 2
                 + t ** 3 / 545868 - t ** 4 / 113065000)
    m = _norm360(357.5291092 + 35999.0502909 * t - 0.0001536 * t ** 2
                 + t ** 3 / 24490000)
    mp = _norm360(134.9633964 + 477198.8675055 * t + 0.0087414 * t ** 2
                  + t ** 3 / 69699 - t ** 4 / 14712000)
    f = _norm360(93.2720950 + 483202.0175233 * t - 0.0036539 * t ** 2
                 - t ** 3 / 3526000 + t ** 4 / 863310000)

    a1 = _norm360(119.75 + 131.849 * t)
    a2 = _norm360(53.09 + 479264.290 * t)
    a3 = _norm360(313.45 + 481266.484 * t)
    # Эксцентриситет земной орбиты меняется, поэтому члены с M домножаются на E.
    ecc = 1 - 0.002516 * t - 0.0000074 * t * t

    dr, mr, mpr, fr = map(math.radians, (d, m, mp, f))
    sum_l = sum_r = sum_b = 0.0
    for cd, cm, cmp_, cf, cl, cr in _TERMS_LR:
        arg = cd * dr + cm * mr + cmp_ * mpr + cf * fr
        factor = ecc ** abs(cm)
        sum_l += cl * factor * math.sin(arg)
        sum_r += cr * factor * math.cos(arg)
    for cd, cm, cmp_, cf, cb in _TERMS_B:
        arg = cd * dr + cm * mr + cmp_ * mpr + cf * fr
        sum_b += cb * (ecc ** abs(cm)) * math.sin(arg)

    # Добавочные члены: возмущения Венерой и Юпитером, сплюснутость Земли.
    sum_l += (3958 * math.sin(math.radians(a1))
              + 1962 * math.sin(math.radians(lp - f))
              + 318 * math.sin(math.radians(a2)))
    sum_b += (-2235 * math.sin(math.radians(lp))
              + 382 * math.sin(math.radians(a3))
              + 175 * math.sin(math.radians(a1 - f))
              + 175 * math.sin(math.radians(a1 + f))
              + 127 * math.sin(math.radians(lp - mp))
              - 115 * math.sin(math.radians(lp + mp)))

    return _norm360(lp + sum_l / 1e6), sum_b / 1e6, 385000.56 + sum_r / 1000.0


def _equatorial(lam: float, beta: float, t: float) -> tuple[float, float]:
    """Эклиптические координаты -> прямое восхождение и склонение (град)."""
    eps = math.radians(23.4392911 - 0.0130042 * t - 1.64e-7 * t * t
                       + 5.036e-7 * t ** 3)
    lam_r, beta_r = math.radians(lam), math.radians(beta)
    ra = math.atan2(math.sin(lam_r) * math.cos(eps)
                    - math.tan(beta_r) * math.sin(eps), math.cos(lam_r))
    dec = math.asin(math.sin(beta_r) * math.cos(eps)
                    + math.cos(beta_r) * math.sin(eps) * math.sin(lam_r))
    return _norm360(math.degrees(ra)), math.degrees(dec)


def _gmst(jd_ut: float) -> float:
    """Гл. 12: среднее гринвичское звёздное время в градусах."""
    t = (jd_ut - 2451545.0) / 36525.0
    return _norm360(280.46061837 + 360.98564736629 * (jd_ut - 2451545.0)
                    + 0.000387933 * t * t - t ** 3 / 38710000.0)


def _moon_altitude(jd_ut: float, lat: float, lon: float) -> tuple[float, float]:
    """Высота Луны над горизонтом и пороговая высота её восхода (град)."""
    t = (jd_ut + _DELTA_T / 86400.0 - 2451545.0) / 36525.0
    lam, beta, dist = _moon_position(t)
    ra, dec = _equatorial(lam, beta, t)
    h = math.radians(_gmst(jd_ut) + lon - ra)
    lat_r, dec_r = math.radians(lat), math.radians(dec)
    alt = math.degrees(math.asin(
        math.sin(lat_r) * math.sin(dec_r)
        + math.cos(lat_r) * math.cos(dec_r) * math.cos(h)))
    # Восход — момент касания горизонта верхним краем диска с поправкой
    # на рефракцию (34') и горизонтальный параллакс (гл. 15).
    parallax = math.degrees(math.asin(6378.14 / dist))
    return alt, 0.7275 * parallax - 0.5666


def _rise_set(day_start_utc: datetime, lat: float, lon: float,
              tz_offset: float) -> tuple[datetime | None, datetime | None]:
    """Восход и заход Луны в пределах местных суток, начинающихся в day_start_utc.

    Луна встаёт примерно на 50 минут позже каждые сутки, поэтому в конкретный
    день восхода либо захода может не быть — тогда возвращается None.
    """
    jd0 = _jd(day_start_utc)
    step = 10.0 / 1440.0  # 10 минут
    steps = int(round(1.0 / step))
    rise = setting = None

    prev = None
    for i in range(steps + 1):
        jd = jd0 + i * step
        alt, h0 = _moon_altitude(jd, lat, lon)
        cur = alt - h0
        if prev is not None and prev[1] * cur < 0:
            # Линейная интерполяция внутри 10-минутного шага: Луна меняет
            # высоту достаточно медленно, ошибка — доли минуты.
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


def _phase_jde(k: float, full: bool) -> float:
    """Гл. 49: момент новолуния (full=False) или полнолуния в динамическом времени."""
    t = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k + 0.00015437 * t ** 2
           - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    ecc = 1 - 0.002516 * t - 0.0000074 * t * t
    m = math.radians(_norm360(2.5534 + 29.10535670 * k
                              - 0.0000014 * t ** 2 - 0.00000011 * t ** 3))
    mp = math.radians(_norm360(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2
                               + 0.00001238 * t ** 3 - 0.000000058 * t ** 4))
    f = math.radians(_norm360(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2
                              - 0.00000227 * t ** 3 + 0.000000011 * t ** 4))
    omega = math.radians(_norm360(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2
                                  + 0.00000215 * t ** 3))

    # Первые семь коэффициентов у новолуния и полнолуния слегка различаются.
    head = ((-0.40614, 0.17302, 0.01614, 0.01043, 0.00734, -0.00515, 0.00209)
            if full else
            (-0.40720, 0.17241, 0.01608, 0.01039, 0.00739, -0.00514, 0.00208))
    corr = (head[0] * math.sin(mp)
            + head[1] * ecc * math.sin(m)
            + head[2] * math.sin(2 * mp)
            + head[3] * math.sin(2 * f)
            + head[4] * ecc * math.sin(mp - m)
            + head[5] * ecc * math.sin(mp + m)
            + head[6] * ecc * ecc * math.sin(2 * m)
            - 0.00111 * math.sin(mp - 2 * f)
            - 0.00057 * math.sin(mp + 2 * f)
            + 0.00056 * ecc * math.sin(2 * mp + m)
            - 0.00042 * math.sin(3 * mp)
            + 0.00042 * ecc * math.sin(m + 2 * f)
            + 0.00038 * ecc * math.sin(m - 2 * f)
            - 0.00024 * ecc * math.sin(2 * mp - m)
            - 0.00017 * math.sin(omega)
            - 0.00007 * math.sin(mp + 2 * m)
            + 0.00004 * math.sin(2 * mp - 2 * f)
            + 0.00004 * math.sin(3 * m)
            + 0.00003 * math.sin(mp + m - 2 * f)
            + 0.00003 * math.sin(2 * mp + 2 * f)
            - 0.00003 * math.sin(mp + m + 2 * f)
            + 0.00003 * math.sin(mp - m + 2 * f)
            - 0.00002 * math.sin(mp - m - 2 * f)
            - 0.00002 * math.sin(3 * mp + m)
            + 0.00002 * math.sin(4 * mp))

    # Малые планетные возмущения (аргументы A1..A14 из гл. 49).
    extras = (
        (0.000325, 299.77, 0.107408, -0.009173), (0.000165, 251.88, 0.016321, 0),
        (0.000164, 251.83, 26.651886, 0), (0.000126, 349.42, 36.412478, 0),
        (0.000110, 84.66, 18.206239, 0), (0.000062, 141.74, 53.303771, 0),
        (0.000060, 207.14, 2.453732, 0), (0.000056, 154.84, 7.306860, 0),
        (0.000047, 34.52, 27.261239, 0), (0.000042, 207.19, 0.121824, 0),
        (0.000040, 291.34, 1.844379, 0), (0.000037, 161.72, 24.198154, 0),
        (0.000035, 239.56, 25.513099, 0), (0.000023, 331.55, 3.592518, 0),
    )
    for amp, c0, c1, c2 in extras:
        corr += amp * math.sin(math.radians(c0 + c1 * k + c2 * t * t))

    return jde + corr


def _lunation_number(moment: datetime) -> int:
    """Оценка номера лунации k от новолуния 2000-01-06 (гл. 49, точность ±1)."""
    year = moment.year + (moment.timetuple().tm_yday - 1) / 365.25
    return math.floor((year - 2000) * 12.3685)


def _phase_moment(after: datetime, full: bool) -> datetime:
    """Ближайшее новолуние/полнолуние строго после `after` (UTC)."""
    jd_target = _jd(after)
    # k целое — новолуние, k+0.5 — полнолуние. Оценка k неточна на ±1,
    # поэтому стартуем заведомо раньше и идём вперёд до первого попадания.
    k = _lunation_number(after) - 2 + (0.5 if full else 0)
    while True:
        jd_ut = _phase_jde(k, full) - _DELTA_T / 86400.0
        if jd_ut > jd_target:
            return _jd_to_datetime(jd_ut)
        k += 1


def _prev_new_moon(before: datetime) -> datetime:
    """Последнее новолуние не позже `before` (UTC).

    Идём назад по номеру лунации, а не ищем вперёд внутри окна шириной
    в лунацию: окно захватывало бы новолуние у самого своего начала и
    давало возраст ~29 суток вместо ~0.
    """
    jd_target = _jd(before)
    k = _lunation_number(before) + 2
    while _phase_jde(k, False) - _DELTA_T / 86400.0 > jd_target:
        k -= 1
    return _jd_to_datetime(_phase_jde(k, False) - _DELTA_T / 86400.0)


@dataclass
class MoonInfo:
    emoji: str
    name: str
    illumination: float          # доля освещённого диска, 0..1
    age_days: float              # время с последнего новолуния, сут
    distance_km: float
    next_new: datetime
    next_full: datetime
    rise: datetime | None
    set: datetime | None


def get_moon_info(when: datetime | None = None, *,
                  lat: float = OMSK_LAT, lon: float = OMSK_LON,
                  tz_offset: float = 6.0) -> MoonInfo:
    now = when or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    t = (_jd(now) + _DELTA_T / 86400.0 - 2451545.0) / 36525.0
    moon_lon, moon_lat, dist = _moon_position(t)
    sun_lon, sun_dist = _sun_apparent(t)

    # Гл. 48: фазовый угол через геоцентрическую элонгацию psi.
    elongation = _norm360(moon_lon - sun_lon)
    psi = math.acos(math.cos(math.radians(moon_lat))
                    * math.cos(math.radians(elongation)))
    phase_angle = math.atan2(sun_dist * math.sin(psi),
                             dist - sun_dist * math.cos(psi))
    illumination = (1 + math.cos(phase_angle)) / 2
    # Освещённость симметрична относительно сизигий, поэтому растущую фазу
    # от убывающей отличает только знак элонгации.
    emoji, name = _phase_name(illumination, waxing=elongation < 180.0)

    # Возраст — от фактического предыдущего новолуния, а не от средней лунации:
    # реальные лунации гуляют на ±0.5 суток относительно средней.
    age = (now - _prev_new_moon(now)).total_seconds() / 86400.0

    tz = timezone(timedelta(hours=tz_offset))
    local_midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0,
                                                microsecond=0)
    rise, setting = _rise_set(local_midnight.astimezone(timezone.utc),
                              lat, lon, tz_offset)

    return MoonInfo(
        emoji=emoji, name=name, illumination=illumination, age_days=age,
        distance_km=dist,
        next_new=_phase_moment(now, full=False).astimezone(tz),
        next_full=_phase_moment(now, full=True).astimezone(tz),
        rise=rise, set=setting,
    )


def format_moon(info: MoonInfo) -> str:
    rise = info.rise.strftime("%H:%M") if info.rise else "—"
    setting = info.set.strftime("%H:%M") if info.set else "—"
    return "\n".join((
        f"{info.emoji} {info.name}, {round(info.illumination * 100)}%",
        f"Возраст {info.age_days:.1f} сут Восход {rise}, заход {setting}",
        f"{_MOON_FULL} {info.next_full.strftime('%d.%m %H:%M')}",
        f"{_MOON_NEW} {info.next_new.strftime('%d.%m %H:%M')}",
    ))


async def get_moon(lat: float = OMSK_LAT, lon: float = OMSK_LON,
                   tz_offset: float = 6.0) -> str:
    """Готовый текст для команды /moon.

    Корутина ради единообразия с остальными источниками данных — сети здесь
    нет, расчёт занимает единицы миллисекунд и не блокирует цикл событий.
    """
    return format_moon(get_moon_info(lat=lat, lon=lon, tz_offset=tz_offset))
