import logging as python_logging
from typing import List, Dict

from galaxy.exceptions import AdminRequiredException

log = python_logging.getLogger(__name__)

def _get_level_info(logger) -> Dict[str, str]:
    '''
    Get the level and effective level of a logger

    :param logger: The logger to get the info for
    :type logger: logging.Logger

    :return: The level and effective level of the logger
    :rtype: Dict[str, str]
    '''
    if logger is None:
        return {
            "name": "None",
            "level": "NOTSET",
            "effective": "NOTSET"
        }
    return {
        "name": logger.name,
        "level": python_logging.getLevelName(logger.level),
        "effective": python_logging.getLevelName(logger.getEffectiveLevel())
    }


def get_logger_names() -> List[str]:
    '''
    Gets the names of all the currently configured loggers.

    :return: The names of all the currently configured loggers
    :rtype: List[str]
    '''
    # if not trans.user_is_admin:
    #     log.warning("Only admins can get log level")
    #     raise AdminRequiredException()
    log.info("Getting a list of all configured loggers")
    logger_dict = python_logging.Logger.manager.loggerDict
    loggers = [name for name in logger_dict if isinstance(logger_dict[name], python_logging.Logger)]
    return loggers



def get_log_levels(name) -> Dict[str, Dict[str, str]]:
    '''
    Get the log level for a one or more loggers. If no name is provided then
    the levels for all loggers is returned.

    :param name: The name of the logger to get the level for
    :type name: str

    :return: The log level for the logger
    :rtype: Dict[str, Dict[str, str]]
    '''
    # if not trans.user_is_admin:
    #     log.warning("Only admins can get log level")
    #     raise AdminRequiredException()
    log.info("Getting level for logger %s", name)
    loggers = get_logger_names()
    if name is None:
        result = {}
        for logger_name in loggers:
            logger = python_logging.getLogger(logger_name)
            result[logger_name] = _get_level_info(logger)
        return result
    elif name.endswith(".*"):
        result = {}
        pattern = name[:-2]
        for logger_name in [logger for logger in loggers if logger.startswith(pattern)]:
            logger = python_logging.getLogger(logger_name)
            result[logger_name] = _get_level_info(logger)
        return result
    elif name in loggers:
        logger = python_logging.getLogger(name)
        return { name: _get_level_info(logger) }
    log.warning("Logger %s not found", name)
    return { "UNKNOWN": _get_level_info(None) }


def set_log_levels(name, level) -> List[Dict[str,str]]:
    '''
    Set the log level for a one or more loggers.

    To set the level for a single logger, pass the name of the logger. To set
    the level for all loggers that start with a certain prefix, e.g. all the logger
    in a particular package, pass the prefix followed by ".*".


    :param name: The name of the logger(s) to set the level for
    :type name: str
    :param level: The level to set the logger to
    :type level: str

    :return: The log level for the logger
    :rtype: LoggerLevelInfo
    '''
    # if not trans.user_is_admin:
    #     log.warning("Only admins can set log level")
    #     raise AdminRequiredException()
    log.info("Setting level for logger %s to %s", name, level)
    result = []
    loggers = get_logger_names()
    if name.endswith(".*"):
        pattern = name[:-2]
        for logger_name in [logger for logger in loggers if logger.startswith(pattern)]:
            logger = python_logging.getLogger(logger_name)
            logger.setLevel(level)
            result.append(_get_level_info(logger))
        return result
    elif name in loggers:
        logger = python_logging.getLogger(name)
        logger.setLevel(level)
        result.append(_get_level_info(logger))
    else:
        log.warning("Logger %s not found", name)
    return result
