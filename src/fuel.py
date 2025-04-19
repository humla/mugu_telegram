from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging
import requests

class FuelPriceRequest(Request):
    def supportedCommands(self):
        return {'fuel'}

    def helpString(self):
        return "fuel : Shows the price of fuel from Costco Thurrock"

    def handleRequest(self):
        logging.info("Fuel price request")
        url = "https://www.costco.co.uk/store-finder/search?q=da1"

        response = requests.get(url)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse JSON response
            json_data = response.json()
            gas_types = json_data["data"][0]["availableServices"][0]["gasTypes"]
            gas_string = ", ".join(f"{gas['name']}: {gas['price']}" for gas in gas_types)

            return SimpleResponse(gas_string)
        else:
            print(f"Request failed with status code {response.status_code}")
            logging.info(response)
        return SimpleResponse("Sample Response")


