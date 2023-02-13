from req import Request, Response
from util import logging, getTokenFromFile

from suds.client import Client
from suds.sax.element import Element

class RailRequest(Request):
    supportedCommands = {'train'}


    def handleRequest(self):
        if (self.command() in RailRequest.supportedCommands): 
            logging.info ("I support this request type")
            return RailRequest.getDepartureForStation("DFD")
    
    def apiKey():
        # Load the rail.key file and get the api key
        token = getTokenFromFile('rail.key')
        return token

    def getDepartureForStation(stationId):
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
            numRows="20", crs=stationId, filterCrs="", filterType="to", timeOffset="0", timeWindow="120")

        # The result variable now holds the result of the SOAP request
        return RailResponse(result)

class RailResponse(Response):
    def __init__(self, stationBoardWithDetails):
        self.details = stationBoardWithDetails
    
    def toTelegramString(self):
        #logging.info(self.details.trainServices[0])
        res = list(map(lambda a: ServiceItem(a).toString() ,self.details.trainServices.service))
        logging.info(res)
        return ""

class ServiceItem:
    def __init__(self, serviceItem):
        
        self.std = serviceItem.std
        self.etd = serviceItem.etd
        if (self.etd.lower() == "on time"):
            self.timing = self.std
        else:
            self.timing = self.etd + " (" + self.std + ")" 
        self.platform = "test" #serviceItem.platform
        self.destination = serviceItem.destination.location[0].locationName
        self.origin = serviceItem.origin.location[0].locationName

    def toString(self):
        return self.timing + " " + self.platform + " " + self.destination + " " + self.origin 
        
