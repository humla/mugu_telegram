
class Request:
    def __init__(self, requestParams):
        self.requestParams = requestParams

    def supportedCommands(self):
        pass

    def handleRequest(self): 
        pass

    def handleRequestBase(self):
        if (self.command() in self.supportedCommands()):
            return self.handleRequest()
        else:
            return EmptyResponse()   

    def command(self):
        return self.requestParams[0].lower()

    def helpString(self):
        pass

class Response:
    def toTelegramString(self): 
        pass

class SimpleResponse(Response):
    def __init__(self, response):
        if isinstance(response, str):
            self.response = [response]
        else:
            self.response = response

    def toTelegramString(self):
        return self.response

class EmptyResponse(Response):  
    def toTelegramString(self):
        return ""