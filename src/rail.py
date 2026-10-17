from req import Request, Response, EmptyResponse, SimpleResponse
from util import logging, getTokenFromFile
import csv
import os

def loadStationCodes():
    codes = {}
    with open(os.path.join(os.path.dirname(__file__), "../res/station_code.csv")) as f:
        reader = csv.reader(f)
        next(reader)  # skip the header row
        for row in reader:
            for i in range(0, len(row) - 1, 2):
                name, code = row[i].strip(), row[i + 1].strip()
                if name and code:
                    codes[name.lower()] = code
    return codes

from suds.client import Client
from suds.sax.element import Element

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
            return SimpleResponse("Which station? e.g. code dartford")
        codes = loadStationCodes()
        if query in codes:
            return SimpleResponse("%s is station code %s" % (query.title(), codes[query]))
        if self.requestParams[1].upper() in codes.values():
            code = self.requestParams[1].upper()
            names = [name.title() for name, c in codes.items() if c == code]
            return SimpleResponse("Station code %s is %s" % (code, " / ".join(names)))
        matches = {name: code for name, code in codes.items() if query in name}
        if not matches:
            return SimpleResponse("I could not find a station called '%s'." % query.title())
        lines = ["Stations matching '%s':" % query.title()]
        for name, code in sorted(matches.items())[:StationCodeRequest.MAX_MATCHES]:
            lines.append("%s is %s" % (name.title(), code))
        if len(matches) > StationCodeRequest.MAX_MATCHES:
            lines.append("... and %d more" % (len(matches) - StationCodeRequest.MAX_MATCHES))
        return SimpleResponse(lines)
 
class RailRequest(Request):
    def supportedCommands(self):
        return {'train'}    

    def helpString(self):
        return "train <station name or code>: Get realtime departure for a train station\n" + "train <station A>, <station B>: Get realtime departures for a train from station A to station B. Stations can be a name (e.g. slade green) or a 3 letter code (e.g. DFD). Separate the two stations with a comma"

    def handleRequest(self):
        logging.info ("I support this request type: " + self.command())
        words = self.requestParams[1:]
        code, to, error = RailRequest.resolveStationWords(words)
        if error:
            return SimpleResponse(error)
        return RailRequest.getDepartureForStation(code, to)

    def resolveStationWords(words):
        """Resolve 'train <station> [, <destination>]' where either may be several words (e.g. slade green).
        A comma is the preferred separator between the two stations and is unambiguous.
        Without one, every split of the words into a station part and a destination part is tried."""
        if any(word.endswith(',') for word in words):
            parts = [part.strip() for part in " ".join(words).split(',') if part.strip()]
            if len(parts) != 2:
                return None, None, "Please use one comma to separate the two stations, e.g. train dartford, london victoria"
            code, error = RailRequest.resolveStation(parts[0])
            if error:
                return None, None, error
            to, error = RailRequest.resolveStation(parts[1])
            if error:
                return None, None, error
            return code, to, None
        errors = []
        for k in range(0, len(words) - 1):
            code, error = RailRequest.resolveStation(" ".join(words[:k + 1]))
            if error:
                errors.append(error)
                continue
            to, error = RailRequest.resolveStation(" ".join(words[k + 1:]))
            if error:
                errors.append(error)
                continue
            return code, to, None
        code, error = RailRequest.resolveStation(" ".join(words))
        if not error:
            return code, "", None
        return None, None, errors[0] if errors else error

    def resolveStation(input):
        """Turn a station name or CRS code into a CRS code. Returns (code, None) on success or (None, error message)."""
        query = input.lower()
        codes = loadStationCodes()
        if query in codes:
            return codes[query], None
        if input.upper() in codes.values():
            return input.upper(), None
        matches = {name: code for name, code in codes.items() if query in name}
        if len(matches) == 1:
            return next(iter(matches.values())), None
        if not matches:
            return None, "I could not find a station called '%s'. Try the 3 letter station code, e.g. train DFD" % input
        examples = ", ".join("%s (%s)" % (name.title(), code) for name, code in sorted(matches.items())[:5])
        more = " and %d more" % (len(matches) - 5) if len(matches) > 5 else ""
        return None, "Several stations match '%s': %s%s. Please be more specific." % (input, examples, more)
    
    def apiKey():
        # Load the rail.key file and get the api key
        token = getTokenFromFile('rail.key')
        return token

    def getDepartureForStation(stationId, to):
        api_address = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2021-11-01"
        api_token = RailRequest.apiKey()

        # Create a client object with the WSDL URL
        client = Client(api_address)

        # Create a header element with your token ID
        header = Element("AccessToken").append(Element("TokenValue").setText(api_token))

        # Add the header element to the SOAP request
        client.set_options(soapheaders=header)

        # Call a method on the service
        result = client.service.GetDepBoardWithDetails(
            numRows="10", crs=stationId, filterCrs=to, filterType="to", timeOffset="0", timeWindow="120")

        # The result variable now holds the result of the SOAP request
        return RailResponse(result)

class RailResponse(Response):
    def __init__(self, stationBoardWithDetails):
        self.details = stationBoardWithDetails
    
    def toTelegramString(self):
        services = getattr(self.details, 'trainServices', None)
        services = services.service if services else []
        if not services:
            return ["National Rail: %s (%s) - no departing services found" % (self.details.locationName, self.details.crs)]
        res = list(map(lambda a: ServiceItem(a).toString() ,services))
        locationName = self.details.locationName
        crs = self.details.crs
        res.insert(0, "National Rail: %s (%s)" % (locationName, crs))
        return res

class ServiceItem:
    def __init__(self, serviceItem):
        logging.debug(serviceItem)
        self.std = serviceItem.std
        self.etd = serviceItem.etd
        if (self.etd.lower() == "on time"):
            self.timing = self.std
        else:
            self.timing = self.etd + " (" + self.std + ")" 
        self.platform = getattr(serviceItem, 'platform', 'NA')
        self.destination = serviceItem.destination.location[0].locationName
        self.origin = serviceItem.origin.location[0].locationName

    def toString(self):
        return self.timing + " " + self.destination + " P:" + self.platform 
        
