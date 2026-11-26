import os
import re
import requests
import json

from datetime import date
from dotenv import load_dotenv
from .search_flights import ALL_CURRENCY


load_dotenv()


SERPAPI_API_URL = "https://serpapi.com/search"
MAX_HOUSINGS = 3

SORTING_OPTIONS = {
    "relevance": None,
    "price": 3,
    "rating": 8,
    "most_reviewed": 13,
}

RATING_OPTIONS = {
    3.5: 7,
    4.0: 8,
    4.5: 9,
}

ALL_PROPERTY_TYPES = {
    "beach_hotels": 12,
    "boutique_hotels": 13,
    "hostels": 14,
    "inns": 15,
    "motels": 16,
    "resorts": 17,
    "spa_hotels": 18,
    "bed_and_breakfasts": 19,
    "apartment_hotels": 21,
    "minshuku": 22,
    "japanese_style_business_hotels": 23,
    "ryokan": 24,
    "other": 20
}

ALL_AMENITIES = {
    "free_parking": 1,
    "parking": 3,
    "indoor_pool": 4,
    "outdoor_pool": 5,
    "pool": 6,
    "fitness_center": 7,
    "restaurant": 8,
    "free_breakfast": 9,
    "spa": 10,
    "beach_access": 11,
    "child_friendly": 12,
    "bar": 15,
    "pet_friendly": 19,
    "room_service": 22,
    "free_wifi": 35,
    "air_conditioned": 40,
    "all_inclusive_available": 52,
    "wheelchair_accessible": 53,
    "ev_charger": 61
}

def search_housing(
    destination: str,
    check_in_date: str,
    check_out_date: str,
    currency: str,
    adults: int = 2,
    children: int = 0,
    children_ages: list[int] | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    price_basis: str = "total",
    min_rating: float | None = None,
    min_hotel_class: int | None = None,
    max_hotel_class: int | None = None,
    property_types: list[str] | None = None,
    amenities: list[str] | None = None,
    free_cancellation: bool = False,
    special_offers: bool = False,
    eco_certified: bool = False,
    vacation_rentals: bool = False,
    min_bedrooms: int | None = None,
    min_bathrooms: int | None = None,
    sort_by: str = "relevance",
) -> dict:
    api_key = os.getenv("SERPAPI_API_KEY")

    if not api_key:
        return {
            "status": "error",
            "message": "La variable SERPAPI_API_KEY n'est pas configurée.",
            "destination": destination,
            "properties": [],
            "count": 0,
        }

    if min_price:
        min_price = int(min_price)
    if max_price:
        max_price = int(max_price)

    is_valid, err_msg = valid_params(destination, check_in_date, check_out_date, currency,
                                     adults, children, children_ages, min_price, max_price,
                                     price_basis, min_rating, min_hotel_class, max_hotel_class,
                                     property_types, amenities, min_bedrooms, min_bathrooms, sort_by)

    if not is_valid:
        return { "error": err_msg, "tool": "search_housing" }

    params = define_params(api_key, destination, check_in_date, check_out_date, currency, adults,
                           children, children_ages, min_price, max_price, min_rating, min_hotel_class,
                           max_hotel_class, property_types, amenities, free_cancellation, special_offers,
                           eco_certified, vacation_rentals, min_bedrooms, min_bathrooms, sort_by)

    try:
        res = requests.get(
            SERPAPI_API_URL,
            params=params,
            timeout=30,
        )

        try:
            res.raise_for_status()
        except Exception as e:
            return { "error": e, "tool": "search_housing" }
        response = res.json()
    except requests.Timeout:
        return {
            "status": "error",
            "message": "La recherche de logements a dépassé le délai autorisé.",
            "destination": destination,
            "properties": [],
            "count": 0,
        }
    except requests.RequestException as exc:
        return {
            "status": "error",
            "message": f"Erreur lors de l'appel à SerpAPI : {str(exc)}",
            "destination": destination,
            "properties": [],
            "count": 0,
        }
    except ValueError:
        return {
            "status": "error",
            "message": "SerpAPI a renvoyé une réponse JSON invalide.",
            "destination": destination,
            "properties": [],
            "count": 0,
        }

    if response.get("error"):
        return {
            "status": "error",
            "message": response["error"],
            "destination": destination,
            "properties": [],
            "count": 0,
        }

    return format_response(response, destination, check_in_date, check_out_date, currency, min_price,
                           max_price, price_basis, min_rating, min_hotel_class, max_hotel_class, free_cancellation, sort_by)

def fail(err_msg: str):
    return False, err_msg

def passed():
    return True, ""

def validate_date(value: str, field_name: str) -> tuple[date | None, str]:
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        return None, f"{field_name} doit être au format YYYY-MM-DD."

    return parsed, ""


def parse_price(value) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return int(value)

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")

        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", cleaned)

        if match:
            return int(match.group(1))

    return None


def get_property_price(property_data: dict, price_basis: str):
    if price_basis == "total":
        price_data = property_data.get("total_rate", {})
    else:
        price_data = property_data.get("rate_per_night", {})

    if not isinstance(price_data, dict):
        return None

    extracted = price_data.get("extracted_lowest")

    if extracted is not None:
        return parse_price(extracted)

    return parse_price(price_data.get("lowest"))


def normalise_property(property_data: dict, price_basis: str) -> dict:
    nightly_price = get_property_price(property_data, "nightly")

    total_price = get_property_price(property_data, "total")

    hotel_class = property_data.get("extracted_hotel_class")

    if hotel_class is None:
        hotel_class = property_data.get("hotel_class")

    if isinstance(hotel_class, str):
        match = re.search(r"(\d+)", hotel_class)
        hotel_class = int(match.group(1)) if match else None

    if not isinstance(hotel_class, (int, float)):
        hotel_class = None

    property_type = property_data.get("type")

    if not property_type:
        property_type = "vacation rental" if property_data.get("property_token") else None

    prices = []

    for offer in property_data.get("prices", []) or []:
        if not isinstance(offer, dict):
            continue

        offer_nightly = offer.get("rate_per_night") or {}
        offer_total = offer.get("total_rate") or {}

        prices.append({
            "provider": offer.get("source"),
            "price_per_night": parse_price(offer_nightly.get("extracted_lowest")),
            "total_price": parse_price(offer_total.get("extracted_lowest")),
            "currency_note": "Les montants utilisent la devise de la recherche."
        })

    return {
        "name": property_data.get("name"),
        "type": property_type,
        "description": property_data.get("description"),
        "price_per_night": nightly_price,
        "total_price": total_price,
        "rating": parse_price(property_data.get("overall_rating")),
        "reviews_count": property_data.get("reviews"),
        "hotel_class": hotel_class,
        "address": property_data.get("address"),
        "amenities": property_data.get("amenities") or [],
        "excluded_amenities": property_data.get("excluded_amenities") or [],
        "free_cancellation": property_data.get("free_cancellation"),
        "check_in_time": property_data.get("check_in_time"),
        "check_out_time": property_data.get("check_out_time"),
        "thumbnail": property_data.get("thumbnail") or ((property_data.get("images") or [{}])[0].get("thumbnail")),
        "link": property_data.get("link"),
        "prices": prices,
    }

def valid_params(
    destination: str,
    check_in_date: str,
    check_out_date: str,
    currency: str,
    adults: int,
    children: int,
    children_ages: list[int] | None,
    min_price: int | None,
    max_price: int | None,
    price_basis: str,
    min_rating: float | None,
    min_hotel_class: int | None,
    max_hotel_class: int | None,
    property_types: list[str] | None,
    amenities: list[str] | None,
    min_bedrooms: int | None,
    min_bathrooms: int | None,
    sort_by: str
):
    if not isinstance(destination, str) or not destination.strip():
        return fail("destination ne peut pas être vide.")

    check_in, err_msg = validate_date(check_in_date, "check_in_date",)

    if check_in is None:
        return fail(err_msg)

    check_out, err_msg = validate_date(check_out_date, "check_out_date",)

    if check_out is None:
        return fail(err_msg)

    if check_out <= check_in:
        return fail("check_out_date doit être postérieure à check_in_date.")

    if not currency:
        return fail("La monnaie est requise.")

    if not currency in ALL_CURRENCY.values():
        return fail(f"La monnaie {currency} n'existe pas, toutes les monnaies existante sont : {ALL_CURRENCY}")

    if adults < 1:
        return fail("Il faut au moins un adulte.")

    if children < 0:
        return fail("children ne peut pas être négatif.")

    if children_ages is not None:
        if len(children_ages) != children:
            return fail("Le nombre de children_ages doit correspondre à children.")

        if any(not isinstance(age, int) or age < 1 or age > 17 for age in children_ages):
            return fail("Les âges des enfants doivent être compris entre 1 et 17.")

    if price_basis not in {"total", "nightly"}:
        return fail("price_basis doit être 'total' ou 'nightly'.")

    if min_price is not None and min_price < 0:
        return fail("min_price ne peut pas être négatif.")

    if max_price is not None and max_price < 0:
        return fail("max_price ne peut pas être négatif.")

    if (min_price is not None and max_price is not None and min_price > max_price):
        return fail("min_price ne peut pas être supérieur à max_price.")

    if min_rating is not None and not 0 <= min_rating <= 5:
        return fail("min_rating doit être compris entre 0 et 5.")

    if min_hotel_class is not None and min_hotel_class not in range(2, 6):
        return fail("min_hotel_class doit être entre 2 et 5.")

    if max_hotel_class is not None and max_hotel_class not in range(2, 6):
        return fail("max_hotel_class doit être entre 2 et 5.")

    if (min_hotel_class is not None and max_hotel_class is not None and min_hotel_class > max_hotel_class):
        return fail("min_hotel_class ne peut pas dépasser max_hotel_class.")

    if min_bedrooms is not None and min_bedrooms < 0:
        return fail("min_bedrooms ne peut pas être négatif.")

    if min_bathrooms is not None and min_bathrooms < 0:
        return fail("min_bathrooms ne peut pas être négatif.")

    if sort_by not in SORTING_OPTIONS:
        return fail(f"sort_by doit être l'une des valeurs : {list(SORTING_OPTIONS.keys())}")

    if property_types and property_types not in ALL_PROPERTY_TYPES:
        return fail(f"Property type invalide: {property_types}. Valeurs acceptées : {list(ALL_PROPERTY_TYPES.keys())}")

    if amenities and amenities not in ALL_AMENITIES:
        return fail(f"Amenitie invalide: {amenities}. Valeurs acceptées : {list(ALL_AMENITIES.keys())}")

    return passed()

def define_params(
    api_key,
    destination: str,
    check_in_date: str,
    check_out_date: str,
    currency: str,
    adults: int,
    children: int,
    children_ages: list[int] | None,
    min_price: int | None,
    max_price: int | None,
    min_rating: float | None,
    min_hotel_class: int | None,
    max_hotel_class: int | None,
    property_types: list[str] | None,
    amenities: list[str] | None,
    free_cancellation: bool,
    special_offers: bool,
    eco_certified: bool,
    vacation_rentals: bool,
    min_bedrooms: int | None,
    min_bathrooms: int | None,
    sort_by: str
):
    params = {
        "engine": "google_hotels",
        "q": destination.strip(),

        "check_in_date": check_in_date,
        "check_out_date": check_out_date,

        "currency": currency.upper(),

        "adults": adults,
        "children": children,

        "api_key": api_key,
    }

    if children_ages:
        params["children_ages"] = ",".join(str(age) for age in children_ages)

    if min_price is not None:
        params["min_price"] = min_price

    if max_price is not None:
        params["max_price"] = max_price

    if min_rating is not None:
        supported_ratings = [threshold for threshold in RATING_OPTIONS if threshold <= min_rating]

        if supported_ratings:
            params["rating"] = RATING_OPTIONS[max(supported_ratings)]

    if property_types:
        params["property_types"] = ",".join(str(ALL_PROPERTY_TYPES[value]) for value in property_types)

    if amenities:
        params["amenities"] = ",".join(str(ALL_AMENITIES[value]) for value in amenities)

    if min_hotel_class is not None or max_hotel_class is not None:
        low = min_hotel_class or 2
        high = max_hotel_class or 5

        params["hotel_class"] = ",".join(str(stars) for stars in range(low, high + 1))

    if free_cancellation:
        params["free_cancellation"] = "true"

    if special_offers:
        params["special_offers"] = "true"

    if eco_certified:
        params["eco_certified"] = "true"

    if vacation_rentals:
        params["vacation_rentals"] = "true"

        if min_bedrooms is not None:
            params["bedrooms"] = min_bedrooms

        if min_bathrooms is not None:
            params["bathrooms"] = min_bathrooms

    sort_value = SORTING_OPTIONS[sort_by]

    if sort_value is not None:
        params["sort_by"] = sort_value

    return params

def format_response(
        response,
        destination: str,
        check_in_date: str,
        check_out_date: str,
        currency: str,
        min_price: int | None,
        max_price: int | None,
        price_basis: str,
        min_rating: float | None,
        min_hotel_class: int | None,
        max_hotel_class: int | None,
        free_cancellation: bool,
        sort_by: str
):
    raw_properties = response.get("properties") or []

    properties = []

    for raw_property in raw_properties:
        if not isinstance(raw_property, dict):
            continue

        prop = normalise_property(raw_property, price_basis)

        prop["currency"] = currency.upper()

        price = prop["total_price" if price_basis == "total" else "price_per_night"]

        if price is not None:
            if min_price is not None and price < min_price:
                continue

            if max_price is not None and price > max_price:
                continue

        rating = prop["rating"]

        if min_rating is not None:
            if rating is None or rating < min_rating:
                continue

        hotel_class = prop["hotel_class"]

        if min_hotel_class is not None:
            if hotel_class is None or hotel_class < min_hotel_class:
                continue

        if max_hotel_class is not None:
            if hotel_class is None or hotel_class > max_hotel_class:
                continue

        if free_cancellation and prop["free_cancellation"] is not True:
            continue

        properties.append(prop)

    if sort_by == "price":
        properties.sort(
            key=lambda item: item["total_price"] if price_basis == "total" else item["price_per_night"]
            if (item["total_price"] if price_basis == "total" else item["price_per_night"]) is not None
            else float("inf")
        )

    elif sort_by == "rating":
        properties.sort(key=lambda item: item["rating"] if item["rating"] is not None else -1, reverse=True)

    elif sort_by == "most_reviewed":
        properties.sort(key=lambda item: item["reviews_count"] or 0, reverse=True)

    properties = properties[:MAX_HOUSINGS]

    return {
        "status": "success" if properties else "no_results",
        "destination": destination,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "nights": (date.fromisoformat(check_out_date) - date.fromisoformat(check_in_date)).days,
        "currency": currency.upper(),
        "properties_count": len(properties),
        "properties": json.dumps(properties)
    }
