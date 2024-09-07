import logging as python_logging

Logger = python_logging.Logger
LogRecord = python_logging.LogRecord
Handler = python_logging.Handler
Filter = python_logging.Filter
Formatter = python_logging.Formatter
NOTSET = python_logging.NOTSET
TRACE = python_logging.DEBUG - 5
DEBUG = python_logging.DEBUG
INFO = python_logging.INFO
WARN = python_logging.WARN
WARNING = python_logging.WARNING
ERROR = python_logging.ERROR
FATAL = python_logging.FATAL
CRITICAL = python_logging.CRITICAL

def getLogger(name):
    return python_logging.getLogger(name)

def basicConfig(**kwargs):
    python_logging.basicConfig(**kwargs)

def getLoggerClass():
    return python_logging.getLoggerClass()

def getLevelName(level):
    return python_logging.getLevelName(level)

def addTraceLoggingLevel():
    addLoggingLevel('TRACE', python_logging.DEBUG - 5)


def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    A modified version of the method found at https://stackoverflow.com/a/35804945/1691778
    Rather than raising an AttributeError we simply return if the levelName or methodName already exist.

    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

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
        return

    python_logging.basicConfig(level=python_logging.DEBUG,
                        format='%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d - %(message)s')

    # if hasattr(logging, levelName):
    #    raise AttributeError('{} already defined in logging module'.format(levelName))
    # if hasattr(logging, methodName):
    #    raise AttributeError('{} already defined in logging module'.format(methodName))
    # if hasattr(logging.getLoggerClass(), methodName):
    #    raise AttributeError('{} already defined in logger class'.format(methodName))

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
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

addTraceLoggingLevel()
