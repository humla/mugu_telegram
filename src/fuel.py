from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging
import datetime
import json
import math
import os
import re
import time

import requests

# UK government Fuel Finder scheme (Motor Fuel Price (Open Data) Regulations 2025).
# Data is served as all-UK baselines in 500-record pages, so results are cached
# locally and refreshed incrementally instead of being fetched per request.
BASE_URL = "https://www.fuel-finder.service.gov.uk"
PAGE_SIZE = 500
BATCH_PAUSE_SECONDS = 2
PRICE_MAX_AGE = datetime.timedelta(minutes=45)
STATION_MAX_AGE = datetime.timedelta(days=7)

CACHE_FILE = os.path.join(os.path.dirname(__file__), "../res/fuel_finder_cache.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "../res/fuelfinder.key")

FUEL_LABELS = {
    "E10": "Unleaded",
    "E5": "Super Unleaded",
    "B7_STANDARD": "Diesel",
    "B7_PREMIUM": "Super Diesel",
    "B10": "Diesel (B10)",
    "HVO": "HVO",
}
SUMMARY_FUELS = ("E10", "B7_STANDARD")  # headline "cheapest near you" grades
NEAREST_COUNT = 8

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_ATTEMPTS = 3


def loadCredentials():
    """Client ID on line 1, client secret on line 2 of res/fuelfinder.key."""
    if not os.path.exists(CREDENTIALS_FILE):
        return None, None
    try:
        with open(CREDENTIALS_FILE) as f:
            lines = [line.strip() for line in f if line.strip()]
    except OSError as e:
        logging.error(f"Could not read Fuel Finder credentials: {e}")
        return None, None
    if len(lines) < 2:
        return None, None
    return lines[0], lines[1]


def parsePrice(raw):
    """Prices arrive as zero-padded decimal strings in pence ('0120.0000').
    Some traders submit pounds instead; values below 2.0 are converted."""
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    if price < 2.0:
        price *= 100
    return round(price, 1)


class FuelFinderError(Exception):
    pass


class FuelFinderClient:
    """Authenticated client for the Fuel Finder public API."""

    def __init__(self):
        self._token = None
        self._token_expiry = 0

    def _getToken(self):
        client_id, client_secret = loadCredentials()
        if not client_id or not client_secret:
            raise FuelFinderError("missing-credentials")
        resp = requests.post(
            f"{BASE_URL}/api/v1/oauth/generate_access_token",
            json={"client_id": client_id, "client_secret": client_secret},
            headers={"accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise FuelFinderError(f"Token request failed with HTTP {resp.status_code}")
        data = resp.json().get("data", {})
        token = data.get("access_token")
        if not token:
            raise FuelFinderError("Token response contained no access_token")
        self._token = token
        self._token_expiry = time.time() + float(data.get("expires_in", 3600)) - 60
        return token

    def _get(self, path, params):
        url = f"{BASE_URL}{path}"
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            if time.time() >= self._token_expiry:
                self._getToken()
            resp = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._token}", "accept": "application/json"},
                timeout=60,
            )
            if resp.status_code in (401, 403):
                # Token rejected or expired: force a fresh one and retry once.
                if attempt < RETRY_ATTEMPTS:
                    self._token_expiry = 0
                    continue
                raise FuelFinderError(f"Fuel Finder rejected our credentials (HTTP {resp.status_code})")
            if resp.status_code == 404:
                # Past the final batch on a paginated endpoint.
                return []
            if resp.status_code in RETRYABLE_STATUS:
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(min(30.0, 2 ** attempt))
                    continue
                raise FuelFinderError(f"Fuel Finder unavailable (HTTP {resp.status_code})")
            if resp.status_code != 200:
                raise FuelFinderError(f"Unexpected HTTP {resp.status_code} from Fuel Finder")
            body = resp.json()
            return body.get("data", body) if isinstance(body, dict) else body
        raise FuelFinderError("Fuel Finder request retries exhausted")

    def _batches(self, path, since=None):
        params = {}
        if since:
            params["effective-start-timestamp"] = since
        batch = 1
        while True:
            batch_params = dict(params, **{"batch-number": str(batch)})
            records = self._get(path, batch_params)
            if not records:
                return
            yield from records
            if len(records) < PAGE_SIZE:
                return
            batch += 1
            time.sleep(BATCH_PAUSE_SECONDS)

    def fetch(self, station_since=None, price_since=None):
        """Fetch stations and prices (full baselines, or incremental with
        effective-start-timestamp). Prices are joined onto their station."""
        stations = {}

        for record in self._batches("/api/v1/pfs", since=station_since):
            stations[record["node_id"]] = {
                "node_id": record["node_id"],
                "brand_name": record.get("brand_name") or record.get("trading_name") or "Unknown",
                "trading_name": record.get("trading_name"),
                "temporary_closure": record.get("temporary_closure", False),
                "permanent_closure": record.get("permanent_closure", False),
                "location": record.get("location") or {},
            }

        for record in self._batches("/api/v1/pfs/fuel-prices", since=price_since):
            station = stations.get(record["node_id"])
            if station is None:
                continue
            station["fuel_prices"] = [
                {"fuel_type": p.get("fuel_type"), "price": p.get("price"),
                 "price_last_updated": p.get("price_last_updated")}
                for p in (record.get("fuel_prices") or [])
            ]

        return list(stations.values())


def loadCache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logging.error(f"Could not read fuel cache: {e}")
        return None


def saveCache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        logging.error(f"Could not save fuel cache: {e}")


def fetchTimestamp(dt):
    """The API's effective-start-timestamp format: 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ensureCache():
    """Return cached station records, fetching or refreshing from the API if needed."""
    cache = loadCache()
    now = datetime.datetime.utcnow()
    client = FuelFinderClient()

    if cache is None:
        logging.info("Fuel Finder: fetching full baseline (first run takes a few minutes)")
        stations = client.fetch()
        if not stations:
            raise FuelFinderError("Fuel Finder returned no stations")
        cache = {"stations_fetched_at": fetchTimestamp(now), "prices_fetched_at": fetchTimestamp(now),
                 "stations": stations}
        saveCache(cache)
        return cache

    fetched = datetime.datetime.strptime(cache["prices_fetched_at"], "%Y-%m-%d %H:%M:%S")
    if now - fetched > PRICE_MAX_AGE:
        logging.info("Fuel Finder: refreshing prices incrementally")
        try:
            updated = client.fetch(station_since=cache["stations_fetched_at"],
                                   price_since=cache["prices_fetched_at"])
            mergeStations(cache["stations"], updated)
            cache["stations_fetched_at"] = fetchTimestamp(now)
            cache["prices_fetched_at"] = fetchTimestamp(now)
            saveCache(cache)
        except FuelFinderError as e:
            logging.error(f"Fuel Finder refresh failed, using cached prices: {e}")

    return cache


def mergeStations(existing, updated):
    by_id = {s["node_id"]: s for s in existing}
    for station in updated:
        if station.get("fuel_prices"):
            if station["node_id"] in by_id:
                by_id[station["node_id"]].update(station)
            else:
                existing.append(station)


def geocodePostcode(postcode):
    """Resolve a full or district postcode to (latitude, longitude, label)."""
    compact = postcode.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z0-9]{2,7}", compact):
        return None
    resp = requests.get(f"https://api.postcodes.io/postcodes/{compact}", timeout=15)
    if resp.status_code == 200:
        result = resp.json()["result"]
        return result["latitude"], result["longitude"], result["postcode"]
    # Not a full postcode: try it as a district (outcode) like DA1
    resp = requests.get(f"https://api.postcodes.io/outcodes/{compact}", timeout=15)
    if resp.status_code == 200:
        result = resp.json()["result"]
        return result["latitude"], result["longitude"], result["outcode"]
    return None


def haversineMiles(lat1, lon1, lat2, lon2):
    radius = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def stationPrice(station, fuel_type):
    for entry in station.get("fuel_prices", []):
        if entry.get("fuel_type") == fuel_type:
            return parsePrice(entry.get("price"))
    return None


def stationUpdatedAt(station):
    dates = [e.get("price_last_updated") for e in station.get("fuel_prices", []) if e.get("price_last_updated")]
    return max(dates) if dates else None


def formatStationLine(station, distance):
    location = station.get("location", {})
    street = location.get("address_line_1") or location.get("city") or ""
    unleaded = stationPrice(station, "E10")
    diesel = stationPrice(station, "B7_STANDARD")
    unleaded_str = f"{unleaded:.1f}p" if unleaded else "-"
    diesel_str = f"{diesel:.1f}p" if diesel else "-"
    return f"• <b>{html_escape(station.get('brand_name', 'Unknown'))}</b>, {html_escape(street)} ({distance:.1f} mi): U {unleaded_str} · D {diesel_str}"


def html_escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cheapestLines(lat, lon, stations):
    """One '🟢 Cheapest ...' line per headline fuel grade among the given stations."""
    lines = []
    for fuel_type in SUMMARY_FUELS:
        priced = [(stationPrice(s, fuel_type), s) for s in stations]
        priced = [(p, s) for p, s in priced if p is not None]
        if priced:
            price, station = min(priced, key=lambda pair: pair[0])
            distance = haversineMiles(lat, lon, float(station["location"]["latitude"]),
                                      float(station["location"]["longitude"]))
            label = FUEL_LABELS.get(fuel_type, fuel_type)
            lines.append(
                f"🟢 Cheapest {label}: <b>{price:.1f}p</b> — {html_escape(station.get('brand_name', ''))} ({distance:.1f} mi)")
    return lines


class FuelFinderResponse(Response):
    def __init__(self, postcode_label, lat, lon, stations):
        self.postcode_label = postcode_label
        self.lat = lat
        self.lon = lon
        self.stations = stations

    def toTelegramString(self):
        lines = [f"⛽ <b>Fuel prices near {html_escape(self.postcode_label)}</b>",
                 "━━━━━━━━━━━━━━━━━━━━"]

        lines.extend(cheapestLines(self.lat, self.lon, self.stations))

        lines.append("")
        lines.append("<b>Nearest stations:</b>")
        for station in self.stations[:NEAREST_COUNT]:
            distance = haversineMiles(self.lat, self.lon, float(station["location"]["latitude"]),
                                      float(station["location"]["longitude"]))
            lines.append(formatStationLine(station, distance))

        updated = stationUpdatedAt(self.stations[0]) if self.stations else None
        if updated:
            lines.append("")
            lines.append(f"<i>Source: Fuel Finder (gov.uk), last price update {updated[:10]}. Always check the forecourt price.</i>")
        return lines

    def parseMode(self):
        return "HTML"


class FuelPriceRequest(Request):
    def supportedCommands(self):
        return {'fuel'}

    def helpString(self):
        return ("fuel <postcode>: UK fuel prices near a postcode from the government Fuel Finder scheme, e.g. fuel DA1 or fuel 'SE1 7PB'\n"
                "fuel : Same, using the default postcode DA1")

    def handleRequest(self):
        logging.info("Fuel price request (Fuel Finder)")
        raw = " ".join(self.requestParams[1:]).strip() if len(self.requestParams) > 1 else "DA1"

        _, client_secret = loadCredentials()
        if not client_secret:
            return SimpleResponse(
                "⛽ The <b>Fuel Finder</b> API needs credentials before I can fetch fuel prices.\n\n"
                "1. Register at <a href=\"https://www.fuel-finder.service.gov.uk/\">fuel-finder.service.gov.uk</a> "
                "(needs a GOV.UK One Login)\n"
                "2. Create an application as an <i>information recipient</i>\n"
                "3. Put the client ID on line 1 and client secret on line 2 of <code>res/fuelfinder.key</code>",
                parse_mode="HTML")

        if not re.fullmatch(r"[a-z0-9 ]{2,9}", raw.lower()):
            return SimpleResponse(f"'{raw}' does not look like a postcode. Try e.g. fuel DA1 or fuel SE1 7PB")

        place = geocodePostcode(raw)
        if place is None:
            return SimpleResponse(f"I could not find the postcode '{html_escape(raw)}'.")

        lat, lon, label = place
        return fuelPricesNear(lat, lon, label)


def stationsNear(lat, lon, max_miles=10):
    """Open fuel stations within max_miles of a point, nearest first.
    Raises FuelFinderError when the price data cannot be loaded."""
    stations = []
    for station in ensureCache()["stations"]:
        if station.get("temporary_closure") or station.get("permanent_closure"):
            continue
        location = station.get("location", {})
        try:
            slat, slon = float(location["latitude"]), float(location["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = haversineMiles(lat, lon, slat, slon)
        if distance <= max_miles:
            stations.append((distance, station))
    stations.sort(key=lambda pair: pair[0])
    return [s for _, s in stations]


def fuelPricesNear(lat, lon, label):
    try:
        stations = stationsNear(lat, lon)
    except FuelFinderError as e:
        logging.error(f"Fuel Finder error: {e}")
        return SimpleResponse("Sorry, I could not fetch fuel prices from Fuel Finder right now.")
    if not stations:
        return SimpleResponse(f"I found no fuel stations within 10 miles of {html_escape(label)}.")
    return FuelFinderResponse(label, lat, lon, stations[:NEAREST_COUNT])
