import logging as python_logging


TRACE = python_logging.DEBUG - 5


def addTraceLoggingLevel():
    addLoggingLevel('TRACE', TRACE)


def setLevels(configuration:dict) -> None:
    for name in configuration:
        level = configuration[name]
        python_logging.getLogger(name).setLevel(level)

def setAllLoggersTo(level:str) -> None:
    setLevels({name: level for name in python_logging.Logger.manager.loggerDict})


def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    A modified version of the method found at
    https://stackoverflow.com/a/35804945/1691778

    Rather than raising an AttributeError we simply return if the levelName or
    methodName already exist.

    --- Original Docstring ---

    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    This method was inspired by the answers to Stack Overflow post
    http://stackoverflow.com/q/2183233/2988730, especially
    http://stackoverflow.com/a/13638084/2988730

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(python_logging, levelName) or hasattr(python_logging, methodName) or hasattr(python_logging.getLoggerClass(), methodName):
        python_logging.warning("Attempted to add logging level %s with level number %d and method name %s, but one or more already exist", levelName, levelNum, methodName)
        # traceback.print_stack()
        return

    # TDOD: Do we really want to do this here?
    python_logging.basicConfig(level=python_logging.DEBUG,
                        format='%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d - %(message)s')

    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)
    def logToRoot(message, *args, **kwargs):
        python_logging.log(levelNum, message, *args, **kwargs)

    python_logging.addLevelName(levelNum, levelName)
    setattr(python_logging, levelName, levelNum)
    setattr(python_logging.getLoggerClass(), methodName, logForLevel)
    setattr(python_logging, methodName, logToRoot)
    python_logging.info("Trace level logging has been enabled")


class DebuggingLogHander(python_logging.Handler):
    """
    A log handler used during testing to capture log records in memory so we
    can validate what has been logged.
    """
    def __init__(self):
        python_logging.Handler.__init__(self)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def reset(self):
        self.records = []

    def get_records(self):
        return self.records



# addTraceLoggingLevel()
