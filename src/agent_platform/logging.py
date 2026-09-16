import logging as std_logging


def configure_logging(level: str = "INFO") -> None:
    resolved_level = getattr(std_logging, level.upper(), std_logging.INFO)
    std_logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
