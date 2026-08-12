"""Add-on entry point."""

from __future__ import annotations

import asyncio
import logging
import signal

from .config import ACTIVATION_TOPIC, ConfigError, load_options
from .microwake_worker import MicroWakeWordActivationWorker
from .mqtt import PahoPublisher, fetch_mqtt_credentials
from .stt_worker import HaSttCanaryWorker
from .transcript import AmbientTranscriptWorker
from .worker import PassiveCanaryWorker

LOGGER = logging.getLogger(__name__)


def build_worker(config, source, publisher):
    """Select exactly one stream owner after configuration validation."""
    if config.passive_canary.enabled:
        LOGGER.info(
            "Starting passive canary source_id=%s models=%s",
            source.id,
            ",".join(config.passive_canary.models),
        )
        return PassiveCanaryWorker(source, config.passive_canary, publisher)
    if config.ha_stt_canary.enabled:
        LOGGER.info(
            "Starting HA STT canary source_id=%s wake_words=%d",
            source.id,
            len(config.ha_stt_canary.wake_words),
        )
        return HaSttCanaryWorker(source, config.ha_stt_canary, publisher)
    if config.ha_stt_activation.enabled:
        LOGGER.info(
            "Starting HA STT activation source_id=%s wake_words=%d transcript_events=%s",
            source.id,
            len(config.ha_stt_activation.wake_words),
            config.transcript_events.enabled,
        )
        return HaSttCanaryWorker(
            source,
            config.ha_stt_activation,
            publisher,
            output_topic=ACTIVATION_TOPIC,
            canary=False,
            transcript_config=(
                config.transcript_events if config.transcript_events.enabled else None
            ),
        )
    if config.microwakeword_activation.enabled:
        LOGGER.info(
            "Starting microWakeWord activation source_id=%s models=%d transcript_events=%s",
            source.id,
            len(config.microwakeword_activation.wake_words),
            config.transcript_events.enabled,
        )
        return MicroWakeWordActivationWorker(
            source,
            config.microwakeword_activation,
            publisher,
            transcript_config=(
                config.transcript_events if config.transcript_events.enabled else None
            ),
        )
    LOGGER.info("Starting standalone transcript events source_id=%s", source.id)
    return AmbientTranscriptWorker(source, config.transcript_events, publisher)


async def run() -> None:
    config = load_options()
    logging.getLogger().setLevel(config.log_level.upper())
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if not any(
        (
            config.passive_canary.enabled,
            config.ha_stt_canary.enabled,
            config.ha_stt_activation.enabled,
            config.microwakeword_activation.enabled,
            config.transcript_events.enabled,
        )
    ):
        LOGGER.info("All activation modes are disabled; waiting for add-on configuration")
        await stop_event.wait()
        return

    if config.passive_canary.enabled:
        active_source_id = config.passive_canary.source_id
    elif config.ha_stt_canary.enabled:
        active_source_id = config.ha_stt_canary.source_id
    elif config.ha_stt_activation.enabled:
        active_source_id = config.ha_stt_activation.source_id
    elif config.microwakeword_activation.enabled:
        active_source_id = config.microwakeword_activation.source_id
    else:
        active_source_id = config.transcript_events.source_id
    source = next(item for item in config.sources if item.id == active_source_id)
    credentials = await asyncio.to_thread(fetch_mqtt_credentials)
    publisher = PahoPublisher(credentials)
    await publisher.connect()
    try:
        worker = build_worker(config, source, publisher)
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
