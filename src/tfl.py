from req import Request, Response, SimpleResponse
from util import logging, getTokenFromFile
import html
import os
from urllib.parse import quote

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None

import requests

# TfL Unified API. Serves anonymous requests at a lower rate limit; an app key
# from res/tfl.key is attached when available.
API_BASE = "https://api.tfl.gov.uk"

# Modes served by the tube command; the bus command serves mode 'bus'.
TUBE_MODES = ("tube", "dlr", "overground", "elizabeth-line", "tram")

MODE_EMOJI = {
    "tube": "🚇",
    "dlr": "🚇",
    "overground": "🚇",
    "elizabeth-line": "🚇",
    "tram": "🚇",
    "bus": "🚌",
}

SESSION = requests.Session()

TFL_KEY_FILE = os.path.join(os.path.dirname(__file__), "../res/tfl.key")


def apiAppKey():
    try:
        return getTokenFromFile("tfl.key")
    except Exception:
        return ""


def _get(path, params):
    """GET a TfL API path, attaching the app key when one is configured.
    A rejected (invalid/expired) key makes the API return HTTP 429, so fall
    back to an anonymous request in that case."""
    key = apiAppKey()
    if key:
        params = dict(params, app_key=key)
    resp = SESSION.get(f"{API_BASE}{path}", params=params, timeout=15)
    if resp.status_code == 429 and key:
        logging.warning("TfL rejected the app key from res/tfl.key; retrying anonymously. Update or remove the key.")
        params.pop("app_key")
        resp = SESSION.get(f"{API_BASE}{path}", params=params, timeout=15)
    return resp


class TflRequest(Request):
    def supportedCommands(self):
        return {"tube", "bus"}

    def helpString(self):
        return ("tube <station>: Live departures for tube, DLR, Overground, Elizabeth line & tram, e.g. tube victoria\n"
                "bus <stop name>: Live bus stop departures, e.g. bus aldwych")

    def handleRequest(self):
        command = self.command()
        query = " ".join(self.requestParams[1:]).strip().lower()
        if not query:
            example = "tube victoria" if command == "tube" else "bus aldwych"
            return SimpleResponse(f"Which stop? e.g. <code>{example}</code>", parse_mode="HTML")

        logging.info(f"TfL request ({command}): {query}")
        modes = TUBE_MODES if command == "tube" else ("bus",)
        try:
            matches = searchStopPoints(query, modes)
        except Exception as e:
            logging.error(f"TfL search failed for '{query}': {e}")
            return SimpleResponse(f"Sorry, unable to reach TfL right now ({html.escape(str(e))}).", parse_mode="HTML")

        if not matches:
            label = "tube, DLR or Overground station" if command == "tube" else "bus stop"
            return SimpleResponse(f"I could not find a {label} called '{html.escape(query.title())}'.", parse_mode="HTML")

        # A case-insensitive exact name match wins outright; otherwise the
        # search is fuzzy, so let the user pick.
        exact = [m for m in matches if m["name"].lower() == query]
        if len(exact) == 1:
            return TflRequest.getArrivalsForStop(command, exact[0]["id"])
        if len(matches) == 1:
            return TflRequest.getArrivalsForStop(command, matches[0]["id"])

        return TflStopDisambiguationResponse(command, query, matches[:8])

    @staticmethod
    def getArrivalsForStop(mode, stop_id):
        """Fetch live arrivals for a stop id, expanding TfL hub ids into
        their child stops (hubs like HUBVIC serve no arrivals themselves)."""
        emoji = MODE_EMOJI.get(mode, "🚇")
        try:
            stop_ids = resolveStopIds(stop_id, mode)
            arrivals = []
            for sid in stop_ids:
                arrivals.extend(fetchArrivals(sid))
            arrivals.sort(key=lambda a: a.get("timeToStation", 0))
            name = arrivals[0].get("stationName") if arrivals else stopNameForId(stop_id)
            return TflArrivalsResponse(name or stop_id, stop_id, arrivals, mode)
        except Exception as e:
            logging.error(f"Error fetching TfL arrivals for {stop_id}: {e}")
            return SimpleResponse(f"Sorry, unable to fetch {emoji} departures right now ({html.escape(str(e))}).", parse_mode="HTML")


def searchStopPoints(query, modes):
    params = {"useStopsOnly": "true", "maxResults": "15"}
    resp = _get(f"/StopPoint/Search/{quote(query)}", params)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from TfL search")
    # The search endpoint applies its modes filter loosely, so filter the
    # returned matches client-side against the command's supported modes.
    wanted = set(modes)
    matches = [m for m in resp.json().get("matches", []) if set(m.get("modes") or []) & wanted]
    matches.sort(key=lambda m: (not m["name"].lower().startswith(query), len(m["name"])))
    return matches


def resolveStopIds(stop_id, mode):
    """Return the stop ids to poll for a TfL stop or hub id."""
    if not stop_id.upper().startswith("HUB"):
        return [stop_id]
    resp = _get(f"/StopPoint/{quote(stop_id)}", {})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from TfL stop lookup")
    wanted = set(TUBE_MODES if mode == "tube" else ("bus",))
    children = [
        child["id"]
        for child in resp.json().get("children", [])
        if set(child.get("modes") or []) & wanted
    ]
    return children[:4] if children else [stop_id]


def fetchArrivals(stop_id):
    resp = _get(f"/StopPoint/{quote(stop_id)}/Arrivals", {})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from TfL arrivals")
    return resp.json()


def stopNameForId(stop_id):
    try:
        resp = _get(f"/StopPoint/{quote(stop_id)}", {})
        if resp.status_code == 200:
            return resp.json().get("commonName")
    except Exception as e:
        logging.debug(f"Stop name lookup failed for {stop_id}: {e}")
    return None


class TflStopDisambiguationResponse(Response):
    """Offers clickable buttons when a TfL stop search is ambiguous."""

    def __init__(self, mode, query, matches):
        self.mode = mode
        self.query = query
        self.matches = matches

    def _emoji(self):
        return MODE_EMOJI.get(self.mode, "🚇")

    def toTelegramString(self):
        label = "stops" if self.mode == "bus" else "stations"
        return [f"{self._emoji()} Multiple {label} match <b>'{html.escape(self.query.title())}'</b>.\nSelect a stop to see live departures:"]

    def replyMarkup(self):
        if not InlineKeyboardMarkup or not InlineKeyboardButton:
            return None
        keyboard = []
        row = []
        for match in self.matches:
            btn_text = f"{self._emoji()} {match['name']}"
            row.append(InlineKeyboardButton(btn_text, callback_data=f"tfl:{self.mode}:{match['id']}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return InlineKeyboardMarkup(keyboard)

    def parseMode(self):
        return "HTML"


class TflArrivalsResponse(Response):
    def __init__(self, stop_name, stop_id, arrivals, mode):
        self.stop_name = stop_name
        self.stop_id = stop_id
        self.arrivals = arrivals
        self.mode = mode

    def _emoji(self):
        return MODE_EMOJI.get(self.mode, "🚇")

    def toTelegramString(self):
        header = f"{self._emoji()} <b>TfL: {html.escape(self.stop_name)}</b>"
        divider = "━━━━━━━━━━━━━━━━━━━━"
        lines = [header, divider]

        if not self.arrivals:
            lines.append("<i>No live departures found for this stop.</i>")
            return lines

        for arrival in self.arrivals[:10]:
            lines.append(self._formatArrival(arrival))
        return lines

    def _formatArrival(self, arrival):
        line = html.escape(str(arrival.get("lineName") or "").strip())
        destination = html.escape(str(arrival.get("destinationName") or "").strip())
        platform = str(arrival.get("platformName") or "").strip()
        mode = arrival.get("modeName") or self.mode
        emoji = MODE_EMOJI.get(mode, self._emoji())

        minutes = max(0, int(arrival.get("timeToStation") or 0) // 60)
        when = "Due" if minutes == 0 else f"{minutes} min"

        line_part = f"<b>{line}</b>" if line else ""
        parts = [p for p in (line_part, destination if destination else None) if p]
        text = f"{emoji} {' → '.join(parts)}"

        details = [when]
        if platform:
            details.append(html.escape(platform))
        text += " • " + " • ".join(details)
        return text

    def replyMarkup(self):
        if not InlineKeyboardMarkup or not InlineKeyboardButton:
            return None
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data=f"tfl:{self.mode}:{self.stop_id}")
        ]])

    def parseMode(self):
        return "HTML"
