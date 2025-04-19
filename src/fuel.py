from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging

class FuelPriceRequest(Request):
    def supportedCommands(self):
        return {'fuel'}

    def helpString(self):
        return "fuel : Shows the price of fuel from Costco Thurrock"

    def handleRequest(self):
        logging.info("Fuel price request")
        return SimpleResponse("Sample Response")


