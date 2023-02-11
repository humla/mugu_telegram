import logging

logging.basicConfig(
    format='%(asctime)s - %(filename)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def getTokenFromFile(file):
    with open(file) as f:
        lines = f.readlines()
    f.close()
    token = lines[0].strip()
    logging.info("Token is " + token)
    return token