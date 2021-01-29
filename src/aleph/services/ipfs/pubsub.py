import asyncio
import base64
import logging
from typing import Coroutine, List
from aiohttp import ClientConnectorError
import base58

from .common import get_base_url, get_ipfs_api

LOGGER = logging.getLogger("IPFS.PUBSUB")


async def decode_msg(msg):
    return {
        'from': base58.b58encode(
            base64.b64decode(msg['from'])),
        'data': base64.b64decode(msg['data']),
        'seqno': base58.b58encode(base64.b64decode(msg['seqno'])),
        'topicIDs': msg['topicIDs']
    }


async def sub(topic, base_url=None):
    from aleph.network import incoming_check
    if base_url is None:
        from aleph.web import app
        base_url = await get_base_url(app['config'])
        
    api = await get_ipfs_api()
    
    async for mvalue in api.pubsub.sub(topic):
        try:
            LOGGER.debug("New message received %r" % mvalue)

            # we should check the sender here to avoid spam
            # and such things...
            message = await incoming_check(mvalue)
            if message is not None:
                yield message

        except Exception:
            LOGGER.exception("Error handling message")


async def pub(topic, message):
    api = await get_ipfs_api()
    await api.pubsub.pub(topic, message)


async def incoming_channel(config, topic):
    from aleph.chains.common import incoming
    # When using some deployment strategies such as docker-compose,
    # the IPFS service may not be ready by the time this function
    # is called. This variable define how many connection attempts
    # will not be logged as exceptions.
    trials_before_exception: int = 5
    while True:
        try:
            # seen_ids = []
            async for message in sub(topic,
                                     base_url=await get_base_url(config)):                    
                LOGGER.debug("New message %r" % message)
                await incoming(message)

                # Raise all connection errors after one has succeeded.
                trials_before_exception = 0
        except Exception:
            if trials_before_exception > 0:
                LOGGER.info("Exception in IPFS pubsub, reconnecting in 2 seconds...")
            else:
                LOGGER.exception("Exception in IPFS pubsub, reconnecting in 2 seconds...")
            await asyncio.sleep(2)
        finally:
            trials_before_exception = max(trials_before_exception -1, 0)


def run_ipfs_incoming_channel(config, topic):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(incoming_channel(config=config, topic=topic))
