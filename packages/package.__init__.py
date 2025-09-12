from pkgutil import extend_path
try:
    from galaxy.util.logging import addTraceLoggingLevel
    addTraceLoggingLevel()
except:
    pass

__path__ = extend_path(__path__, __name__)  # noqa: F821
