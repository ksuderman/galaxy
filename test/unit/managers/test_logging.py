import logging.config

from galaxy.managers.logging import LoggingManager


SIMPLE_LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': True,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'simple',
            'stream': 'ext://sys.stdout',
        },
        'test': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'simple',
            'stream': 'ext://sys.stdout'
        }
    },
    'formatters': {
        'simple': {
            'format': '%(name)s: %(message)s',
        },
    },
    'loggers': {
        'console': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'test': {
            'handlers': ['test'],
            'level': 'WARNING',
            'propagate': False,
        }
    },
}

def test_logging_manager_index():
    logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)

    manager = LoggingManager()
    index = manager.index()
    assert len(index) > 1
    # These are the only two loggers that we can be sure are present.
    assert 'galaxy.managers.logging' in index
    assert 'test' in index


def test_get_logging_levels():
    logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
    manager = LoggingManager()
    levels = manager.get_logger_levels()
    assert 'test' in levels
    assert levels['test'].level == 'WARNING'
    assert levels['test'].effective == 'WARNING'
    assert 'galaxy.managers.logging' in levels
    assert levels['galaxy.managers.logging'].level == 'NOTSET'
    assert levels['galaxy.managers.logging'].effective == 'DEBUG'


def test_get_logging_level():
    logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
    manager = LoggingManager()
    logger = manager.get_logger_level('test')
    assert logger.level == 'WARNING'
    assert logger.effective == 'WARNING'
    logger = manager.get_logger_level('galaxy.managers.logging')
    assert logger.level == 'NOTSET'
    assert logger.effective == 'DEBUG'


def test_set_level():
    logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
    manager = LoggingManager()
    loggers = manager.index()
    assert 'test' in loggers
    manager.set_logger_level('test', 'DEBUG')
    assert manager.get_logger_level('test').level == 'DEBUG'

    # assert manager.get_logger_level('test').level == 'WARNING'
    # assert manager.get_logger_level('test').level == 'WARNING'
    # assert manager.set_logger_level('test', 'DEBUG').level == 'DEBUG'
    # assert manager.get_logger_level('test').level == 'DEBUG'
