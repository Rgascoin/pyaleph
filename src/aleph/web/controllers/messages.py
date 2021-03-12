import aiohttp

from aleph.model.messages import Message
from aleph.web import app
from aiohttp import web
import asyncio
import collections
from aleph.web.controllers.utils import (Pagination,
                                         cond_output, prepare_date_filters)
import logging
LOGGER = logging.getLogger("MESSAGES")


async def get_filters(request):
    find_filters = {}

    msg_type = request.query.get('msgType', None)

    filters = []
    addresses = request.query.get('addresses', None)
    if addresses is not None:
        addresses = addresses.split(',')

    refs = request.query.get('refs', None)
    if refs is not None:
        refs = refs.split(',')

    content_types = request.query.get('contentTypes', None)
    if content_types is not None:
        content_types = content_types.split(',')

    channels = request.query.get('channels', None)
    if channels is not None:
        channels = channels.split(',')

    tags = request.query.get('tags', None)
    if tags is not None:
        tags = tags.split(',')

    hashes = request.query.get('hashes', None)
    if hashes is not None:
        hashes = hashes.split(',')

    date_filters = prepare_date_filters(request, 'time')

    if msg_type is not None:
        filters.append({'type': msg_type})

    if addresses is not None:
        filters.append(
            {'$or': [
                {'content.address': {'$in': addresses}},
                {'sender': {'$in': addresses}},
            ]}
        )

    if content_types is not None:
        filters.append({'content.type': {'$in': content_types}})

    if refs is not None:
        filters.append({'content.ref': {'$in': refs}})

    if tags is not None:
        filters.append({'content.content.tags': {'$elemMatch': {'$in': tags}}})

    if channels is not None:
        filters.append({'channel': {'$in': channels}})

    if hashes is not None:
        filters.append({'$or': [
            {'item_hash': {'$in': hashes}},
            {'tx_hash': {'$in': hashes}}
        ]})

    if date_filters is not None:
        filters.append(date_filters)

    if len(filters) > 0:
        find_filters = {'$and': filters} if len(filters) > 1 else filters[0]

    return find_filters


async def view_messages_list(request):
    """ Messages list view with filters
    """

    find_filters = await get_filters(request)

    pagination_page, pagination_per_page, pagination_skip = \
        Pagination.get_pagination_params(request)
    if pagination_per_page is None:
        pagination_per_page = 0
    if pagination_skip is None:
        pagination_skip = 0

    messages = [msg async for msg
                in Message.collection.find(
                    find_filters, limit=pagination_per_page,
                    skip=pagination_skip,
                    sort=[('time',
                           int(request.query.get('sort_order', '-1')))])]

    context = {
        'messages': messages
    }

    if pagination_per_page is not None:
        if len(find_filters.keys()):
            total_msgs = await Message.collection.count_documents(find_filters)
        else:
            total_msgs = await Message.collection.estimated_document_count()

        query_string = request.query_string
        pagination = Pagination(pagination_page, pagination_per_page,
                                total_msgs,
                                url_base='/messages/posts/page/',
                                query_string=query_string)

        context.update({
            'pagination': pagination,
            'pagination_page': pagination_page,
            'pagination_total': total_msgs,
            'pagination_per_page': pagination_per_page,
            'pagination_item': 'messages'
        })

    return cond_output(request, context, 'TODO.html')

app.router.add_get('/api/v0/messages.json', view_messages_list)
app.router.add_get('/api/v0/messages/page/{page}.json', view_messages_list)


async def messages_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    db = Message.collection
    last_ids = collections.deque(maxlen=100)

    find_filters = await get_filters(request)
    initial_count = int(request.query.get('history', 10))
    initial_count = max(initial_count, 10)
    initial_count = min(initial_count, 200)  # let's cap this to
                                             # 200 historic messages max.
    i = 0
    # TODO: handle this with a capped collection
    while True:
        try:
            run_count = 10
            if i == 0:
                run_count = initial_count
            
            # We get all last items
            items = [item async for item in db.find(
                        find_filters).sort([('$natural', -1)]).limit(run_count)]
            
            # Now let's iterate them in reverse order, older first.
            for item in reversed(items):
                item['_id'] = str(item['_id'])
                if item['_id'] in last_ids:
                    continue

                last_ids.append(item['_id'])
                await ws.send_json(item)
            i += 1

            await asyncio.sleep(1)

        except Exception:
            LOGGER.exception("Error processing")
            await asyncio.sleep(1)


app.router.add_get('/api/ws0/messages', messages_ws)


async def get_random_message(type: str = 'STORE'):
    db = Message.collection

    matches = db.aggregate([
        {'$match': {'type': type}},
        {'$sample': {'size': 1}},  # Random message
    ])
    async for item in matches:
        return item


async def random_message(request: aiohttp.web.Request) -> web.Response:
    message_type = request.query.get('msgType', 'STORE')
    message = await get_random_message(type=message_type)
    message['_id'] = str(message['_id'])
    return web.json_response(message)

app.router.add_get('/api/v0/messages/random.json', random_message)
