""" This is the storage message handlers file.

For now it's very simple, we check if we want to store files or not.

TODO:
- check balances and storage allowance
- handle incentives from 3rd party
- hjandle garbage collection of unused hashes
"""

import logging

import aioipfs
import asyncio

from aleph.handlers.register import register_incoming_handler
from aleph.services.ipfs.common import get_ipfs_api
from aleph.storage import get_hash_content
from aleph.types import ItemType
from aleph.web import app
from aleph.web.controllers.p2p import get_user_usage, get_user_quota

LOGGER = logging.getLogger("HANDLERS.STORAGE")


async def handle_new_storage(message, content):
    if not app["config"].storage.store_files.value:
        return True  # Ignore

    try:
        engine = ItemType(content.get("item_type"))
    except ValueError:
        LOGGER.warning("Got invalid storage engine %s" % engine)
        return -1  # not allowed, ignore.

    is_folder = False
    item_hash = content["item_hash"]
    ipfs_enabled = app["config"].ipfs.enabled.value
    do_standard_lookup = True
    size = 0

    if engine == ItemType.IPFS and ipfs_enabled:
        api = await get_ipfs_api(timeout=5)
        try:
            stats = await asyncio.wait_for(api.files.stat(f"/ipfs/{item_hash}"), 5)

            if (
                stats["Type"] == "file"
                and stats["CumulativeSize"] < 1024 ** 2
                and len(item_hash) == 46
            ):
                do_standard_lookup = True
            else:
                # Large file or directory
                size = stats['CumulativeSize']

                address: str = content["content"]["address"]
                usage: float = await get_user_usage(address)
                quota: float = await get_user_quota(address)
                assert usage >= 0
                assert quota >= 0
                if (size + usage) > quota:
                    LOGGER.warning(f"Not enough tokens for pinning {item_hash} by {address}")
                    return -1  # -1 to permanently reject

                content['engine_info'] = stats
                pin_api = await get_ipfs_api(timeout=60)
                timer = 0
                is_folder = stats["Type"] == "directory"
                async for status in pin_api.pin.add(item_hash):
                    timer += 1
                    if timer > 30 and status["Pins"] is None:
                        return None  # Can't retrieve data now.
                do_standard_lookup = False

        except (aioipfs.APIError, asyncio.TimeoutError) as e:
            if hasattr(e, "message"):
                if "invalid CID" in getattr(e, "message", ""):
                    LOGGER.warning(
                        f"Error retrieving stats of hash {item_hash}: {e.message}"
                    )
                    return -1

                LOGGER.exception(
                    f"Error retrieving stats of hash {item_hash}: {e.message}"
                )
                do_standard_lookup = True
            else:
                LOGGER.exception(f"Error retrieving stats of hash {item_hash}")
                do_standard_lookup = True

    if do_standard_lookup:
        # TODO: We should check the balance here.
        file_content = await get_hash_content(
            item_hash,
            engine=engine,
            tries=4,
            timeout=2,
            use_network=True,
            use_ipfs=True,
            store_value=True,
        )
        if file_content is None or file_content == -1:
            return None

        size = len(file_content)

    content["size"] = size
    content["content_type"] = is_folder and "directory" or "file"

    return True


register_incoming_handler("STORE", handle_new_storage)
