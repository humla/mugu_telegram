from req import Request, Response, EmptyResponse, SimpleResponse
from util import logging, getTokenFromFile
import csv
import os
import re
import html
import datetime

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None

try:
    from suds.client import Client
    from suds.sax.element import Element
except ImportError:
    Client = None
    Element = None

_cached_codes = None

# Newer stations not in older National Rail CSV datasets (e.g. Elizabeth line)
EXTRA_STATIONS = {
    'canary wharf': 'CWX',
    'custom house': 'CUS',
    'woolwich': 'WWC',
    'tottenham court road': 'TCR',
    'bond street': 'BDS',
    'barking riverside': 'BGV',
    'battersea power station': 'BAE',
}

def loadStationCodes():
    global _cached_codes
    if _cached_codes is not None:
        return _cached_codes
    codes = {}
    csv_path = os.path.join(os.path.dirname(__file__), "../res/station_code.csv")
    if os.path.exists(csv_path):
        with open(csv_path) as f:
            reader = csv.reader(f)
            next(reader, None)  # skip the header row
            for row in reader:
                for i in range(0, len(row) - 1, 2):
                    name, code = row[i].strip(), row[i + 1].strip()
                    if name and code:
                        codes[name.lower()] = code
    codes.update(EXTRA_STATIONS)
    _cached_codes = codes
    return codes


class StationCodeRequest(Request):
    def supportedCommands(self):
        return {'code'}

    MAX_MATCHES = 10

    def helpString(self):
        return "code <station name>: Get the CRS code for a station, e.g. code dartford"

    def handleRequest(self):
        logging.info("Station code request")
        query = " ".join(self.requestParams[1:]).lower()
        if not query:
            return SimpleResponse("Which station? e.g. <code>code dartford</code>", parse_mode="HTML")
        codes = loadStationCodes()

        # Exact station name match
        if query in codes:
            code = codes[query]
            markup = None
            if InlineKeyboardMarkup and InlineKeyboardButton:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"🚆 View Departures ({code})", callback_data=f"train:{code}:")]])
            return SimpleResponse(f"🚉 <b>{query.title()}</b> CRS code is <code>{code}</code>", reply_markup=markup, parse_mode="HTML")

        # Exact CRS code match
        if self.requestParams[1].upper() in codes.values():
            code = self.requestParams[1].upper()
            names = [name.title() for name, c in codes.items() if c == code]
            markup = None
            if InlineKeyboardMarkup and InlineKeyboardButton:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"🚆 View Departures ({code})", callback_data=f"train:{code}:")]])
            return SimpleResponse(f"🚉 Station code <code>{code}</code> is <b>{' / '.join(names)}</b>", reply_markup=markup, parse_mode="HTML")

        # Substring search
        matches = {name: code for name, code in codes.items() if query in name}
        if not matches:
            return SimpleResponse(f"I could not find a station called '{html.escape(query.title())}'.", parse_mode="HTML")

        lines = [f"🚉 Stations matching <b>'{html.escape(query.title())}'</b>:"]
        buttons = []
        row = []
        for name, code in sorted(matches.items())[:StationCodeRequest.MAX_MATCHES]:
            lines.append(f"• <b>{name.title()}</b>: <code>{code}</code>")
            if len(buttons) < 4:  # top 4 buttons
                row.append(InlineKeyboardButton(f"🚆 {name.title()} ({code})", callback_data=f"train:{code}:"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
        if row:
            buttons.append(row)

        if len(matches) > StationCodeRequest.MAX_MATCHES:
            lines.append(f"<i>... and {len(matches) - StationCodeRequest.MAX_MATCHES} more</i>")

        markup = InlineKeyboardMarkup(buttons) if buttons and InlineKeyboardMarkup else None
        return SimpleResponse(lines, reply_markup=markup, parse_mode="HTML")


class StationDisambiguationResponse(Response):
    """Offers clickable buttons when a station search is ambiguous."""
    def __init__(self, query, matches, from_crs="", is_destination=False):
        self.query = query
        self.matches = matches  # dict of {name: code}
        self.from_crs = from_crs
        self.is_destination = is_destination

    def toTelegramString(self):
        title_query = html.escape(self.query.title())
        if self.is_destination and self.from_crs:
            codes = loadStationCodes()
            from_names = [n.title() for n, c in codes.items() if c == self.from_crs]
            origin = from_names[0] if from_names else self.from_crs
            text = f"🚆 Multiple destinations match <b>'{title_query}'</b> from {origin}.\nSelect your destination station:"
        else:
            text = f"🚆 Multiple stations match <b>'{title_query}'</b>.\nSelect a station to see departures:"
        return [text]

    def replyMarkup(self):
        if not InlineKeyboardMarkup or not InlineKeyboardButton:
            return None
        keyboard = []
        row = []
        for name, code in sorted(self.matches.items())[:8]:
            btn_text = f"🚆 {name.title()} ({code})"
            if self.is_destination and self.from_crs:
                cb_data = f"train:{self.from_crs}:{code}"
            else:
                cb_data = f"train:{code}:"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return InlineKeyboardMarkup(keyboard)

    def parseMode(self):
        return "HTML"


class RailRequest(Request):
    def supportedCommands(self):
        return {'train'}    

    def helpString(self):
        return ("train <station name or code>: Get realtime departure for a train station\n"
                "train <station A> to <station B>: Get realtime departures from station A to station B (or separate with a comma).")

    def handleRequest(self):
        logging.info("Rail request: " + " ".join(self.requestParams))
        words = self.requestParams[1:]
        code, to, disambig_resp, error = RailRequest.resolveStationWords(words)
        if disambig_resp:
            return disambig_resp
        if error:
            return SimpleResponse(error, parse_mode="HTML")
        return RailRequest.getDepartureForStation(code, to)

    @staticmethod
    def resolveStationWords(words):
        """Resolve 'train <station> [to <destination>]' or 'train <station> [, <destination>]'
        where either station may be several words.
        Returns: (code, to, disambiguation_response, error_message)
        """
        if not words:
            return None, None, None, ("Which station would you like to check? E.g.:\n"
                                      "• <code>train DFD</code>\n"
                                      "• <code>train dartford to london bridge</code>\n"
                                      "• <code>train dartford, london bridge</code>")

        # Comma-separated: train station1, station2 (or train station1, to station2)
        if any(',' in word for word in words):
            parts = [part.strip() for part in " ".join(words).split(',') if part.strip()]
            if len(parts) != 2:
                return None, None, None, "Please use one comma to separate the two stations, e.g. <code>train dartford to london bridge</code>"
            origin_query = parts[0]
            dest_query = parts[1]
            if dest_query.lower().startswith("to "):
                dest_query = dest_query[3:].strip()

            code, matches1, err1 = RailRequest.resolveStation(origin_query)
            if err1:
                return None, None, None, err1
            if matches1:
                return None, None, StationDisambiguationResponse(origin_query, matches1), None

            to, matches2, err2 = RailRequest.resolveStation(dest_query)
            if err2:
                return None, None, None, err2
            if matches2:
                return None, None, StationDisambiguationResponse(dest_query, matches2, from_crs=code, is_destination=True), None

            return code, to, None, None

        # 'to' separated: train station1 to station2
        words_lower = [w.lower() for w in words]
        if 'to' in words_lower:
            to_idx = words_lower.index('to')
            origin_words = words[:to_idx]
            dest_words = words[to_idx + 1:]
            if not origin_words:
                return None, None, None, "Please specify where you are travelling from, e.g. <code>train dartford to london bridge</code>"
            if not dest_words:
                return None, None, None, "Please specify where you are travelling to, e.g. <code>train dartford to london bridge</code>"

            origin_query = " ".join(origin_words)
            dest_query = " ".join(dest_words)

            code, matches1, err1 = RailRequest.resolveStation(origin_query)
            if err1:
                return None, None, None, err1
            if matches1:
                return None, None, StationDisambiguationResponse(origin_query, matches1), None

            to, matches2, err2 = RailRequest.resolveStation(dest_query)
            if err2:
                return None, None, None, err2
            if matches2:
                return None, None, StationDisambiguationResponse(dest_query, matches2, from_crs=code, is_destination=True), None

            return code, to, None, None

        # Try word splits for two stations without a comma or 'to' (e.g. train slade green london bridge)
        for k in range(0, len(words) - 1):
            code, m1, err1 = RailRequest.resolveStation(" ".join(words[:k + 1]))
            if code:
                to, m2, err2 = RailRequest.resolveStation(" ".join(words[k + 1:]))
                if to:
                    return code, to, None, None

        # Single station query
        query_str = " ".join(words)
        code, matches, err = RailRequest.resolveStation(query_str)
        if code:
            return code, "", None, None
        if matches:
            return None, None, StationDisambiguationResponse(query_str, matches), None
        return None, None, None, err

    @staticmethod
    def resolveStation(input_text):
        """Turn a station name or CRS code into a CRS code.
        Returns:
            (crs_code, None, None) on unique match
            (None, matches_dict, None) on multiple matches
            (None, None, error_string) on no match
        """
        query = input_text.strip().lower()
        if not query:
            return None, None, "Please specify a station name or code."
        codes = loadStationCodes()
        if query in codes:
            return codes[query], None, None
        if input_text.strip().upper() in codes.values():
            return input_text.strip().upper(), None, None

        matches = {name: code for name, code in codes.items() if query in name}
        if len(matches) == 1:
            return next(iter(matches.values())), None, None
        if not matches:
            return None, None, f"I could not find a station called '{html.escape(input_text)}'. Try the 3-letter station code, e.g. <code>train DFD</code>"
        return None, matches, None
    
    @staticmethod
    def apiKey():
        return getTokenFromFile('rail.key')

    @staticmethod
    def getDepartureForStation(stationId, to=""):
        try:
            api_address = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2021-11-01"
            api_token = RailRequest.apiKey()

            client = Client(api_address)
            header = Element("AccessToken").append(Element("TokenValue").setText(api_token))
            client.set_options(soapheaders=header)

            result = client.service.GetDepBoardWithDetails(
                numRows="10",
                crs=stationId,
                filterCrs=to if to else None,
                filterType="to",
                timeOffset="0",
                timeWindow="120"
            )
            return RailResponse(result, to)
        except Exception as e:
            logging.error(f"Error fetching rail departures for {stationId} -> {to}: {e}")
            return SimpleResponse(f"Sorry, unable to fetch train departures right now ({html.escape(str(e))}).", parse_mode="HTML")


class RailResponse(Response):
    def __init__(self, stationBoardWithDetails, filterTo=""):
        self.details = stationBoardWithDetails
        self.filterTo = filterTo
    
    def destinationLabel(self):
        if not self.filterTo:
            return ""
        codes = loadStationCodes()
        names = [name.title() for name, code in codes.items() if code == self.filterTo]
        name = names[0] if names else self.filterTo
        return f" ➔ {html.escape(name)} ({html.escape(self.filterTo)})"

    def toTelegramString(self):
        services = getattr(self.details, 'trainServices', None)
        services = services.service if services else []
        locationName = html.escape(getattr(self.details, 'locationName', 'Unknown'))
        crs = html.escape(getattr(self.details, 'crs', ''))
        header = f"🚆 <b>National Rail: {locationName} ({crs}){self.destinationLabel()}</b>"
        divider = "━━━━━━━━━━━━━━━━━━━━"
        lines = [header, divider]

        # Check for NRCC disruption alerts on the board
        nrcc = getattr(self.details, 'nrccMessages', None)
        if nrcc and hasattr(nrcc, 'message') and nrcc.message:
            for msg in nrcc.message[:1]:
                clean_msg = re.sub(r'<[^>]+>', '', str(msg)).strip()
                clean_msg = re.sub(r'\s+', ' ', clean_msg)
                if clean_msg:
                    if len(clean_msg) > 140:
                        clean_msg = clean_msg[:137] + "..."
                    lines.append(f"⚠️ <i>{html.escape(clean_msg)}</i>\n")

        if not services:
            lines.append("<i>No departing services found in the next 2 hours.</i>")
            return lines

        ref_dt = getattr(self.details, 'generatedAt', None)

        for s in services[:10]:
            lines.append(ServiceItem(s, ref_dt=ref_dt).toFormattedString())

        return lines

    def replyMarkup(self):
        if not InlineKeyboardMarkup or not InlineKeyboardButton:
            return None
        crs = getattr(self.details, 'crs', '')
        buttons = []
        if self.filterTo:
            buttons.append([
                InlineKeyboardButton("🔄 Refresh", callback_data=f"train:{crs}:{self.filterTo}"),
                InlineKeyboardButton("🔁 Reverse", callback_data=f"train:{self.filterTo}:{crs}")
            ])
        else:
            buttons.append([
                InlineKeyboardButton("🔄 Refresh", callback_data=f"train:{crs}:")
            ])
        return InlineKeyboardMarkup(buttons)

    def parseMode(self):
        return "HTML"


def get_ref_time(ref_dt=None):
    if isinstance(ref_dt, datetime.datetime):
        try:
            local_dt = ref_dt.astimezone() if ref_dt.tzinfo else ref_dt
            return local_dt.hour, local_dt.minute
        except Exception:
            return ref_dt.hour, ref_dt.minute
    now = datetime.datetime.now()
    return now.hour, now.minute


def get_relative_minute_string(target_time_str, ref_dt=None):
    """If target_time_str (HH:MM) is within the next 60 minutes, return '(in X mins)'.
    For departures 1 hour or more away, returns empty string."""
    if not target_time_str or not re.match(r"^\d{1,2}:\d{2}$", target_time_str):
        return ""
    try:
        t_hour, t_min = map(int, target_time_str.split(":"))
        ref_hour, ref_min = get_ref_time(ref_dt)

        diff = (t_hour * 60 + t_min) - (ref_hour * 60 + ref_min)
        # Handle midnight rollover (e.g. ref=23:55, target=00:15 -> diff = 20)
        if diff < -12 * 60:
            diff += 24 * 60
        elif diff > 12 * 60:
            diff -= 24 * 60

        # Only display for departures less than 1 hour away (0 to 59 minutes)
        if diff <= 0 and diff >= -2:
            return "(in 0 mins)"
        elif diff == 1:
            return "(in 1 min)"
        elif 1 < diff < 60:
            return f"(in {diff} mins)"
        return ""
    except Exception:
        return ""


class ServiceItem:
    def __init__(self, serviceItem, ref_dt=None):
        logging.debug(serviceItem)
        self.std = getattr(serviceItem, 'std', '')
        self.etd = getattr(serviceItem, 'etd', '')
        self.isCancelled = getattr(serviceItem, 'isCancelled', False) or (self.etd.lower() == 'cancelled')
        self.cancelReason = getattr(serviceItem, 'cancelReason', None)
        self.delayReason = getattr(serviceItem, 'delayReason', None)
        self.platform = getattr(serviceItem, 'platform', None)
        self.ref_dt = ref_dt

        # Compute relative minutes for anything less than 1 hour away
        if self.isCancelled:
            self.relative_time = ""
        elif re.match(r"^\d{1,2}:\d{2}$", self.etd):
            self.relative_time = get_relative_minute_string(self.etd, ref_dt=ref_dt)
        else:
            self.relative_time = get_relative_minute_string(self.std, ref_dt=ref_dt)

        dest_loc = getattr(serviceItem, 'destination', None)
        if dest_loc and hasattr(dest_loc, 'location') and dest_loc.location:
            self.destination = dest_loc.location[0].locationName
        else:
            self.destination = "Unknown"

    def toFormattedString(self):
        dest = html.escape(self.destination)
        plat = f" • Plat {html.escape(str(self.platform))}" if self.platform and str(self.platform) != "NA" else ""
        rel = f" {self.relative_time}" if self.relative_time else ""

        if self.isCancelled:
            line = f"🔴 <b>{self.std}</b> <s>{dest}</s>{plat} • <i>Cancelled</i>"
            if self.cancelReason:
                line += f"\n   ↳ <i>{html.escape(self.cancelReason)}</i>"
            return line
        elif self.etd.lower() == "on time":
            return f"🟢 <b>{self.std}</b>{rel} {dest}{plat} • <i>On time</i>"
        else:
            if self.etd.lower() == "delayed":
                line = f"🟡 <b>Delayed</b> (was {self.std}){rel} {dest}{plat}"
            else:
                line = f"🟡 <b>Exp {html.escape(self.etd)}</b> (was {self.std}){rel} {dest}{plat} • <i>Delayed</i>"
            if self.delayReason:
                line += f"\n   ↳ <i>{html.escape(self.delayReason)}</i>"
            return line

    def toString(self):
        rel = f" {self.relative_time}" if self.relative_time else ""
        timing = self.std if self.etd.lower() == "on time" else f"{self.etd} ({self.std})"
        plat = f" P:{self.platform}" if self.platform else ""
        return f"{timing}{rel} {self.destination}{plat}"
