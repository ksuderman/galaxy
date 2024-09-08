import logging
import json

from . import Router

router = Router(tags=["logging"])

@router.cbv
class FastAPILogging:
    @router.get(
        "/api/logging",
        public=True,
        summary="Returns the current logging configuration",
        response_description="Logging configuration",
    )
    async def get(self) -> str:
        """Returns the current logging configuration."""
        result = []
        for logger in logging.Logger.manager.loggerDict:
            print(logger)
        result = json.dumps(logging.Logger.manager.loggerDict, indent=4, sort_keys=True)
        print(result)
        return result

