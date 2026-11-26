from datetime import datetime
from datetime import date
from collections import Counter
from statistics import mean
import urllib.parse as uri
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SEASONAL_URL = "https://seasonal-api.open-meteo.com/v1/seasonal"

DATE_FORMAT = "%Y-%m-%d"
EARLIEST_DATE = date.fromisoformat("1940-01-01")

WEATHER_CONDITIONS = {
    "0": "Clear sky",
    "1": "Mostly clear",
    "2": "Partly cloudy",
    "3": "Overcast",
    "45,48": "Fog",
    "51,53,55": "Drizzly",
    "61": "Light rain",
    "63": "Moderate rain",
    "65": "Heavy rain",
    "71,73,75": "Snowy",
    "80,81,82": "Rain shower",
    "95,96,99": "Thunderstorms"
}

ACCEPTED_WEATHER_CODE = [0, 1, 2, 3, 45, 48, 51, 53, 55, 61, 63, 65, 71, 73, 75, 80, 81, 82, 95, 96, 99]

def weather(city: str, now: bool = False, start_date: str | None = None, end_date: str | None = None):

    latitude, longitude, err_msg = get_latitude_longitude(city)

    if latitude is None or longitude is None:
        return { "error": err_msg }

    if now:
        temp, unit, w_code, err_msg = get_realtime_temperature(latitude, longitude)

        if temp is None or unit is None or w_code is None:
            return { "error": err_msg }

        return { "city": city, "temperature": str(temp) + unit, "weather_condition": get_condition_with_weather_code(w_code) }

    date_error = get_invalid_date_error(start_date, end_date)

    if date_error is not None:
        return { "error": date_error, "tool": "weather" }

    return get_weather(city, latitude, longitude, start_date, end_date)




def get_invalid_date_error(start_date: str | None, end_date: str | None):
    if start_date is None:
        return "start_date is empty"
    elif end_date is None:
        return "end_date is empty"
    elif not is_valid_date(start_date):
        return "start_date is not in the correct format"
    elif not is_valid_date(end_date):
        return "end_date is not in the correct format"
    elif date.fromisoformat(start_date) > date.fromisoformat(end_date):
        return "start_date must precede end_date"
    elif date.fromisoformat(start_date) < EARLIEST_DATE:
        return f"start_date must not be earlier than {EARLIEST_DATE}"
    elif (date.fromisoformat(end_date) - date.today()).days > 217:
        return "The journey is too far back in time"

    return None

def is_valid_date(str_date: str):
    try:
        datetime.strptime(str_date, DATE_FORMAT)
        return True
    except ValueError:
        return False
    except:
        return False

def get_latitude_longitude(city: str):
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={uri.quote(city)}&count=1&language=fr&format=json"

    try:
        response = requests.get(
            url,
            timeout=30
        )
        
        response.raise_for_status()

        data = response.json()
        results = data.get("results", [{}])[0]

        return results.get("latitude", None), results.get("longitude", None), "Unexpected error, try reformulating the parameters"
    except ConnectionError:
        return None, None, "Connection lost"
    except:
        return None, None, "Unexpected error, try reformulating the parameters"

def get_condition_with_weather_code(w_code: str):
    for code, cond in WEATHER_CONDITIONS.items():
        if str(w_code) in code.split(","):
            return cond
    return "Unknown condition"



def get_realtime_temperature(latitude: float | str, longitude: float | str):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current=temperature_2m,weather_code"

    try:
        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        results = response.json()

        return results.get("current", {}).get("temperature_2m", 0.0), results.get("current_units", {}).get("temperature_2m", "°C"), results.get("current", {}).get("weather_code", 1), "Unexpected error, try reformulating the parameters"
    except ConnectionError:
        return None, None, None, "Connection lost"
    except:
        return None, None, None, "Unexpected error, try reformulating the parameters"

def get_average_temperature(daily):
    temperatures = daily["temperature_2m_mean"]
    weather_codes = daily["weather_code"]

    valid_temperatures = [
        temperature
        for temperature in temperatures
        if temperature is not None
    ]

    average_temperature = (
        round(mean(valid_temperatures), 1)
        if valid_temperatures
        else None
    )

    daily_weather = [
        {
            "date": day,
            "temperature_mean": temperature,
            "weather_condition": get_condition_with_weather_code(code)
        }
        for day, temperature, code in zip(
            daily["time"],
            temperatures,
            weather_codes
        )
    ]

    return {
        "average_temperature": str(average_temperature) + "°C",
        "daily_weather": daily_weather
    }

def summarize_weather(daily):
    temperatures = daily["temperature_2m_mean"]
    weather_codes = daily["weather_code"]

    valid_temperatures = [
        temperature
        for temperature in temperatures
        if temperature is not None
    ]

    average_temperature = (
        round(mean(valid_temperatures), 1)
        if valid_temperatures
        else None
    )

    categories = Counter()

    for code in weather_codes:
        if code is None:
            continue

        if code in (0, 1, 2):
            categories["clear_or_partly_cloudy"] += 1
        elif code == 3:
            categories["cloudy"] += 1
        elif code in (45, 48):
            categories["fog"] += 1
        elif code in (51, 53, 55, 56, 57):
            categories["drizzle"] += 1
        elif code in (61, 63, 65, 66, 67, 80, 81, 82):
            categories["rain"] += 1
        elif code in (71, 73, 75, 77, 85, 86):
            categories["snow"] += 1
        elif code in (95, 96, 99):
            categories["thunderstorm"] += 1
        else:
            categories["other"] += 1

    return {
        "duration_days": len(daily["time"]),
        "average_temperature": str(average_temperature) + "°C",
        "weather_summary": dict(categories)
    }

def get_temperature(latitude, longitude, start_date, end_date, api_url, unused_days):
    response = requests.get(
            api_url,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": "temperature_2m_mean,weather_code",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "timezone": "auto"
            },
            timeout=30
        )
    
    response.raise_for_status()

    daily = response.json()["daily"]

    days_of_travel = (end_date - start_date).days

    if days_of_travel > 7:
        res_dict = summarize_weather(daily)
    res_dict = get_average_temperature(daily)

    if unused_days > 0:
        res_dict["note"] = f"{unused_days} day(s) have not been taken into account, as the stay begins before the current date and ends after it."

    return res_dict


def get_weather(city, latitude, longitude, str_start_date, str_end_date, unused_days: int = 0):
    start_date = date.fromisoformat(str_start_date)
    end_date = date.fromisoformat(str_end_date)

    today = date.today()

    days_until_end = (end_date - today).days

    if end_date <= today:
        return get_temperature(latitude, longitude, start_date, end_date, ARCHIVE_URL, unused_days)

    if start_date < today:
        return get_weather(city, latitude, longitude, start_date.isoformat(), today.isoformat(), (end_date - today).days)

    if days_until_end <= 16:
        return get_temperature(latitude, longitude, start_date, end_date, FORECAST_URL, unused_days)

    if days_until_end <= 217:
        return get_temperature(latitude, longitude, start_date, end_date, SEASONAL_URL, unused_days)

    return { "error": "Unexpected error, try reformulating the parameters" }
