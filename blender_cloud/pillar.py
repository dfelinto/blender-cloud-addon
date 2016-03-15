import asyncio
import sys
import os
import functools
import logging
from contextlib import closing

import requests

# Add our shipped Pillar SDK wheel to the Python path
if not any('pillar_sdk' in path for path in sys.path):
    import glob

    # TODO: gracefully handle errors when the wheel cannot be found.
    my_dir = os.path.dirname(__file__)
    pillar_wheel = glob.glob(os.path.join(my_dir, 'pillar_sdk*.whl'))[0]
    sys.path.append(pillar_wheel)

import pillarsdk
import pillarsdk.exceptions
import pillarsdk.utils

_pillar_api = None  # will become a pillarsdk.Api object.
log = logging.getLogger(__name__)


class UserNotLoggedInError(RuntimeError):
    """Raised when the user should be logged in on Blender ID, but isn't.

    This is basically for every interaction with Pillar.
    """


def blender_id_profile() -> dict:
    """Returns the Blender ID profile of the currently logged in user."""

    import bpy

    active_user_id = getattr(bpy.context.window_manager, 'blender_id_active_profile', None)
    if not active_user_id:
        return None

    import blender_id.profiles
    return blender_id.profiles.get_active_profile()


def pillar_api() -> pillarsdk.Api:
    """Returns the Pillar SDK API object for the current user.

    The user must be logged in.
    """

    global _pillar_api
    import bpy

    # Only return the Pillar API object if the user is still logged in.
    profile = blender_id_profile()
    if not profile:
        raise UserNotLoggedInError()

    if _pillar_api is None:
        endpoint = bpy.context.user_preferences.addons['blender_cloud'].preferences.pillar_server
        _pillar_api = pillarsdk.Api(endpoint=endpoint,
                                    username=profile['username'],
                                    password=None,
                                    token=profile['token'])

    return _pillar_api


async def get_project_uuid(project_url: str) -> str:
    """Returns the UUID for the project, given its '/p/<project_url>' string."""

    find_one = functools.partial(pillarsdk.Project.find_one, {
        'where': {'url': project_url},
        'projection': {'permissions': 1},
    }, api=pillar_api())

    loop = asyncio.get_event_loop()
    try:
        project = await loop.run_in_executor(None, find_one)
    except pillarsdk.exceptions.ResourceNotFound:
        log.error('Project with URL %r does not exist', project_url)
        return None

    log.info('Found project %r', project)
    return project['_id']


async def get_nodes(project_uuid: str = None, parent_node_uuid: str = None,
                    node_type: str = None) -> list:
    """Gets nodes for either a project or given a parent node.

    @param project_uuid: the UUID of the project, or None if only querying by parent_node_uuid.
    @param parent_node_uuid: the UUID of the parent node. Can be the empty string if the
        node should be a top-level node in the project. Can also be None to query all nodes in a
        project. In both these cases the project UUID should be given.
    """

    if not project_uuid and not parent_node_uuid:
        raise ValueError('get_nodes(): either project_uuid or parent_node_uuid must be given.')

    where = {'properties.status': 'published'}

    # Build the parent node where-clause
    if parent_node_uuid == '':
        where['parent'] = {'$exists': False}
    elif parent_node_uuid is not None:
        where['parent'] = parent_node_uuid

    # Build the project where-clause
    if project_uuid:
        where['project'] = project_uuid

    if node_type:
        where['node_type'] = node_type

    node_all = functools.partial(pillarsdk.Node.all, {
        'projection': {'name': 1, 'parent': 1, 'node_type': 1,
                       'properties.order': 1, 'properties.status': 1,
                       'properties.content_type': 1, 'picture': 1},
        'where': where,
        'sort': 'properties.order'}, api=pillar_api())

    loop = asyncio.get_event_loop()
    children = await loop.run_in_executor(None, node_all)

    return children['_items']


async def download_to_file(url, filename, chunk_size=10 * 1024, *, future: asyncio.Future = None):
    """Downloads a file via HTTP(S) directly to the filesystem."""

    loop = asyncio.get_event_loop()

    def perform_get_request():
        return requests.get(url, stream=True, verify=True)

    # Download the file in a different thread.
    def download_loop():
        with closing(req), open(filename, 'wb') as outfile:
            for block in req.iter_content(chunk_size=chunk_size):
                if is_cancelled(future):
                    raise asyncio.CancelledError('Downloading was cancelled')
                outfile.write(block)

    # Check for cancellation even before we start our GET request
    if is_cancelled(future):
        log.debug('Downloading was cancelled before doing the GET')
        raise asyncio.CancelledError('Downloading was cancelled')

    log.debug('Performing GET %s', url)
    req = await loop.run_in_executor(None, perform_get_request)
    log.debug('Done with GET %s', url)

    # After we performed the GET request, we should check whether we should start
    # the download at all.
    if is_cancelled(future):
        log.debug('Downloading was cancelled before downloading the GET response')
        raise asyncio.CancelledError('Downloading was cancelled')

    log.debug('Downloading response of GET %s', url)
    await loop.run_in_executor(None, download_loop)
    log.debug('Done downloading response of GET %s', url)


async def stream_thumb_to_file(file: pillarsdk.File, directory: str, desired_size: str, *,
                               future: asyncio.Future = None):
    """Streams a thumbnail to a file.

    @param file: the pillar File object that represents the image whose thumbnail to download.
    @param directory: the directory to save the file to.
    @param desired_size: thumbnail size
    @return: the absolute path of the downloaded file, or None if the task was cancelled before
        downloading finished.
    """

    api = pillar_api()

    if is_cancelled(future):
        log.debug('stream_thumb_to_file(): cancelled before fetching thumbnail URL from Pillar')
        return None

    loop = asyncio.get_event_loop()
    thumb_link = await loop.run_in_executor(None, functools.partial(
        file.thumbnail_file, desired_size, api=api))

    if thumb_link is None:
        raise ValueError("File {} has no thumbnail of size {}"
                         .format(file['_id'], desired_size))

    if is_cancelled(future):
        log.debug('stream_thumb_to_file(): cancelled before downloading file')
        return None

    root, ext = os.path.splitext(file['file_path'])
    thumb_fname = "{0}-{1}.jpg".format(root, desired_size)
    thumb_path = os.path.abspath(os.path.join(directory, thumb_fname))

    await download_to_file(thumb_link, thumb_path, future=future)

    return thumb_path


async def fetch_texture_thumbs(parent_node_uuid: str, desired_size: str,
                               thumbnail_directory: str,
                               *,
                               thumbnail_loading: callable,
                               thumbnail_loaded: callable,
                               future: asyncio.Future = None):
    """Generator, fetches all texture thumbnails in a certain parent node.

    @param parent_node_uuid: the UUID of the parent node. All sub-nodes will be downloaded.
    @param desired_size: size indicator, from 'sbtmlh'.
    @param thumbnail_directory: directory in which to store the downloaded thumbnails.
    @param thumbnail_loading: callback function that takes (node_id, pillarsdk.File object)
        parameters, which is called before a thumbnail will be downloaded. This allows you to
        show a "downloading" indicator.
    @param thumbnail_loaded: callback function that takes (node_id, pillarsdk.File object,
        thumbnail path) parameters, which is called for every thumbnail after it's been downloaded.
    @param future: Future that's inspected; if it is not None and cancelled, texture downloading
        is aborted.
    """

    api = pillar_api()
    loop = asyncio.get_event_loop()

    file_find = functools.partial(pillarsdk.File.find, params={
        'projection': {'filename': 1, 'variations': 1, 'width': 1, 'height': 1},
    }, api=api)

    async def handle_texture_node(texture_node):
        # Skip non-texture nodes, as we can't thumbnail them anyway.
        if texture_node['node_type'] != 'texture':
            return

        if is_cancelled(future):
            log.debug('fetch_texture_thumbs cancelled before finding File for texture %r',
                      texture_node['_id'])
            return

        # Find the File that belongs to this texture node
        pic_uuid = texture_node['picture']
        loop.call_soon_threadsafe(functools.partial(thumbnail_loading,
                                                    texture_node['_id'],
                                                    texture_node))
        file_desc = await loop.run_in_executor(None, file_find, pic_uuid)

        if file_desc is None:
            log.warning('Unable to find file for texture node %s', pic_uuid)
            thumb_path = None
        else:
            if is_cancelled(future):
                log.debug('fetch_texture_thumbs cancelled before downloading file %r',
                          file_desc['_id'])
                return

            # Save the thumbnail
            thumb_path = await stream_thumb_to_file(file_desc, thumbnail_directory, desired_size,
                                                    future=future)
            if thumb_path is None:
                # The task got cancelled, we should abort too.
                log.debug('fetch_texture_thumbs cancelled while downloading file %r',
                          file_desc['_id'])
                return

                # print('Texture node {} has file {}'.format(texture_node['_id'], thumb_path))

        loop.call_soon_threadsafe(functools.partial(thumbnail_loaded,
                                                    texture_node['_id'],
                                                    file_desc, thumb_path))

    # Download all texture nodes in parallel.
    log.debug('Getting child nodes of node %r', parent_node_uuid)
    texture_nodes = await get_nodes(parent_node_uuid=parent_node_uuid,
                                    node_type='texture')

    if is_cancelled(future):
        log.warning('fetch_texture_thumbs: Texture downloading cancelled')
        return

    # We don't want to gather too much in parallel, as it will make cancelling take more time.
    # This is caused by HTTP requests going out in parallel, and once the socket is open and
    # the GET request is sent, we can't cancel until the server starts streaming the response.
    chunk_size = 2
    for i in range(0, len(texture_nodes), chunk_size):
        chunk = texture_nodes[i:i + chunk_size]

        log.debug('fetch_texture_thumbs: Gathering texture[%i:%i] for parent node %r',
                  i, i + chunk_size, parent_node_uuid)
        coros = (handle_texture_node(texture_node)
                 for texture_node in chunk)

        # raises any exception from failed handle_texture_node() calls.
        await asyncio.gather(*coros)

    log.info('fetch_texture_thumbs: Done downloading texture thumbnails')


def is_cancelled(future: asyncio.Future) -> bool:
    log.debug('%s.cancelled() = %s', future, future.cancelled())
    return future is not None and future.cancelled()


async def parent_node_uuid(node_uuid: str) -> str:
    """Returns the UUID of the node's parent node, or an empty string if this is the top level."""

    api = pillar_api()
    loop = asyncio.get_event_loop()

    log.debug('Finding parent node for node %r', node_uuid)
    find_node = functools.partial(pillarsdk.Node.find, node_uuid,
                                  {'projection': {'parent': 1}}, api=api)
    node = await loop.run_in_executor(None, find_node)
    if node is None:
        log.debug('Unable to find node %r, returning empty parent', node_uuid)
        return ''

    parent_uuid = node.parent or ''
    log.debug('Parent node of %r is %r', node_uuid, parent_uuid)
    return parent_uuid
