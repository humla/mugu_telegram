from req import Request, Response, EmptyResponse
from util import logging

class TflRequest(Request):
    def supportedCommands(self):
        return {'tube', 'bus'}

    def helpString(self):
        return "tube <station name>: Not supported yet\nbus <bus stop name>: Not supported yet"

    def apiKey():
        token = getTokenFromFile('tfl.key')
        return token

    def handleRequest(self):
        logging.info("Tfl request handler: I support this command ")
        return TflResponse("Sample Response")

    def makeRequestToTflServer(self):
        apiServer = 'https://api.tfl.gov.uk/StopPoint/%Name%'
        apiServer2= 'https://api.tfl.gov.uk/StopPoint/%Name%/Arrivals?'
        "Result"

class TflResponse(Response):
    def __init__(self, timings):
        self.timings = timings

    def toTelegramString():
        timings
