from req import Request, Response
from util import logging

class TflRequest(Request):
    supportedCommands = {'tube', 'bus'}

    def helpString(self):
        return "tube <station name>: Not supported yet\nbus <bus stop name>: Not supported yet"

    def apiKey():
        token = getTokenFromFile('tfl.key')
        return token

    def handleRequest(self):
        if (self.command in TflRequest.supportedCommands):
            logging.info("Tfl request handler: I support this command ")
            return TflResponse("Sample Response")
        else:
            logging.debug("I do not support this request")

    def makeRequestToTflServer(self):
        apiServer = 'https://api.tfl.gov.uk/StopPoint/%Name%'
        apiServer2= 'https://api.tfl.gov.uk/StopPoint/%Name%/Arrivals?'
        "Result"

class TflResponse(Response):
    def __init__(self, timings):
        self.timings = timings

    def toTelegramString():
        timings
