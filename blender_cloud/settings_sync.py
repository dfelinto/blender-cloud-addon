
"""Synchronises settings & startup file with the Cloud.
Caching is disabled on many PillarSDK calls, as synchronisation can happen
rapidly between multiple machines. This means that information can be outdated
in seconds, rather than the minutes the cache system assumes.
"""
import functools
import logging
import pathlib
import tempfile
import shutil

import bpy

import asyncio

import pillarsdk
from pillarsdk import exceptions as sdk_exceptions
from .pillar import pillar_call
from . import async_loop, pillar, cache, blendfile

SETTINGS_FILES_TO_UPLOAD = ['bookmarks.txt', 'recent-files.txt', 'userpref.blend', 'startup.blend']

# These are RNA keys inside the userpref.blend file, and their
# Python properties names.
LOCAL_SETTINGS_RNA = [
    (b'dpi', 'system.dpi'),
    (b'virtual_pixel', 'system.virtual_pixel_mode'),
    (b'compute_device_id', 'system.compute_device'),
    (b'compute_device_type', 'system.compute_device_type'),
    (b'tempdir', 'filepaths.temporary_directory'),
]

HOME_PROJECT_ENDPOINT = '/bcloud/home-project'
SYNC_GROUP_NODE_NAME = 'Blender Sync'
SYNC_GROUP_NODE_DESC = 'The [Blender Cloud Addon](https://cloud.blender.org/services' \
                       '#blender-addon) will synchronize your Blender settings here.'
log = logging.getLogger(__name__)


def set_blender_sync_status(set_status: str):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            bss = bpy.context.window_manager.blender_sync_status
            bss.status = set_status
            try:
                return func(*args, **kwargs)
            finally:
                bss.status = 'IDLE'

        return wrapper

    return decorator


def async_set_blender_sync_status(set_status: str):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            bss = bpy.context.window_manager.blender_sync_status
            bss.status = set_status
            try:
                return await func(*args, **kwargs)
            finally:
                bss.status = 'IDLE'

        return wrapper
    return decorator


async def get_home_project(params=None) -> pillarsdk.Project:
    """Returns the home project."""

    log.debug('Getting home project')
    try:
        return await pillar_call(pillarsdk.Project.find_from_endpoint,
                                 HOME_PROJECT_ENDPOINT, params=params)
    except sdk_exceptions.ForbiddenAccess:
        log.warning('Access to the home project was denied. '
                    'Double-check that you are a cloud subscriber and logged in.')
        raise


async def get_home_project_id():
    home_proj = await get_home_project({'projection': {'_id': 1}})
    home_proj_id = home_proj['_id']
    return home_proj_id


async def find_sync_group_id(home_project_id: str,
                             user_id: str,
                             blender_version: str,
                             *,
                             may_create=True) -> str:
    """Finds the group node in which to store sync assets.

    If the group node doesn't exist and may_create=True, it creates it.
    """

    # Find/create the top-level sync group node.
    try:
        sync_group, created = await find_or_create_node(
            where={'project': home_project_id,
                   'node_type': 'group',
                   'parent': None,
                   'name': SYNC_GROUP_NODE_NAME,
                   'user': user_id},
            additional_create_props={
                'description': SYNC_GROUP_NODE_DESC,
                'properties': {'status': 'published'},
            },
            projection={'_id': 1},
            may_create=may_create)
    except pillar.PillarError:
        raise pillar.PillarError('Unable to create sync folder on the Cloud')

    if not may_create and sync_group is None:
        log.info("Sync folder doesn't exist, and not creating it either.")
        return None, None

    # Find/create the sub-group for the requested Blender version
    try:
        sub_sync_group, created = await find_or_create_node(
            where={'project': home_project_id,
                   'node_type': 'group',
                   'parent': sync_group['_id'],
                   'name': blender_version,
                   'user': user_id},
            additional_create_props={
                'description': 'Sync folder for Blender %s' % blender_version,
                'properties': {'status': 'published'},
            },
            projection={'_id': 1},
            may_create=may_create)
    except pillar.PillarError:
        raise pillar.PillarError('Unable to create sync folder on the Cloud')

    if not may_create and sub_sync_group is None:
        log.info("Sync folder for Blender version %s doesn't exist, "
                 "and not creating it either.", blender_version)
        return sync_group['_id'], None

    return sync_group['_id'], sub_sync_group['_id']


async def find_or_create_node(where: dict,
                              additional_create_props: dict,
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

    created = False
    if found_node is None:
        if not may_create:
            return None, False

        log.info('Creating new sync group node')

        # Augment the node properties to form a complete node.
        node_props = where.copy()
        node_props.update(additional_create_props)

        found_node = pillarsdk.Node.new(node_props)
        created_ok = await pillar_call(found_node.create)
        if not created_ok:
            log.error('Blender Cloud addon: unable to create node on the Cloud.')
            raise pillar.PillarError('Unable to create node on the Cloud')
        created = True

    return found_node, created


async def attach_file_to_group(file_path: pathlib.Path,
                               home_project_id: str,
                               group_node_id: str,
                               user_id: str,
                               *,
                               future=None) -> pillarsdk.Node:
    """Creates an Asset node and attaches a file document to it."""

    # First upload the file...
    file_id = await pillar.upload_file(home_project_id, file_path,
                                       future=future)

    # Then attach it to a node.
    node, created = await find_or_create_node(
        where={
            'project': home_project_id,
            'node_type': 'asset',
            'parent': group_node_id,
            'name': file_path.name,
            'user': user_id},
        additional_create_props={
            'properties': {'file': file_id},
        })

    if not created:
        # Update the existing node.
        node.properties = {'file': file_id}
        updated_ok = await pillar_call(node.update)
        if not updated_ok:
            log.error(
                'Blender Cloud addon: unable to update asset node on the Cloud for file %s.',
                file_path)
            raise pillar.PillarError(
                'Unable to update asset node on the Cloud for file %s' % file_path.name)

    return node


@functools.lru_cache()
async def available_blender_versions(home_project_id: str, user_id: str) -> list:
    bss = bpy.context.window_manager.blender_sync_status

    # Get the available Blender versions.
    sync_group = await pillar_call(
        pillarsdk.Node.find_first,
        params={
            'where': {'project': home_project_id,
                      'node_type': 'group',
                      'parent': None,
                      'name': SYNC_GROUP_NODE_NAME,
                      'user': user_id},
            'projection': {'_id': 1},
        },
        caching=False)

    if sync_group is None:
        bss.report({'ERROR'}, 'No synced Blender settings in your home project')
        log.warning('No synced Blender settings in your home project')
        log.debug('-- unable to find sync group for home_project_id=%r and user_id=%r',
                  home_project_id, user_id)
        return []

    sync_nodes = await pillar_call(
        pillarsdk.Node.all,
        params={
            'where': {'project': home_project_id,
                      'node_type': 'group',
                      'parent': sync_group['_id'],
                      'user': user_id},
            'projection': {'_id': 1, 'name': 1},
            'sort': '-name',
        },
        caching=False)

    if not sync_nodes or not sync_nodes._items:
        bss.report({'ERROR'}, 'No synced Blender settings in your home project')
        log.warning('No synced Blender settings in your home project')
        return []

    versions = sync_nodes._items
    log.info('Versions: %s', versions)

    return [node.name for node in versions]


# noinspection PyAttributeOutsideInit
class PILLAR_OT_sync(pillar.PillarOperatorMixin,
                     async_loop.AsyncModalOperatorMixin,
                     bpy.types.Operator):
    bl_idname = 'pillar.sync'
    bl_label = 'Synchronise with Blender Cloud'

    log = logging.getLogger('bpy.ops.%s' % bl_idname)
    home_project_id = None
    sync_group_id = None  # top-level sync group node ID
    sync_group_versioned_id = None  # sync group node ID for the given Blender version.

    action = bpy.props.EnumProperty(
        items=[
            ('PUSH', 'Push', 'Push settings to the Blender Cloud'),
            ('PULL', 'Pull', 'Pull settings from the Blender Cloud'),
            ('REFRESH', 'Refresh', 'Refresh available versions'),
        ],
        name='action')

    blender_version = bpy.props.StringProperty(name='blender_version',
                                               description='Blender version to sync for',
                                               default='%i.%i' % bpy.app.version[:2])

    def bss_report(self, level, message):
        bss = bpy.context.window_manager.blender_sync_status
        bss.report(level, message)

    def invoke(self, context, event):
        self.log.info('at invoke: self = %r', self)

        self.log.info('Pulling from Blender %s', self.blender_version)
        if not self.blender_version:
            self.bss_report({'ERROR'}, 'No Blender version to sync for was given.')
            return {'CANCELLED'}

        async_loop.AsyncModalOperatorMixin.invoke(self, context, event)

        self.log.info('Starting synchronisation')
        self._new_async_task(self.async_execute(context))
        return {'RUNNING_MODAL'}

    @async_set_blender_sync_status('SYNCING')
    async def async_execute(self, context):
        """Entry point of the asynchronous operator."""

        self.bss_report({'INFO'}, 'Synchronizing settings %s with Blender Cloud' % self.action)

        try:
            self.user_id = await self.check_credentials(context)
            try:
                self.home_project_id = await get_home_project_id()
            except sdk_exceptions.ForbiddenAccess:
                self.log.exception('Forbidden access to home project.')
                self.bss_report({'ERROR'}, 'Did not get access to home project.')
                self._state = 'QUIT'
                return

            # Only create the folder structure if we're pushing.
            may_create = self.action == 'PUSH'
            try:
                gid, subgid = await find_sync_group_id(self.home_project_id,
                                                       self.user_id,
                                                       self.blender_version,
                                                       may_create=may_create)
                self.sync_group_id = gid
                self.sync_group_versioned_id = subgid
                self.log.info('Found top-level group node ID: %s', self.sync_group_id)
                self.log.info('Found group node ID for %s: %s',
                              self.blender_version, self.sync_group_versioned_id)
            except sdk_exceptions.ForbiddenAccess:
                self.log.exception('Unable to find Group ID')
                self.bss_report({'ERROR'}, 'Unable to find sync folder.')
                self._state = 'QUIT'
                return

            # Perform the requested action.
            action = {
                'PUSH': self.action_push,
                'PULL': self.action_pull,
                'REFRESH': self.action_refresh,
            }[self.action]
            await action(context)
        except Exception as ex:
            self.log.exception('Unexpected exception caught.')
            self.bss_report({'ERROR'}, 'Unexpected error: %s' % ex)

        self._state = 'QUIT'

    async def action_push(self, context):
        """Sends files to the Pillar server."""

        self.log.info('Saved user preferences to disk before pushing to cloud.')
        bpy.ops.wm.save_userpref()

        config_dir = pathlib.Path(bpy.utils.user_resource('CONFIG'))

        for fname in SETTINGS_FILES_TO_UPLOAD:
            path = config_dir / fname
            if not path.exists():
                self.log.debug('Skipping non-existing %s', path)
                continue

            self.bss_report({'INFO'}, 'Uploading %s' % fname)
            await attach_file_to_group(path,
                                       self.home_project_id,
                                       self.sync_group_versioned_id,
                                       self.user_id,
                                       future=self.signalling_future)

        self.bss_report({'INFO'}, 'Settings pushed to Blender Cloud.')

    async def action_pull(self, context):
        """Loads files from the Pillar server."""

        # Refuse to start if the file hasn't been saved.
        if context.blend_data.is_dirty:
            self.bss_report({'ERROR'}, 'Please save your Blend file before pulling'
                                   ' settings from the Blender Cloud.')
            return

        # If the sync group node doesn't exist, offer a list of groups that do.
        if self.sync_group_id is None:
            self.bss_report({'ERROR'}, 'There are no synced Blender settings in your home project.')
            return

        if self.sync_group_versioned_id is None:
            self.bss_report({'ERROR'}, 'Therre are no synced Blender settings for version %s' %
                        self.blender_version)
            return

        self.bss_report({'INFO'}, 'Pulling settings from Blender Cloud')
        with tempfile.TemporaryDirectory(prefix='bcloud-sync') as tempdir:
            for fname in SETTINGS_FILES_TO_UPLOAD:
                await self.download_settings_file(fname, tempdir)

        self.bss_report({'WARNING'}, 'Settings pulled from Cloud, restart Blender to load them.')
        self.log.info('at end: self = %r', self)

    async def action_refresh(self, context):
        self.bss_report({'INFO'}, 'Refreshing available Blender versions.')

        # Clear the LRU cache of available_blender_versions so that we can
        # obtain new versions (if someone synced from somewhere else, for example)
        available_blender_versions.cache_clear()

        versions = await available_blender_versions(self.home_project_id, self.user_id)
        bss = bpy.context.window_manager.blender_sync_status
        bss['available_blender_versions'] = versions

        self.bss_report({'INFO'}, '')


    async def download_settings_file(self, fname: str, temp_dir: str):
        config_dir = pathlib.Path(bpy.utils.user_resource('CONFIG'))
        meta_path = cache.cache_directory('home-project', 'blender-sync')

        self.bss_report({'INFO'}, 'Downloading %s from Cloud' % fname)

        # Get the asset node
        node_props = {'project': self.home_project_id,
                      'node_type': 'asset',
                      'parent': self.sync_group_versioned_id,
                      'name': fname}
        node = await pillar_call(pillarsdk.Node.find_first, {
            'where': node_props,
            'projection': {'_id': 1, 'properties.file': 1}
        }, caching=False)
        if node is None:
            self.bss_report({'INFO'}, 'Unable to find %s on Blender Cloud' % fname)
            self.log.info('Unable to find node on Blender Cloud for %s', fname)
            return

        async def file_downloaded(file_path: str, file_desc: pillarsdk.File):
            # Allow the caller to adjust the file before we move it into place.

            if fname.lower() == 'userpref.blend':
                await self.update_userpref_blend(file_path)

            # Move the file next to the final location; as it may be on a
            # different filesystem than the temporary directory, this can
            # fail, and we don't want to destroy the existing file.
            local_temp = config_dir / (fname + '~')
            local_final = config_dir / fname

            # Make a backup copy of the file as it was before pulling.
            if local_final.exists():
                local_bak = config_dir / (fname + '-pre-bcloud-pull')
                self.move_file(local_final, local_bak)

            self.move_file(file_path, local_temp)
            self.move_file(local_temp, local_final)

        file_id = node.properties.file
        await pillar.download_file_by_uuid(file_id,
                                           temp_dir,
                                           str(meta_path),
                                           file_loaded_sync=file_downloaded,
                                           future=self.signalling_future)

    def move_file(self, src, dst):
        self.log.info('Moving %s to %s', src, dst)
        shutil.move(str(src), str(dst))

    async def update_userpref_blend(self, file_path: str):
        self.log.info('Overriding machine-local settings in %s', file_path)

        # Remember some settings that should not be overwritten from the Cloud.
        up = bpy.context.user_preferences
        remembered = {}
        for rna_key, python_key in LOCAL_SETTINGS_RNA:
            assert '.' in python_key, 'Sorry, this code assumes there is a dot in the Python key'

            try:
                value = up.path_resolve(python_key)
            except ValueError:
                # Setting doesn't exist. This can happen, for example Cycles
                # settings on a build that doesn't have Cycles enabled.
                continue

            # Map enums from strings (in Python) to ints (in DNA).
            dot_index = python_key.rindex('.')
            parent_key, prop_key = python_key[:dot_index], python_key[dot_index + 1:]
            parent = up.path_resolve(parent_key)
            prop = parent.bl_rna.properties[prop_key]
            if prop.type == 'ENUM':
                log.debug('Rewriting %s from %r to %r',
                          python_key, value, prop.enum_items[value].value)
                value = prop.enum_items[value].value
            else:
                log.debug('Keeping value of %s: %r', python_key, value)

            remembered[rna_key] = value
        log.debug('Overriding values: %s', remembered)

        # Rewrite the userprefs.blend file to override the options.
        with blendfile.open_blend(file_path, 'rb+') as blend:
            prefs = next(block for block in blend.blocks
                         if block.code == b'USER')

            for key, value in remembered.items():
                self.log.debug('prefs[%r] = %r' % (key, prefs[key]))
                self.log.debug('  -> setting prefs[%r] = %r' % (key, value))
                prefs[key] = value


def register():
    bpy.utils.register_class(PILLAR_OT_sync)


def unregister():
    bpy.utils.unregister_class(PILLAR_OT_sync)
