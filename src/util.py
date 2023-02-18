import logging
import os

logging.basicConfig(
    format='%(asctime)s - %(filename)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def getTokenFromFile(file):
    file_with_path = os.path.join(os.path.dirname(__file__), "../res/" + file)
    with open(file_with_path) as f:
        lines = f.readlines()
    f.close()
    token = lines[0].strip()
    logging.info("Token is " + token)
    return token