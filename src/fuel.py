from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging
import re
import requests

class FuelPriceRequest(Request):
    def supportedCommands(self):
        return {'fuel'}

    def helpString(self):
        return "fuel : Shows the price of fuel from Costco Thurrock\n" + \
               "fuel <postcode>: Shows the price of fuel from Costco stores near that postcode"

    def handleRequest(self):
        logging.info("Fuel price request")
        postcode = self.requestParams[1].lower() if len(self.requestParams) > 1 else "da1"
        if not re.fullmatch(r"[a-z0-9]{2,4}", postcode):
            return SimpleResponse("'%s' does not look like a postcode. Try e.g. fuel DA1" % postcode)
        url = "https://www.costco.co.uk/store-finder/search?q=" + postcode

        response = requests.get(url)

        # Check if the request was successful
        if response.status_code == 200:
            try:
                # Parse JSON response
                json_data = response.json()

                gas_info = []
                for location in json_data.get("data", []):
                    display_name = location.get("displayName", "Unknown")

                    gas_station = next((service for service in location.get("availableServices", []) if service.get("code") == "GAS_STATION"), None)

                    if gas_station and "gasTypes" in gas_station:
                        gas_string = ", ".join(f"{g['name']}: {g['price']}" for g in gas_station["gasTypes"])
                        gas_info.append(f"{display_name}: {gas_string}")

                for gas_string in gas_info:
                    logging.info(f"{gas_string}")

                if not gas_info:
                    return SimpleResponse("I found no Costco fuel stations near %s." % postcode.upper())
                return SimpleResponse(gas_info)
            except (ValueError, KeyError, IndexError, AttributeError):
                logging.info("Costco response was not the expected JSON")
                return SimpleResponse("Sorry, the Costco website is not returning fuel prices in the expected format right now.")
        else:
            logging.info(f"Request failed with status code {response.status_code}")
            return SimpleResponse("Sorry, I could not fetch fuel prices right now (HTTP %d)." % response.status_code)
