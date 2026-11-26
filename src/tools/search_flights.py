import os
import requests
from datetime import datetime, date
from typing import Any
from dotenv import load_dotenv
import re

load_dotenv()

SERPAPI_API_URL = "https://serpapi.com/search"
MAX_FLIGHTS = 5
TRAVEL_CLASSES = {
    "economy": 1,
    "premium": 2,
    "business": 3,
    "first": 4,
}
SORTING_OPTIONS = {
    "top_flights": 1,
    "price": 2,
    "departure_time": 3,
    "arrival_time": 4,
    "duration": 5,
}
LAYOVER_OPTIONS = {
    "any": 0,
    "nonstop": 1,
    "one_or_fewer": 2,
}
ALL_CURRENCY = {
    "Albanian Lek": "ALL",
    "Algerian Dinar": "DZD",
    "Argentine Peso": "ARS",
    "Armenian Dram": "AMD",
    "Aruban Florin": "AWG",
    "Australian Dollar": "AUD",
    "Azerbaijani Manat": "AZN",
    "Bahamian Dollar": "BSD",
    "Bahraini Dinar": "BHD",
    "Belarusian Ruble": "BYN",
    "Bermudan Dollar": "BMD",
    "Bosnia-Herzegovina Convertible Mark": "BAM",
    "Brazilian Real": "BRL",
    "British Pound": "GBP",
    "Bulgarian Lev": "BGN",
    "CFP Franc": "XPF",
    "Canadian Dollar": "CAD",
    "Chilean Peso": "CLP",
    "Chinese Yuan": "CNY",
    "Colombian Peso": "COP",
    "Costa Rican Colón": "CRC",
    "Cuban Peso": "CUP",
    "Czech Koruna": "CZK",
    "Danish Krone": "DKK",
    "Dominican Peso": "DOP",
    "Egyptian Pound": "EGP",
    "Euro": "EUR",
    "Georgian Lari": "GEL",
    "Hong Kong Dollar": "HKD",
    "Hungarian Forint": "HUF",
    "Icelandic Króna": "ISK",
    "Indian Rupee": "INR",
    "Indonesian Rupiah": "IDR",
    "Iranian Rial": "IRR",
    "Israeli New Shekel": "ILS",
    "Jamaican Dollar": "JMD",
    "Japanese Yen": "JPY",
    "Jordanian Dinar": "JOD",
    "Kazakhstani Tenge": "KZT",
    "Kuwaiti Dinar": "KWD",
    "Lebanese Pound": "LBP",
    "Macedonian Denar": "MKD",
    "Malaysian Ringgit": "MYR",
    "Mexican Peso": "MXN",
    "Moldovan Leu": "MDL",
    "Moroccan Dirham": "MAD",
    "New Taiwan Dollar": "TWD",
    "New Zealand Dollar": "NZD",
    "Norwegian Krone": "NOK",
    "Omani Rial": "OMR",
    "Pakistani Rupee": "PKR",
    "Panamanian Balboa": "PAB",
    "Peruvian Sol": "PEN",
    "Philippine Peso": "PHP",
    "Polish Zloty": "PLN",
    "Qatari Riyal": "QAR",
    "Romanian Leu": "RON",
    "Russian Ruble": "RUB",
    "Saudi Riyal": "SAR",
    "Serbian Dinar": "RSD",
    "Singapore Dollar": "SGD",
    "South African Rand": "ZAR",
    "South Korean Won": "KRW",
    "Swedish Krona": "SEK",
    "Swiss Franc": "CHF",
    "Thai Baht": "THB",
    "Turkish Lira": "TRY",
    "US Dollar": "USD",
    "Ukrainian Hryvnia": "UAH",
    "United Arab Emirates Dirham": "AED",
    "Vietnamese Dong": "VND",
}

def search_flights(
    airport_departure_id: str,
    airport_arrival_id: str,
    outbound_date: str,
    currency: str,
    round_trip: bool,
    sort_by: str,
    return_date: str | None = None,
    budget: int | None = None,
    layover: str = "any",
    travel_class: str = "economy",
    adults: int = 1,
    children: int = 0,
    infants_in_seat: int = 0,
    infants_on_lap: int = 0,
) -> dict[str, Any]:
    airport_departure_id = airport_departure_id.strip().upper()
    airport_arrival_id = airport_arrival_id.strip().upper()
    currency = currency.strip().upper()
    sort_by = sort_by.strip().lower()
    travel_class = travel_class.strip().lower()
    layover = layover.strip().lower()

    if budget:
        budget = int(budget)

    isvalid, err = valid_params(airport_departure_id, airport_arrival_id, outbound_date, return_date, currency,
                                round_trip, budget, layover, sort_by, travel_class, adults, children, infants_in_seat, infants_on_lap)

    if not isvalid:
        return { "error": err, "tool": "search_flights" }

    api_key = os.getenv("SERPAPI_API_KEY")

    if not api_key:
        return { "error": "La variable d'environnement SERPAPI_API_KEY n'est pas définie.", "tool": "search_flights" }

    params = {
        "engine": "google_flights",
        "api_key": api_key,
        "departure_id": airport_departure_id,
        "arrival_id": airport_arrival_id,
        "outbound_date": outbound_date,
        "currency": currency,
        "type": 1 if round_trip else 2,
        "travel_class": TRAVEL_CLASSES[travel_class],
        "adults": adults,
        "children": children,
        "infants_in_seat": infants_in_seat,
        "infants_on_lap": infants_on_lap,
        "sort_by": SORTING_OPTIONS[sort_by],
        "stops": LAYOVER_OPTIONS[layover]
    }

    if round_trip and return_date:
        params["return_date"] = return_date

    if budget is not None:
        params["max_price"] = budget

    res = requests.get(
        SERPAPI_API_URL,
        params=params,
        timeout=30,
    )

    try:
        res.raise_for_status()
    except Exception as e:
        return { "error": e, "tool": "search_flights" }

    response = res.json()

    if "error" in response:
        if str(response.get("search_information", {}).get("flights_results_state", "")).strip().lower() == "fully empty":
            return { "info": "Aucun vol trouvé. Essayez de changer les dates ou les paramètres de recherche" }
        return { "error": f"Erreur SerpApi : {response['error']}", "tool": "search_flights" }

    flights, alternatives, ukn_prices, err_msg = format_result(response, budget)

    if flights is None and alternatives is None and ukn_prices is None:
        return { "error": err_msg, "tool": "search_flights" }

    res_data = {
        "flights_found": flights != [],
        "alternatives_found": alternatives != [],
        "unknown_prices_found": ukn_prices != [],
        "airport_departure_id": airport_departure_id,
        "airport_arrival_id": airport_arrival_id,
        "outbound_date": outbound_date,
        "currency": currency,
        "passengers": {
            "adults": adults,
            "children": children,
            "infants_in_seat": infants_in_seat,
            "infants_on_lap": infants_on_lap,
        },
        "flights_results_count": len(flights),
        "alternatives_results_count": len(alternatives),
        "unknown_prices_count": len(ukn_prices),
        "flights": flights,
        "alternatives": alternatives,
        "unknown_prices": ukn_prices
    }

    if return_date is not None:
        res_data["return_date"] = return_date

    if budget is not None:
        res_data["budget"] = budget

    return res_data

def fail(err_msg: str):
    return False, err_msg

def passed():
    return True, ""

def valid_params(
    airport_departure_id: str,
    airport_arrival_id: str,
    outbound_date: str,
    return_date: str | None,
    currency: str,
    round_trip: bool,
    budget: int | None,
    layover: str,
    sort_by: str,
    travel_class: str,
    adults: int,
    children: int,
    infants_in_seat: int,
    infants_on_lap: int,
):
    def validate_date(date_str: str, field_name: str) -> tuple[bool, str]:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return passed()
        except (ValueError, TypeError):
            return fail(f"{field_name} doit respecter le format YYYY-MM-DD.")

    try:
        isvalid, err = validate_date(outbound_date, "outbound_date")
        if not isvalid:
            return fail(err)
    
        departure = date.fromisoformat(outbound_date)

        if departure < date.today():
            return fail("La date de départ doit être dans le futur.")

        if return_date:
            isvalid, err = validate_date(return_date, "return_date")
            if not isvalid:
                return fail(err)

            retour = date.fromisoformat(return_date)

            if retour < departure:
                return fail("La date de retour doit être égale ou postérieure à la date de départ.")

    except ValueError as exc:
        return fail(f"Dates invalides : {exc}")

    if return_date is None and round_trip is True:
        return fail("La date de retour est requise pour une recherche de vol aller-retour")

    iata_pattern = re.compile(r"^[A-Z]{3}$")

    if not iata_pattern.fullmatch(airport_departure_id):
        return fail(f"Code IATA de départ invalide : {airport_departure_id!r}.")

    if not iata_pattern.fullmatch(airport_arrival_id):
        return fail(f"Code IATA d'arrivée invalide : {airport_arrival_id!r}.")

    if not airport_departure_id or not airport_arrival_id:
        return fail("Les aéroports de départ et d'arrivée sont requis.")

    if airport_departure_id == airport_arrival_id:
        return fail("Les aéroports de départ et d'arrivée doivent différer.")

    if not currency:
        return fail("La monnaie est requise.")

    if not currency in ALL_CURRENCY.values():
        return fail(f"La monnaie {currency} n'existe pas, toutes les monnaies existante sont : {ALL_CURRENCY}")

    if budget is not None and budget <= 0:
        return fail("Le budget doit être supérieur à zéro.")

    passenger_counts = {
        "adults": adults,
        "children": children,
        "infants_in_seat": infants_in_seat,
        "infants_on_lap": infants_on_lap,
    }

    for category, count in passenger_counts.items():
        if not isinstance(count, int) or isinstance(count, bool):
            return fail(f"Le nombre de passagers '{category}' doit être un entier.")

        if count < 0:
            return fail(f"Le nombre de passagers '{category}' ne peut pas être négatif.")

    if adults < 1:
        return fail("Il faut au moins un adulte.")

    if infants_on_lap > adults:
        return fail("Le nombre de nourrissons sur les genoux ne peut pas dépasser le nombre d'adultes.")

    if layover not in LAYOVER_OPTIONS:
        return fail(f"Escale invalide : {layover}. Valeurs acceptées : {list(LAYOVER_OPTIONS.keys())}")

    if travel_class not in TRAVEL_CLASSES:
        return fail(f"Classe invalide : {travel_class}. Valeurs acceptées : {list(TRAVEL_CLASSES.keys())}")

    if sort_by not in SORTING_OPTIONS:
        return fail(f"Tri invalide : {sort_by}. Valeurs acceptées : {list(SORTING_OPTIONS.keys())}")

    return passed()

def format_result(response, budget):
    if response.get("search_metadata", {}).get("status") == "Error":
        return None, None, "La recherche SerpAPI a échoué."

    raw_flights = (
        response.get("best_flights", [])
        + response.get("other_flights", [])
    )

    flights = []
    alternatives = []
    ukn_prices = []

    for flight in raw_flights:
        isAlt = False
        isKnowPrice = True
        price = flight.get("price")

        if price is None:
            isKnowPrice = False

        if isKnowPrice:
            try:
                price = int(price)
            except (ValueError, TypeError):
                continue

            if budget is not None and price > budget:
                isAlt = True

        segments = []

        for segment in flight.get("flights", []):
            departure_airport = segment.get("departure_airport") or {}
            arrival_airport = segment.get("arrival_airport") or {}

            segments.append({
                "airline": segment.get("airline"),
                "flight_number": segment.get("flight_number"),
                "departure_airport": departure_airport.get("id"),
                "departure_time": departure_airport.get("time"),
                "arrival_airport": arrival_airport.get("id"),
                "arrival_time": arrival_airport.get("time"),
                "duration_minutes": segment.get("duration"),
            })

        if not segments:
            continue

        if isAlt:
            alternatives.append({
                "price": price,
                "total_duration_minutes": flight.get("total_duration"),
                "stops": max(0, len(segments) - 1),
                "segments": segments,
            })
        elif not isKnowPrice:
            ukn_prices.append({
                "price": "unknown",
                "total_duration_minutes": flight.get("total_duration"),
                "stops": max(0, len(segments) - 1),
                "segments": segments,
            })
        else:
            flights.append({
                "price": price,
                "total_duration_minutes": flight.get("total_duration"),
                "stops": max(0, len(segments) - 1),
                "segments": segments,
            })

    for f in [flights, alternatives]:
        f.sort(
            key=lambda flight: (
                flight["price"],
                flight["total_duration_minutes"]
                if flight["total_duration_minutes"] is not None
                else float("inf"),
            )
        )

    return flights[:MAX_FLIGHTS], alternatives[:MAX_FLIGHTS], ukn_prices[:MAX_FLIGHTS], ""