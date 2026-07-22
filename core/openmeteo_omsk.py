"""
Получение текущей погоды через бесплатное API Open-Meteo (без ключа и токена).

По умолчанию — Омск. Можно передать другой город (через геокодер Open-Meteo)
или явные координаты.

Зависимости:
    pip install requests

Документация API: https://open-meteo.com/en/docs
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Координаты Омска (по умолчанию)
OMSK_LAT, OMSK_LON = 54.9914, 73.3645

# Расшифровка WMO weather code (https://open-meteo.com/en/docs)
WMO_CODES = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "морось слабая",
    53: "морось умеренная",
    55: "морось сильная",
    56: "ледяная морось слабая",
    57: "ледяная морось сильная",
    61: "дождь слабый",
    63: "дождь умеренный",
    65: "дождь сильный",
    66: "ледяной дождь слабый",
    67: "ледяной дождь сильный",
    71: "снег слабый",
    73: "снег умеренный",
    75: "снег сильный",
    77: "снежная крупа",
    80: "ливень слабый",
    81: "ливень умеренный",
    82: "ливень сильный",
    85: "снегопад слабый",
    86: "снегопад сильный",
    95: "гроза",
    96: "гроза со слабым градом",
    99: "гроза с сильным градом",
}

# Направление ветра по градусам (16 румбов)
_WIND_DIRS = [
    "С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ",
    "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ",
]


@dataclass
class Weather:
    city: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    country: Optional[str] = None
    temperature: Optional[float] = None        # °C
    feels_like: Optional[float] = None         # °C
    condition: Optional[str] = None            # текстовое описание
    humidity: Optional[int] = None             # %
    wind_speed: Optional[float] = None         # км/ч
    wind_dir: Optional[str] = None             # румб (С, ЮЗ, ...)
    pressure_mmhg: Optional[float] = None      # мм рт. ст.
    time: Optional[str] = None                 # локальное время замера

    def __str__(self) -> str:
        lines = [f"{self.city} ({self.country if self.country else "UNK"}) {self.temperature:+.0f}({self.feels_like:+.0f}),{self.condition},{self.wind_dir}{self.wind_speed:.0f}км/ч,{self.humidity}%,{self.pressure_mmhg:.0f}мм.р.с."]
        return "\n".join(lines)

    def __str2__(self) -> str:
        lines = [f"{"Погода в г. " if self.lat and self.lon else ""}{self.city} ({self.country if self.country else "UNKNOWN"})"]
        if self.lat and self.lon:
            lines.append(f"  Координаты: {self.lat}, {self.lon}")
        if self.time:
            lines.append(f"  Время:          {self.time}")
        if self.temperature is not None:
            lines.append(f"  Температура:    {self.temperature:+.0f} °C")
        if self.feels_like is not None:
            lines.append(f"  Ощущается как:  {self.feels_like:+.0f} °C")
        if self.condition:
            lines.append(f"  Состояние:      {self.condition}")
        if self.wind_speed is not None:
            wind = f"{self.wind_speed:.0f} км/ч"
            if self.wind_dir:
                wind += f", {self.wind_dir}"
            lines.append(f"  Ветер:          {wind}")
        if self.humidity is not None:
            lines.append(f"  Влажность:      {self.humidity} %")
        if self.pressure_mmhg is not None:
            lines.append(f"  Давление:       {self.pressure_mmhg:.0f} мм рт. ст.")
        return "\n".join(lines)


def _deg_to_compass(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    idx = int((deg / 22.5) + 0.5) % 16
    return _WIND_DIRS[idx]


def geocode_city(name: str, timeout: int = 15) -> Tuple[float, float, str, str]:
    """Находит координаты города по названию через геокодер Open-Meteo."""
    resp = requests.get(
        GEOCODING_URL,
        params={"name": name, "count": 1, "language": "ru", "format": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json().get("results")
    if not results:
        return None, None, f"Город не найден: {name!r}", None
    r = results[0]
    return r["latitude"], r["longitude"], r["name"], r["country"]


async def get_weather(
    city: str = "Омск",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    timeout: int = 15,
) -> Weather:
    """
    Возвращает текущую погоду.

    - Если переданы lat и lon — используются они (city идёт как подпись).
    - Иначе для 'Омск' берутся встроенные координаты, для остальных
      городов координаты ищутся через геокодер.
    """
    if lat is None or lon is None:
        if city.strip().lower() in ("омск", "omsk"):
            lat, lon, city_name, country = OMSK_LAT, OMSK_LON, "Омск", "Россия"
        else:
            lat, lon, city_name, country = geocode_city(city, timeout)
    else:
        city_name = city


    if not lat and not lon and not country:
        return Weather(
            city=city_name,
            country=country
        )


    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "weather_code",
            "surface_pressure",
            "wind_speed_10m",
            "wind_direction_10m",
        ]),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }

    resp = requests.get(FORECAST_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    cur = resp.json().get("current", {})

    pressure_hpa = cur.get("surface_pressure")
    pressure_mmhg = pressure_hpa * 0.750062 if pressure_hpa is not None else None

    return Weather(
        city=city_name,
        lat=lat,
        lon=lon,
        country=country,
        temperature=cur.get("temperature_2m"),
        feels_like=cur.get("apparent_temperature"),
        condition=WMO_CODES.get(cur.get("weather_code"), "н/д"),
        humidity=cur.get("relative_humidity_2m"),
        wind_speed=cur.get("wind_speed_10m"),
        wind_dir=_deg_to_compass(cur.get("wind_direction_10m")),
        pressure_mmhg=pressure_mmhg,
        time=cur.get("time"),
    )

if __name__ == "__main__":
    try:
        print(get_weather("Омск"))
        # Пример для другого города:
        # print(get_weather("Новосибирск"))
    except requests.RequestException as e:
        print(f"Ошибка сети: {e}")
    except Exception as e:
        print(f"Ошибка: {e}")
