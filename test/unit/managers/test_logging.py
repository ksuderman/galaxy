import logging.config
import types


from galaxy.util.logging import TRACE
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

def test_trace_level_exists():
    assert hasattr(logging, 'TRACE')
    assert hasattr(logging, 'trace')
    assert logging.TRACE == TRACE
    assert type(getattr(logging, 'TRACE')) == int
    assert isinstance(getattr(logging, 'trace'), types.FunctionType)


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
    assert manager.get_logger_level('test').level == 'WARNING'
    manager.set_logger_level('test', 'DEBUG')
    assert manager.get_logger_level('test').level == 'DEBUG'

    # assert manager.get_logger_level('test').level == 'WARNING'
    # assert manager.get_logger_level('test').level == 'WARNING'
    # assert manager.set_logger_level('test', 'DEBUG').level == 'DEBUG'
    # assert manager.get_logger_level('test').level == 'DEBUG'


def test_set_levels():
    import logging
    logging.config.dictConfig(SIMPLE_LOGGING_CONFIG)
    manager = LoggingManager()

    a = logging.getLogger('a')
    ab = logging.getLogger('a.b')
    abc = logging.getLogger('a.b.c')

    assert a.getEffectiveLevel() == logging.DEBUG
    assert ab.getEffectiveLevel() == logging.DEBUG
    assert abc.getEffectiveLevel() == logging.DEBUG

    manager.set_logger_levels('a.*', 'INFO')
    assert a.getEffectiveLevel() == logging.INFO
    assert ab.getEffectiveLevel() == logging.INFO
    assert abc.getEffectiveLevel() == logging.INFO

    manager.set_logger_levels('a.b.*', logging.WARNING)
    assert a.getEffectiveLevel() == logging.INFO
    assert ab.getEffectiveLevel() == logging.WARNING
    assert abc.getEffectiveLevel() == logging.WARNING

    manager.set_logger_levels('a.b.c.*', logging.ERROR)
    assert a.getEffectiveLevel() == logging.INFO
    assert ab.getEffectiveLevel() == logging.WARNING
    assert abc.getEffectiveLevel() == logging.ERROR

    manager.set_logger_levels('a.b.c', logging.CRITICAL)
    assert a.getEffectiveLevel() == logging.INFO
    assert ab.getEffectiveLevel() == logging.WARNING
    assert abc.getEffectiveLevel() == logging.CRITICAL

    manager.set_logger_levels('a.b', logging.DEBUG)
    assert a.getEffectiveLevel() == logging.INFO
    assert ab.getEffectiveLevel() == logging.DEBUG
    assert abc.getEffectiveLevel() == logging.CRITICAL

    manager.set_logger_levels('a', logging.TRACE)
    assert a.getEffectiveLevel() == logging.TRACE
    assert ab.getEffectiveLevel() == logging.DEBUG
    assert abc.getEffectiveLevel() == logging.CRITICAL

