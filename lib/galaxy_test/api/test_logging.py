import logging
from galaxy.util.logging import setLevels, setAllLoggersTo, DebuggingLogHander

setAllLoggersTo(logging.ERROR)
setLevels({
    'galaxy.datatypes.display_applications.application': logging.CRITICAL,
    'galaxy.managers.logging': logging.TRACE,
    'galaxy.api.logging': logging.TRACE,
    'galaxy_test.api.test_logging': logging.TRACE
})

log = logging.getLogger(__name__)
log.trace("Setting the log level to TRACE for %s", __name__)

from ._framework import ApiTestCase


SIMPLE_LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': True,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'simple',
            'stream': 'ext://sys.stdout'
        }
    },
    'formatters': {
        'simple': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        }
    },
    'loggers': {
        'test': {
            'level': 'DEBUG',
            'handlers': ['console']
        }
    }
}

class TestLoggingApi(ApiTestCase):

    def test_logging_has_trace_level(self):
        assert hasattr(logging, "TRACE")
        assert hasattr(logging, "trace")
        assert logging.TRACE == logging.DEBUG - 5


    def test_index(self):
        logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
        response = self._get("logging")
        response.raise_for_status()
        logger_names = response.json()
        # These are the only two loggers that we can be sure are present.
        assert "test" in logger_names
        assert "galaxy.managers.logging" in logger_names

    def test_get(self):
        logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
        response = self._get("logging/config/test")
        response.raise_for_status()
        logger_level = response.json()
        assert logger_level["name"] == "test"
        assert logger_level["level"] == "DEBUG"
        assert logger_level["effective"] == "DEBUG"

        response = self._get("logging/config/galaxy.managers.logging")
        response.raise_for_status()
        logger_level = response.json()
        assert logger_level["name"] == "galaxy.managers.logging"
        assert logger_level["level"] == "TRACE"
        assert logger_level["effective"] == "TRACE"

    def test_set(self):
        logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
        response = self._post("logging/config/test?level=CRITICAL") #, data={"level": "CRITICAL"})
        response.raise_for_status()
        logger_level = response.json()
        assert  logger_level["name"] == "test"
        assert logger_level["level"] == "CRITICAL"
        assert logger_level["effective"] == "CRITICAL"

        # Verify that only CRITICAL messages are logged.
        handler = DebuggingLogHander()
        logger = logging.getLogger("test")
        logger.addHandler(handler)
        logger.info("INFO")
        logger.warning("WARNING")
        logger.error("ERROR")
        logger.critical("CRITICAL")
        records = handler.get_records()
        assert len(records) == 1
        assert records[0].levelname == "CRITICAL"
        assert records[0].message == "CRITICAL"

    def test_set_existing_logger(self):
        logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
        logger = logging.getLogger('test')
        handler = DebuggingLogHander()
        logger.addHandler(handler)
        logger.trace("TRACE")
        logger.debug("DEBUG")
        logger.info("INFO")
        records = handler.get_records()
        assert len(records) == 2
        assert records[0].levelname == "DEBUG"
        assert records[1].levelname == "INFO"
        handler.reset()

        response = self._post("logging/config/test?level=INFO")
        response.raise_for_status()
        logger_level = response.json()
        assert logger_level["name"] == "test"
        assert logger_level["level"] == "INFO"
        assert logger_level["effective"] == "INFO"
        logger.trace("TRACE")
        logger.debug("DEBUG")
        logger.info("INFO")
        records = handler.get_records()
        assert len(records) == 1
        assert records[0].levelname == "INFO"
        handler.reset()

        response = self._post("logging/config/test?level=ERROR")
        response.raise_for_status()
        logger_level = response.json()
        assert logger_level["name"] == "test"
        assert logger_level["level"] == "ERROR"
        assert logger_level["effective"] == "ERROR"
        logger.trace("TRACE")
        logger.debug("DEBUG")
        logger.info("INFO")
        records = handler.get_records()
        assert len(records) == 0
        handler.reset()
        logger.error("ERROR")
        records = handler.get_records()
        assert len(records) == 1
        assert records[0].levelname == "ERROR"