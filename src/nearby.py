from req import Request, Response, SimpleResponse
from util import logging
from rail import loadStationCodes
from tfl import _get, TUBE_MODES
import fuel
import html
import re

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
except ImportError:
    InlineKeyboardButton = InlineKeyboardMarkup = KeyboardButton = ReplyKeyboardMarkup = None

STATION_RADII_M = (1000, 3000)
BUS_RADIUS_M = 400
MAX_STATIONS = 4
MAX_BUS_STOPS = 4
FUEL_RADIUS_MILES = 5

# Buttons on the nearby board open their departures in a new message, so the
# board stays available; main.handleCallback strips this prefix.
NEW_MESSAGE_PREFIX = "new:"

STATION_SUFFIXES = re.compile(r"\s+(rail|underground|dlr|tram|overground)?\s*station$", re.IGNORECASE)


class NearbyRequest(Request):
    """'near' offers a button that shares the user's location; the shared
    location itself arrives as a location message handled by nearbyResponse."""

    def supportedCommands(self):
        return {'near', 'nearby'}

    def helpString(self):
        return "near (or share your location 📍): Nearest train stations, tube and bus stops with live departures, and the cheapest fuel nearby"

    def handleRequest(self):
        markup = None
        if ReplyKeyboardMarkup:
            markup = ReplyKeyboardMarkup([[KeyboardButton("📍 Send my location", request_location=True)]],
                                         one_time_keyboard=True, resize_keyboard=True)
        return SimpleResponse("Tap the button below (or use 📎 → Location) to share where you are.", reply_markup=markup)


def displayName(common_name):
    return STATION_SUFFIXES.sub("", common_name).strip()


def crsForStation(name):
    """CRS code for a TfL station name such as 'Abbey Wood (London)'."""
    codes = loadStationCodes()
    key = name.lower()
    return codes.get(key) or codes.get(re.sub(r"\s*\(.*\)$", "", key))


def formatDistance(metres):
    return f"{metres:.0f} m" if metres < 1000 else f"{metres / 1609.34:.1f} mi"


def findStations(lat, lon):
    """Nearest rail/tube stations as (label, distance_m, callback) tuples.
    A station served by both National Rail and TfL modes gets both boards."""
    # TfL's response time grows with the number of stops in the circle (a 3 km
    # search in central London takes over 30 s), so only widen when needed.
    for radius in STATION_RADII_M:
        resp = _get("/StopPoint", {"lat": lat, "lon": lon, "radius": radius, "returnLines": "false",
                                   "stopTypes": "NaptanRailStation,NaptanMetroStation"})
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} from TfL stop search")
        stops = resp.json().get("stopPoints", [])
        if len(stops) >= 2:
            break
    results, seen = [], set()
    for stop in stops:
        name = displayName(stop["commonName"])
        modes = set(stop.get("modes") or [])
        boards = []
        if "national-rail" in modes:
            crs = crsForStation(name)
            if crs:
                boards.append((f"🚆 {name}", f"train:{crs}:"))
        if modes & set(TUBE_MODES):
            boards.append((f"🚇 {name}", f"tfl:tube:{stop['id']}"))
        for label, callback in boards:
            if label not in seen:
                seen.add(label)
                results.append((label, stop.get("distance", 0), callback))
    return results[:MAX_STATIONS]


def findBusStops(lat, lon):
    """Nearest TfL-served bus stops as (label, distance_m, callback, towards)."""
    resp = _get("/StopPoint", {"lat": lat, "lon": lon, "radius": BUS_RADIUS_M, "returnLines": "true",
                               "stopTypes": "NaptanPublicBusCoachTram", "modes": "bus"})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from TfL stop search")
    results = []
    for stop in resp.json().get("stopPoints", []):
        if not stop.get("lines"):
            continue  # not served by TfL buses, so no live arrivals
        name = " ".join(stop["commonName"].split())
        letter = stop.get("stopLetter")
        if letter and not letter.startswith("->"):
            name += f" ({letter})"
        towards = next((p["value"] for p in stop.get("additionalProperties", []) if p.get("key") == "Towards"), "")
        results.append((f"🚌 {name}", stop.get("distance", 0), f"tfl:bus:{stop['id']}", towards))
    return results[:MAX_BUS_STOPS]


def findFuel(lat, lon):
    """Cheapest-fuel summary lines, or [] when Fuel Finder is not configured or unavailable."""
    _, secret = fuel.loadCredentials()
    if not secret:
        return []
    try:
        stations = fuel.stationsNear(lat, lon, FUEL_RADIUS_MILES)
    except fuel.FuelFinderError as e:
        logging.error(f"Fuel Finder error for nearby request: {e}")
        return []
    return fuel.cheapestLines(lat, lon, stations)


def nearbyResponse(lat, lon):
    logging.info(f"Nearby request at {lat:.4f},{lon:.4f}")
    results = []
    for find in (findStations, findBusStops):
        try:
            results.append(find(lat, lon))
        except Exception as e:
            logging.error(f"TfL nearby search ({find.__name__}) failed: {e}")
            results.append([])
    return NearbyResponse(lat, lon, *results, findFuel(lat, lon))


class NearbyResponse(Response):
    def __init__(self, lat, lon, stations, bus_stops, fuel_lines):
        self.lat = lat
        self.lon = lon
        self.stations = stations
        self.bus_stops = bus_stops
        self.fuel_lines = fuel_lines

    def toTelegramString(self):
        sections = []
        if self.stations:
            sections.append(["<b>Stations</b>"] + [
                f"{html.escape(label)} · {formatDistance(d)}" for label, d, _ in self.stations])
        if self.bus_stops:
            sections.append(["<b>Bus stops</b>"] + [
                f"{html.escape(label)}{' → ' + html.escape(towards) if towards else ''} · {formatDistance(d)}"
                for label, d, _, towards in self.bus_stops])
        if self.fuel_lines:
            sections.append(["<b>Fuel</b>"] + self.fuel_lines)
        if not sections:
            sections.append(["<i>I could not find any stations, bus stops or fuel prices near here.</i>"])
        else:
            sections.append(["<i>Tap a button for live departures or prices.</i>"])

        lines = ["📍 <b>Near you</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for i, section in enumerate(sections):
            if i:
                lines.append("")
            lines.extend(section)
        return lines

    def replyMarkup(self):
        if not InlineKeyboardMarkup:
            return None
        buttons = [InlineKeyboardButton(label, callback_data=NEW_MESSAGE_PREFIX + callback)
                   for label, _, callback, *_ in self.stations + self.bus_stops]
        keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        if self.fuel_lines:
            keyboard.append([InlineKeyboardButton(
                "⛽ All fuel prices nearby", callback_data=f"{NEW_MESSAGE_PREFIX}fuel:{self.lat:.4f}:{self.lon:.4f}")])
        return InlineKeyboardMarkup(keyboard) if keyboard else None

    def parseMode(self):
        return "HTML"
