import asyncio
import json
import os
import functools
import logging
from contextlib import closing, contextmanager
import urllib.parse
import pathlib

import requests
import requests.structures
import pillarsdk
import pillarsdk.exceptions
import pillarsdk.utils
from pillarsdk.utils import sanitize_filename

from . import cache

SUBCLIENT_ID = 'PILLAR'

_pillar_api = {}  # will become a mapping from bool (cached/non-cached) to pillarsdk.Api objects.
log = logging.getLogger(__name__)
uncached_session = requests.session()
_testing_blender_id_profile = None  # Just for testing, overrides what is returned by blender_id_profile.
_downloaded_urls = set()  # URLs we've downloaded this Blender session.


class UserNotLoggedInError(RuntimeError):
    """Raised when the user should be logged in on Blender ID, but isn't.

    This is basically for every interaction with Pillar.
    """

    def __str__(self):
        return self.__class__.__name__


class CredentialsNotSyncedError(UserNotLoggedInError):
    """Raised when the user may be logged in on Blender ID, but has no Blender Cloud token."""


class NotSubscribedToCloudError(UserNotLoggedInError):
    """Raised when the user may be logged in on Blender ID, but has no Blender Cloud token."""


class PillarError(RuntimeError):
    """Raised when there is some issue with the communication with Pillar.

    This is only raised for logical errors (for example nodes that should
    exist but still can't be found). HTTP communication errors are signalled
    with other exceptions.
    """


class CloudPath(pathlib.PurePosixPath):
    """Cloud path, in the form of /project uuid/node uuid/node uuid/...

    The components are:
        - the root '/'
        - the project UUID
        - zero or more node UUIDs.
    """

    @property
    def project_uuid(self) -> str:
        assert self.parts[0] == '/'
        if len(self.parts) <= 1:
            return None
        return self.parts[1]

    @property
    def node_uuids(self) -> list:
        assert self.parts[0] == '/'
        return self.parts[2:]

    @property
    def node_uuid(self) -> str:
        if len(self.parts) <= 2:
            return None

        return self.parts[-1]


@contextmanager
def with_existing_dir(filename: str, open_mode: str, encoding=None):
    """Opens a file, ensuring its directory exists."""

    directory = os.path.dirname(filename)
    if not os.path.exists(directory):
        log.debug('Creating directory %s', directory)
        os.makedirs(directory, exist_ok=True)
    with open(filename, open_mode, encoding=encoding) as file_object:
        yield file_object


def save_as_json(pillar_resource, json_filename):
    with with_existing_dir(json_filename, 'w') as outfile:
        log.debug('Saving metadata to %r' % json_filename)
        json.dump(pillar_resource, outfile, sort_keys=True, cls=pillarsdk.utils.PillarJSONEncoder)


def blender_id_profile() -> 'blender_id.BlenderIdProfile':
    """Returns the Blender ID profile of the currently logged in user."""

    # Allow overriding before we import the bpy module.
    if _testing_blender_id_profile is not None:
        return _testing_blender_id_profile

    import blender_id
    return blender_id.get_active_profile()


def blender_id_subclient() -> dict:
    """Returns the subclient dict, containing the 'subclient_user_id' and 'token' keys."""

    profile = blender_id_profile()
    if not profile:
        raise UserNotLoggedInError()

    subclient = profile.subclients.get(SUBCLIENT_ID)
    if not subclient:
        raise CredentialsNotSyncedError()

    return subclient


def pillar_api(pillar_endpoint: str = None, caching=True) -> pillarsdk.Api:
    """Returns the Pillar SDK API object for the current user.

    The user must be logged in.

    :param pillar_endpoint: URL of the Pillar server, for testing purposes. If not specified,
        it will use the addon preferences.
    :param caching: whether to return a caching or non-caching API
    """

    global _pillar_api

    # Only return the Pillar API object if the user is still logged in.
    subclient = blender_id_subclient()

    if not _pillar_api:
        # Allow overriding the endpoint before importing Blender-specific stuff.
        if pillar_endpoint is None:
            from . import blender
            pillar_endpoint = blender.preferences().pillar_server

        _caching_api = pillarsdk.Api(endpoint=pillar_endpoint,
                                     username=subclient['subclient_user_id'],
                                     password=SUBCLIENT_ID,
                                     token=subclient['token'])
        _caching_api.requests_session = cache.requests_session()

        _noncaching_api = pillarsdk.Api(endpoint=pillar_endpoint,
                                        username=subclient['subclient_user_id'],
                                        password=SUBCLIENT_ID,
                                        token=subclient['token'])
        _noncaching_api.requests_session = uncached_session

        _pillar_api = {
            True: _caching_api,
            False: _noncaching_api,
        }

    return _pillar_api[caching]


# No more than this many Pillar calls should be made simultaneously
pillar_semaphore = asyncio.Semaphore(3)


async def pillar_call(pillar_func, *args, caching=True, **kwargs):
    partial = functools.partial(pillar_func, *args, api=pillar_api(caching=caching), **kwargs)
    loop = asyncio.get_event_loop()

    async with pillar_semaphore:
        return await loop.run_in_executor(None, partial)


async def check_pillar_credentials(required_roles: set):
    """Tries to obtain the user at Pillar using the user's credentials.

    :param required_roles: set of roles to require -- having one of those is enough.
    :raises UserNotLoggedInError: when the user is not logged in on Blender ID.
    :raises CredentialsNotSyncedError: when the user is logged in on Blender ID but
        doesn't have a valid subclient token for Pillar.
    :returns: the Pillar User ID of the current user.
    """

    profile = blender_id_profile()
    if not profile:
        raise UserNotLoggedInError()

    subclient = profile.subclients.get(SUBCLIENT_ID)
    if not subclient:
        raise CredentialsNotSyncedError()

    pillar_user_id = subclient['subclient_user_id']
    if not pillar_user_id:
        raise CredentialsNotSyncedError()

    try:
        db_user = await pillar_call(pillarsdk.User.me)
    except (pillarsdk.UnauthorizedAccess, pillarsdk.ResourceNotFound, pillarsdk.ForbiddenAccess):
        raise CredentialsNotSyncedError()

    roles = db_user.roles or set()
    log.debug('User has roles %r', roles)
    if required_roles and not required_roles.intersection(set(roles)):
        # Delete the subclient info. This forces a re-check later, which can
        # then pick up on the user's new status.
        del profile.subclients[SUBCLIENT_ID]
        profile.save_json()
        raise NotSubscribedToCloudError()

    return pillar_user_id


async def refresh_pillar_credentials(required_roles: set):
    """Refreshes the authentication token on Pillar.

    :raises blender_id.BlenderIdCommError: when Blender ID refuses to send a token to Pillar.
    :raises Exception: when the Pillar credential check fails.
    """

    global _pillar_api

    import blender_id

    from . import blender
    pillar_endpoint = blender.preferences().pillar_server.rstrip('/')

    # Create a subclient token and send it to Pillar.
    # May raise a blender_id.BlenderIdCommError
    try:
        blender_id.create_subclient_token(SUBCLIENT_ID, pillar_endpoint)
    except blender_id.communication.BlenderIdCommError as ex:
        log.warning("Unable to create authentication token: %s", ex)
        raise CredentialsNotSyncedError()

    # Test the new URL
    _pillar_api = None
    return await check_pillar_credentials(required_roles)


async def get_project_uuid(project_url: str) -> str:
    """Returns the UUID for the project, given its '/p/<project_url>' string."""

    try:
        project = await pillar_call(pillarsdk.Project.find_one, {
            'where': {'url': project_url},
            'projection': {'permissions': 1},
        })
    except pillarsdk.exceptions.ResourceNotFound:
        log.error('Project with URL %r does not exist', project_url)
        return None

    log.info('Found project %r', project)
    return project['_id']


async def get_nodes(project_uuid: str = None, parent_node_uuid: str = None,
                    node_type=None, max_results=None) -> list:
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
        if isinstance(node_type, str):
            where['node_type'] = node_type
        else:
            where['node_type'] = {'$in': node_type}

    params = {'projection': {'name': 1, 'parent': 1, 'node_type': 1, 'properties.order': 1,
                             'properties.status': 1, 'properties.files': 1,
                             'properties.content_type': 1, 'picture': 1},
              'where': where,
              'embed': ['parent']}

    # Pagination
    if max_results:
        params['max_results'] = int(max_results)

    children = await pillar_call(pillarsdk.Node.all, params)

    return children['_items']


async def get_texture_projects(max_results=None) -> list:
    """Returns project dicts that contain textures."""

    params = {}

    # Pagination
    if max_results:
        params['max_results'] = int(max_results)

    try:
        children = await pillar_call(pillarsdk.Project.all_from_endpoint,
                                     '/bcloud/texture-libraries',
                                     params=params)
    except pillarsdk.ResourceNotFound as ex:
        log.warning('Unable to find texture projects: %s', ex)
        raise PillarError('Unable to find texture projects: %s' % ex)

    return children['_items']


async def download_to_file(url, filename, *,
                           header_store: str,
                           chunk_size=100 * 1024,
                           future: asyncio.Future = None):
    """Downloads a file via HTTP(S) directly to the filesystem."""

    stored_headers = {}
    if os.path.exists(filename) and os.path.exists(header_store):
        log.debug('Loading cached headers %r', header_store)
        try:
            with open(header_store, 'r') as infile:
                stored_headers = requests.structures.CaseInsensitiveDict(json.load(infile))

            # Check file length.
            expected_content_length = int(stored_headers['Content-Length'])
            statinfo = os.stat(filename)
            if expected_content_length == statinfo.st_size:
                # File exists, and is of the correct length. Don't bother downloading again
                # if we already downloaded it this session.
                if url in _downloaded_urls:
                    log.debug('Already downloaded %s this session, skipping this request.',
                              url)
                    return
            else:
                log.debug('File size should be %i but is %i; ignoring cache.',
                          expected_content_length, statinfo.st_size)
                stored_headers = {}
        except Exception as ex:
            log.warning('Unable to load headers from %r, ignoring cache: %s', header_store, str(ex))

    loop = asyncio.get_event_loop()

    # Separated doing the GET and downloading the body of the GET, so that we can cancel
    # the download in between.

    def perform_get_request() -> requests.Request:
        headers = {}
        try:
            if stored_headers['Last-Modified']:
                headers['If-Modified-Since'] = stored_headers['Last-Modified']
        except KeyError:
            pass
        try:
            if stored_headers['ETag']:
                headers['If-None-Match'] = stored_headers['ETag']
        except KeyError:
            pass

        if is_cancelled(future):
            log.debug('Downloading was cancelled before doing the GET.')
            raise asyncio.CancelledError('Downloading was cancelled')
        log.debug('Performing GET request, waiting for response.')
        return uncached_session.get(url, headers=headers, stream=True, verify=True)

    # Download the file in a different thread.
    def download_loop():
        with with_existing_dir(filename, 'wb') as outfile:
            with closing(response):
                for block in response.iter_content(chunk_size=chunk_size):
                    if is_cancelled(future):
                        raise asyncio.CancelledError('Downloading was cancelled')
                    outfile.write(block)

    # Check for cancellation even before we start our GET request
    if is_cancelled(future):
        log.debug('Downloading was cancelled before doing the GET')
        raise asyncio.CancelledError('Downloading was cancelled')

    log.debug('Performing GET %s', url)
    response = await loop.run_in_executor(None, perform_get_request)
    log.debug('Status %i from GET %s', response.status_code, url)
    response.raise_for_status()

    if response.status_code == 304:
        # The file we have cached is still good, just use that instead.
        _downloaded_urls.add(url)
        return

    # After we performed the GET request, we should check whether we should start
    # the download at all.
    if is_cancelled(future):
        log.debug('Downloading was cancelled before downloading the GET response')
        raise asyncio.CancelledError('Downloading was cancelled')

    log.debug('Downloading response of GET %s', url)
    await loop.run_in_executor(None, download_loop)
    log.debug('Done downloading response of GET %s', url)

    # We're done downloading, now we have something cached we can use.
    log.debug('Saving header cache to %s', header_store)
    _downloaded_urls.add(url)

    with with_existing_dir(header_store, 'w') as outfile:
        json.dump({
            'ETag': str(response.headers.get('etag', '')),
            'Last-Modified': response.headers.get('Last-Modified'),
            'Content-Length': response.headers.get('Content-Length'),
        }, outfile, sort_keys=True)


async def fetch_thumbnail_info(file: pillarsdk.File, directory: str, desired_size: str):
    """Fetches thumbnail information from Pillar.

    @param file: the pillar File object that represents the image whose thumbnail to download.
    @param directory: the directory to save the file to.
    @param desired_size: thumbnail size
    @return: (url, path), where 'url' is the URL to download the thumbnail from, and 'path' is the absolute path of the
        where the thumbnail should be downloaded to. Returns None, None if the task was cancelled before downloading
        finished.
    """

    thumb_link = await pillar_call(file.thumbnail, desired_size)

    if thumb_link is None:
        raise ValueError("File {} has no thumbnail of size {}"
                         .format(file['_id'], desired_size))

    root, ext = os.path.splitext(file['file_path'])
    thumb_fname = sanitize_filename('{0}-{1}.jpg'.format(root, desired_size))
    thumb_path = os.path.abspath(os.path.join(directory, thumb_fname))

    return thumb_link, thumb_path


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
    @param thumbnail_loading: callback function that takes (pillarsdk.Node, pillarsdk.File)
        parameters, which is called before a thumbnail will be downloaded. This allows you to
        show a "downloading" indicator.
    @param thumbnail_loaded: callback function that takes (pillarsdk.Node, pillarsdk.File object,
        thumbnail path) parameters, which is called for every thumbnail after it's been downloaded.
    @param future: Future that's inspected; if it is not None and cancelled, texture downloading
        is aborted.
    """

    # Download all texture nodes in parallel.
    log.debug('Getting child nodes of node %r', parent_node_uuid)
    texture_nodes = await get_nodes(parent_node_uuid=parent_node_uuid,
                                    node_type='texture')

    if is_cancelled(future):
        log.warning('fetch_texture_thumbs: Texture downloading cancelled')
        return

    coros = (download_texture_thumbnail(texture_node, desired_size,
                                        thumbnail_directory,
                                        thumbnail_loading=thumbnail_loading,
                                        thumbnail_loaded=thumbnail_loaded,
                                        future=future)
             for texture_node in texture_nodes)

    # raises any exception from failed handle_texture_node() calls.
    await asyncio.gather(*coros)

    log.info('fetch_texture_thumbs: Done downloading texture thumbnails')


async def download_texture_thumbnail(texture_node, desired_size: str,
                                     thumbnail_directory: str,
                                     *,
                                     thumbnail_loading: callable,
                                     thumbnail_loaded: callable,
                                     future: asyncio.Future = None):
    # Skip non-texture nodes, as we can't thumbnail them anyway.
    if texture_node['node_type'] != 'texture':
        return

    if is_cancelled(future):
        log.debug('fetch_texture_thumbs cancelled before finding File for texture %r',
                  texture_node['_id'])
        return

    loop = asyncio.get_event_loop()

    # Find out which file to use for the thumbnail picture.
    pic_uuid = texture_node.picture
    if not pic_uuid:
        # Fall back to the first texture file, if it exists.
        log.debug('Node %r does not have a picture, falling back to first file.',
                  texture_node['_id'])
        files = texture_node.properties and texture_node.properties.files
        if not files:
            log.info('Node %r does not have a picture nor files, skipping.', texture_node['_id'])
            return
        pic_uuid = files[0].file
        if not pic_uuid:
            log.info('Node %r does not have a picture nor files, skipping.', texture_node['_id'])
            return

    # Load the File that belongs to this texture node's picture.
    loop.call_soon_threadsafe(thumbnail_loading, texture_node, texture_node)
    file_desc = await pillar_call(pillarsdk.File.find, pic_uuid, params={
        'projection': {'filename': 1, 'variations': 1, 'width': 1, 'height': 1},
    })

    if file_desc is None:
        log.warning('Unable to find file for texture node %s', pic_uuid)
        thumb_path = None
    else:
        if is_cancelled(future):
            log.debug('fetch_texture_thumbs cancelled before downloading file %r',
                      file_desc['_id'])
            return

        # Get the thumbnail information from Pillar
        thumb_url, thumb_path = await fetch_thumbnail_info(file_desc, thumbnail_directory,
                                                           desired_size)
        if thumb_path is None:
            # The task got cancelled, we should abort too.
            log.debug('fetch_texture_thumbs cancelled while downloading file %r',
                      file_desc['_id'])
            return

        # Cached headers are stored next to thumbnails in sidecar files.
        header_store = '%s.headers' % thumb_path

        await download_to_file(thumb_url, thumb_path, header_store=header_store, future=future)

    loop.call_soon_threadsafe(thumbnail_loaded, texture_node, file_desc, thumb_path)


async def download_file_by_uuid(file_uuid,
                                target_directory: str,
                                metadata_directory: str,
                                *,
                                map_type: str = None,
                                file_loading: callable = None,
                                file_loaded: callable = None,
                                file_loaded_sync: callable = None,
                                future: asyncio.Future):
    if is_cancelled(future):
        log.debug('download_file_by_uuid(%r) cancelled.', file_uuid)
        return

    loop = asyncio.get_event_loop()

    # Find the File document.
    file_desc = await pillar_call(pillarsdk.File.find, file_uuid, params={
        'projection': {'link': 1, 'filename': 1},
    })

    # Save the file document to disk
    metadata_file = os.path.join(metadata_directory, 'files', '%s.json' % file_uuid)
    save_as_json(file_desc, metadata_file)

    root, ext = os.path.splitext(file_desc['filename'])
    if map_type is None or root.endswith(map_type):
        target_filename = '%s%s' % (root, ext)
    else:
        target_filename = '%s-%s%s' % (root, map_type, ext)

    file_path = os.path.join(target_directory, sanitize_filename(target_filename))
    file_url = file_desc['link']
    # log.debug('Texture %r:\n%s', file_uuid, pprint.pformat(file_desc.to_dict()))
    if file_loading is not None:
        loop.call_soon_threadsafe(file_loading, file_path, file_desc)

    # Cached headers are stored in the project space
    header_store = os.path.join(metadata_directory, 'files',
                                sanitize_filename('%s.headers' % file_uuid))

    await download_to_file(file_url, file_path, header_store=header_store, future=future)

    if file_loaded is not None:
        loop.call_soon_threadsafe(file_loaded, file_path, file_desc)
    if file_loaded_sync is not None:
        await file_loaded_sync(file_path, file_desc)


async def download_texture(texture_node,
                           target_directory: str,
                           metadata_directory: str,
                           *,
                           texture_loading: callable,
                           texture_loaded: callable,
                           future: asyncio.Future):
    if texture_node['node_type'] != 'texture':
        raise TypeError("Node type should be 'texture', not %r" % texture_node['node_type'])

    # Download every file. Eve doesn't support embedding from a list-of-dicts.
    downloaders = (download_file_by_uuid(file_info['file'],
                                         target_directory,
                                         metadata_directory,
                                         map_type=file_info['map_type'],
                                         file_loading=texture_loading,
                                         file_loaded=texture_loaded,
                                         future=future)
                   for file_info in texture_node['properties']['files'])

    return await asyncio.gather(*downloaders, return_exceptions=True)


async def upload_file(project_id: str, file_path: pathlib.Path, *,
                      future: asyncio.Future) -> str:
    """Uploads a file to the Blender Cloud, returning a file document ID."""

    from .blender import PILLAR_SERVER_URL

    loop = asyncio.get_event_loop()
    url = urllib.parse.urljoin(PILLAR_SERVER_URL, '/storage/stream/%s' % project_id)

    # Upload the file in a different thread.
    def upload():
        auth_token = blender_id_subclient()['token']

        with file_path.open(mode='rb') as infile:
            return uncached_session.post(url,
                                         files={'file': infile},
                                         auth=(auth_token, SUBCLIENT_ID))

    # Check for cancellation even before we start our POST request
    if is_cancelled(future):
        log.debug('Uploading was cancelled before doing the POST')
        raise asyncio.CancelledError('Uploading was cancelled')

    log.debug('Performing POST %s', url)
    response = await loop.run_in_executor(None, upload)
    log.debug('Status %i from POST %s', response.status_code, url)
    response.raise_for_status()

    resp = response.json()
    log.debug('Upload response: %s', resp)

    try:
        file_id = resp['file_id']
    except KeyError:
        log.error('No file ID in upload response: %s', resp)
        raise PillarError('No file ID in upload response: %s' % resp)

    log.info('Uploaded %s to file ID %s', file_path, file_id)
    return file_id


def is_cancelled(future: asyncio.Future) -> bool:
    # assert future is not None  # for debugging purposes.
    cancelled = future is not None and future.cancelled()
    return cancelled


class PillarOperatorMixin:
    async def check_credentials(self, context, required_roles) -> bool:
        """Checks credentials with Pillar, and if ok returns the user ID.

        Returns None if the user cannot be found, or if the user is not a Cloud subscriber.
        """

        # self.report({'INFO'}, 'Checking Blender Cloud credentials')

        try:
            user_id = await check_pillar_credentials(required_roles)
        except NotSubscribedToCloudError:
            self._log_subscription_needed()
            raise
        except CredentialsNotSyncedError:
            self.log.info('Credentials not synced, re-syncing automatically.')
        else:
            self.log.info('Credentials okay.')
            return user_id

        try:
            user_id = await refresh_pillar_credentials(required_roles)
        except NotSubscribedToCloudError:
            self._log_subscription_needed()
            raise
        except UserNotLoggedInError:
            self.log.error('User not logged in on Blender ID.')
        else:
            self.log.info('Credentials refreshed and ok.')
            return user_id

        return None

    def _log_subscription_needed(self):
        self.log.warning(
            'Please subscribe to the blender cloud at https://cloud.blender.org/join')
        self.report({'INFO'},
                    'Please subscribe to the blender cloud at https://cloud.blender.org/join')


async def find_or_create_node(where: dict,
                              additional_create_props: dict = None,
                              projection: dict = None,
                              may_create: bool = True) -> (pillarsdk.Node, bool):
    """Finds a node by the `filter_props`, creates it using the additional props.

    :returns: tuple (node, created), where 'created' is a bool indicating whether
              a new node was created, or an exising one is returned.
    """

    params = {
        'where': where,
    }
    if projection:
        params['projection'] = projection

    found_node = await pillar_call(pillarsdk.Node.find_first, params, caching=False)

    if found_node is not None:
        return found_node, False

    if not may_create:
        return None, False

    # Augment the node properties to form a complete node.
    node_props = where.copy()
    if additional_create_props:
        node_props.update(additional_create_props)

    log.debug('Creating new node %s', node_props)
    created_node = pillarsdk.Node.new(node_props)
    created_ok = await pillar_call(created_node.create)
    if not created_ok:
        log.error('Blender Cloud addon: unable to create node on the Cloud.')
        raise PillarError('Unable to create node on the Cloud')

    return created_node, True


async def attach_file_to_group(file_path: pathlib.Path,
                               home_project_id: str,
                               group_node_id: str,
                               user_id: str = None) -> pillarsdk.Node:
    """Creates an Asset node and attaches a file document to it."""

    node = await pillar_call(pillarsdk.Node.create_asset_from_file,
                             home_project_id,
                             group_node_id,
                             'file',
                             str(file_path),
                             extra_where=user_id and {'user': user_id})

    return node
