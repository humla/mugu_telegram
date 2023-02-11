
class Request:
    def __init__(self, requestParams):
        self.requestParams = requestParams

    def handleRequest(self): 
        pass

    def command(self):
        return self.requestParams[0].lower()

    def helpString():
        pass

class Response:
    def toTelegramString(self): 
        pass
