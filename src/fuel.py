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

            gas_info = []
            for location in json_data.get("data", []):
                display_name = location.get("displayName", "Unknown")

                gas_station = next((service for service in location.get("availableServices", []) if service.get("code") == "GAS_STATION"),None)

                if gas_station and "gasTypes" in gas_station:
                    gas_string = ", ".join(f"{g['name']}: {g['price']}" for g in gas_station["gasTypes"])
                    gas_info.append(f"{display_name}: {gas_string}")

            for gas_string in gas_info:
                logging.info(f"{gas_string}")
            
            gas_types = json_data["data"][0]["availableServices"][0]["gasTypes"]
            gas_string = ", ".join(f"{gas['name']}: {gas['price']}" for gas in gas_types)

            return SimpleResponse(gas_info)
        else:
            print(f"Request failed with status code {response.status_code}")
            logging.info(response)
        return SimpleResponse("Sample Response")


