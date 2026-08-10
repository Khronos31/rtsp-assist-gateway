"""Add-on entry point."""

from __future__ import annotations

import asyncio
import logging
import signal

from .config import ConfigError, load_options
from .mqtt import PahoPublisher, fetch_mqtt_credentials
from .stt_worker import HaSttCanaryWorker
from .worker import PassiveCanaryWorker

LOGGER = logging.getLogger(__name__)


async def run() -> None:
    config = load_options()
    logging.getLogger().setLevel(config.log_level.upper())
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if not config.passive_canary.enabled and not config.ha_stt_canary.enabled:
        LOGGER.info("All canary modes are disabled; waiting for add-on configuration")
        await stop_event.wait()
        return

    active_source_id = (
        config.passive_canary.source_id
        if config.passive_canary.enabled
        else config.ha_stt_canary.source_id
    )
    source = next(item for item in config.sources if item.id == active_source_id)
    credentials = await asyncio.to_thread(fetch_mqtt_credentials)
    publisher = PahoPublisher(credentials)
    await publisher.connect()
    try:
        if config.passive_canary.enabled:
            LOGGER.info(
                "Starting passive canary source_id=%s models=%s",
                source.id,
                ",".join(config.passive_canary.models),
            )
            worker = PassiveCanaryWorker(source, config.passive_canary, publisher)
        else:
            LOGGER.info(
                "Starting HA STT canary source_id=%s wake_words=%d",
                source.id,
                len(config.ha_stt_canary.wake_words),
            )
            worker = HaSttCanaryWorker(source, config.ha_stt_canary, publisher)
        await worker.run_forever(stop_event)
    finally:
        await publisher.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(run())
    except ConfigError as exc:
        LOGGER.error("Invalid add-on configuration: %s", exc)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
