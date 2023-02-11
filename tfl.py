from req import Request, Response
from util import logging

class TflRequest(Request):
    supportedCommands = {'tube', 'bus'}

    def apiKey():
        # Load the tfl.key file 
        pass

    def handleRequest(self):
        if (self.command in TflRequest.supportedCommands):
            logging.info("Tfl request handler: I support this command ")
            return TflResponse("Sample Response")
        else:
            logging.debug("I do not support this request")

    def makeRequestToTflServer(self):
        apiServer = 'https://api.tfl.gov.uk/StopPoint/%Name%'
        apiServer2= 'https://api.tfl.gov.uk/StopPoint/%Name%/Arrivals?'
        appIdAndKey = "app_id=0db3f13d&app_key=91a44d1f46487768a0718e716e33a5ce"
        "Result"

class TflResponse(Response):
    def __init__(self, timings):
        self.timings = timings

    def toTelegramString():
        timings
