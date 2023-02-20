from req import Request, Response
from util import logging, getTokenFromFile
import csv

from suds.client import Client
from suds.sax.element import Element

class StationCodeRequest(Request):
    supportedCommands = {'code'}

    def helpString(self):
        return "code <station name>: Get the crif code for a station."

    def handleRequest(self):
        if (self.command() in StationCodeRequest.supportedCommands):
            return ""
        else:
            return "1"
        pass

    def lookupStation(stationName):
        
        pass

class RailRequest(Request):
    supportedCommands = {'train'}

    def helpString(self):
        return "train <station code>: Get realtime departure for a train station\n" + "train <station code A> <station code B>: Get realtime departures for a train from station A to station B"
            

    def handleRequest(self):
        if (self.command() in RailRequest.supportedCommands): 
            logging.info ("I support this request type: " + self.command())
            if (len(self.requestParams) > 2):
                 to = self.requestParams[2] 
            else:
                to =  ""
            return RailRequest.getDepartureForStation(self.requestParams[1], to)
    
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
        
