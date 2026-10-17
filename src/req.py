
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

    def replyMarkup(self):
        return None

    def parseMode(self):
        return None

class SimpleResponse(Response):
    def __init__(self, response, reply_markup=None, parse_mode=None):
        if isinstance(response, str):
            self.response = [response]
        else:
            self.response = response
        self._reply_markup = reply_markup
        self._parse_mode = parse_mode

    def toTelegramString(self):
        return self.response

    def replyMarkup(self):
        return self._reply_markup

    def parseMode(self):
        return self._parse_mode

class EmptyResponse(Response):  
    def toTelegramString(self):
        return ""