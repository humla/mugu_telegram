from req import Request, Response, EmptyResponse, SimpleResponse
from util import logging, getTokenFromFile
import csv
import os
import re
import html

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
                "train <station A>, <station B>: Get realtime departures from station A to station B.")

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
        """Resolve 'train <station> [, <destination>]' where either may be several words.
        Returns: (code, to, disambiguation_response, error_message)
        """
        if not words:
            return None, None, None, ("Which station would you like to check? E.g.:\n"
                                      "• <code>train DFD</code>\n"
                                      "• <code>train dartford, london bridge</code>")

        # Comma-separated: train station1, station2
        if any(',' in word for word in words):
            parts = [part.strip() for part in " ".join(words).split(',') if part.strip()]
            if len(parts) != 2:
                return None, None, None, "Please use one comma to separate the two stations, e.g. <code>train dartford, london bridge</code>"
            code, matches1, err1 = RailRequest.resolveStation(parts[0])
            if err1:
                return None, None, None, err1
            if matches1:
                return None, None, StationDisambiguationResponse(parts[0], matches1), None

            to, matches2, err2 = RailRequest.resolveStation(parts[1])
            if err2:
                return None, None, None, err2
            if matches2:
                return None, None, StationDisambiguationResponse(parts[1], matches2, from_crs=code, is_destination=True), None

            return code, to, None, None

        # Try word splits for two stations without a comma (e.g. train slade green london bridge)
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

        for s in services[:10]:
            lines.append(ServiceItem(s).toFormattedString())

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


class ServiceItem:
    def __init__(self, serviceItem):
        logging.debug(serviceItem)
        self.std = getattr(serviceItem, 'std', '')
        self.etd = getattr(serviceItem, 'etd', '')
        self.isCancelled = getattr(serviceItem, 'isCancelled', False) or (self.etd.lower() == 'cancelled')
        self.cancelReason = getattr(serviceItem, 'cancelReason', None)
        self.delayReason = getattr(serviceItem, 'delayReason', None)
        self.platform = getattr(serviceItem, 'platform', None)

        dest_loc = getattr(serviceItem, 'destination', None)
        if dest_loc and hasattr(dest_loc, 'location') and dest_loc.location:
            self.destination = dest_loc.location[0].locationName
        else:
            self.destination = "Unknown"

    def toFormattedString(self):
        dest = html.escape(self.destination)
        plat = f" • Plat {html.escape(str(self.platform))}" if self.platform and str(self.platform) != "NA" else ""

        if self.isCancelled:
            line = f"🔴 <b>{self.std}</b> <s>{dest}</s>{plat} • <i>Cancelled</i>"
            if self.cancelReason:
                line += f"\n   ↳ <i>{html.escape(self.cancelReason)}</i>"
            return line
        elif self.etd.lower() == "on time":
            return f"🟢 <b>{self.std}</b> {dest}{plat} • <i>On time</i>"
        else:
            exp_text = f"Exp {html.escape(self.etd)}" if self.etd.lower() != "delayed" else "Delayed"
            line = f"🟡 <b>{self.std}</b> {dest}{plat} • <i>{exp_text} (was {self.std})</i>"
            if self.delayReason:
                line += f"\n   ↳ <i>{html.escape(self.delayReason)}</i>"
            return line

    def toString(self):
        timing = self.std if self.etd.lower() == "on time" else f"{self.etd} ({self.std})"
        plat = f" P:{self.platform}" if self.platform else ""
        return f"{timing} {self.destination}{plat}"
