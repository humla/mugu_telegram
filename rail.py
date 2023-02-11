from req import Request, Response
from util import logging, getTokenFromFile

from suds.client import Client
from suds.sax.element import Element

class RailRequest(Request):
    supportedCommands = {'train'}


    def handleRequest(self):
        if (self.command() in RailRequest.supportedCommands): 
            logging.info ("I support this request type")
            RailRequest.getDepartureForStation("DFD")
            return RailResponse()

    
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
            numRows="10", crs=stationId, filterCrs="", filterType="to", timeOffset="0", timeWindow="120")

        # The result variable now holds the result of the SOAP request
        logging.info(result)

        return 

class RailResponse(Response):
    pass
        
