"""
Парсер текущей погоды в Омске с сайта Гисметео.

Зависимости:
    pip install requests beautifulsoup4

Внимание:
    - Гисметео меняет верстку -> CSS-селекторы могут перестать работать.
    - Сайт может отдавать 403 без "человеческого" User-Agent или требовать
      прохождения защиты (Cloudflare). Для стабильной работы рассмотрите
      официальный API: https://b2b.gismeteo.ru/
"""

from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Идентификатор Омска в URL Гисметео: weather-omsk-4578
OMSK_URL = "https://www.gismeteo.ru/weather-omsk-4578/"
OMSK_URL_MOBILE = "https://m.gismeteo.ru/weather-omsk-4578/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class Weather:
    city: str
    temperature: Optional[str] = None      # температура, °C
    condition: Optional[str] = None        # описание (облачно, дождь и т.п.)
    feels_like: Optional[str] = None       # ощущается как
    wind: Optional[str] = None             # ветер
    pressure: Optional[str] = None         # давление
    humidity: Optional[str] = None         # влажность

    def __str__(self) -> str:
        parts = [f"Погода в г. {self.city}:"]
        if self.temperature:
            parts.append(f"  Температура: {self.temperature} °C")
        if self.feels_like:
            parts.append(f"  Ощущается как: {self.feels_like} °C")
        if self.condition:
            parts.append(f"  Состояние: {self.condition}")
        if self.wind:
            parts.append(f"  Ветер: {self.wind}")
        if self.pressure:
            parts.append(f"  Давление: {self.pressure}")
        if self.humidity:
            parts.append(f"  Влажность: {self.humidity}")
        return "\n".join(parts)


def _clean(text: Optional[str]) -> Optional[str]:
    """Убирает лишние пробелы/переносы строк."""
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def fetch_weather_omsk(timeout: int = 15) -> Weather:
    """
    Загружает и парсит текущую погоду в Омске с Гисметео.

    Сначала пробует основную версию сайта, при неудаче — мобильную.
    Возвращает объект Weather. Поля, которые не удалось распарсить,
    останутся None.
    """
    # 1) Основная версия
    try:
        weather = _parse_desktop(OMSK_URL, timeout)
        if weather.temperature:
            return weather
    except requests.RequestException as e:
        print(f"[warn] основная версия недоступна: {e}")

    # 2) Мобильная версия (легче и стабильнее парсится)
    return _parse_mobile(OMSK_URL_MOBILE, timeout)


def _parse_desktop(url: str, timeout: int) -> Weather:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    weather = Weather(city="Омск")

    # Температура: блок с текущей температурой.
    # Селекторы приблизительны — при редизайне их нужно проверить в DevTools.
    temp = soup.select_one("temperature-value, .now-weather .unit_temperature_c")
    if temp is None:
        # запасной вариант: любой элемент с классом, содержащим 'temp'
        temp = soup.find(attrs={"class": lambda c: c and "temperature-value" in c})
    weather.temperature = _clean(temp.get_text()) if temp else None

    # Описание погоды
    cond = soup.select_one(".now-desc, .now-weather .now-desc")
    weather.condition = _clean(cond.get_text()) if cond else None

    # Дополнительные параметры часто лежат в блоке .now-info-item
    for item in soup.select(".now-info-item"):
        name = _clean(item.select_one(".name").get_text()) if item.select_one(".name") else ""
        value = _clean(item.select_one(".value").get_text()) if item.select_one(".value") else ""
        low = (name or "").lower()
        if "ощущается" in low:
            weather.feels_like = value
        elif "ветер" in low:
            weather.wind = value
        elif "давление" in low:
            weather.pressure = value
        elif "влажность" in low:
            weather.humidity = value

    return weather


def _parse_mobile(url: str, timeout: int) -> Weather:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    weather = Weather(city="Омск")

    temp = soup.find(attrs={"class": lambda c: c and "temperature" in c.lower()})
    weather.temperature = _clean(temp.get_text()) if temp else None

    cond = soup.find(attrs={"class": lambda c: c and "description" in c.lower()})
    weather.condition = _clean(cond.get_text()) if cond else None

    return weather


if __name__ == "__main__":
    try:
        w = fetch_weather_omsk()
        print(w)
    except Exception as exc:
        print(f"Не удалось получить погоду: {exc}")
