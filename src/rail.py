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

    def helpString(self):
        return "code <station name>: Get the crif code for a station."

    def handleRequest(self):
        return SimpleResponse("")
     
    def lookupStation(stationName):  
        pass

class RailRequest(Request):
    def supportedCommands(self):
        return {'train'}    

    def helpString(self):
        return "train <station name or code>: Get realtime departure for a train station\n" + "train <station A> <station B>: Get realtime departures for a train from station A to station B. Stations can be a name (e.g. dartford) or a 3 letter code (e.g. DFD)"

    def handleRequest(self):
        logging.info ("I support this request type: " + self.command())
        code, error = RailRequest.resolveStation(self.requestParams[1])
        if error:
            return SimpleResponse(error)
        if (len(self.requestParams) > 2):
            # The message is split on spaces, so try the full phrase first, then just the first word
            to, error = RailRequest.resolveStation(" ".join(self.requestParams[2:]))
            if error:
                to, error = RailRequest.resolveStation(self.requestParams[2])
            if error:
                return SimpleResponse(error)
        else:
            to =  ""
        return RailRequest.getDepartureForStation(code, to)

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
        res = list(map(lambda a: ServiceItem(a).toString() ,self.details.trainServices.service))
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
        
