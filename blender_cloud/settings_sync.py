"""Synchronises settings & startup file with the Cloud.

Caching is disabled on many PillarSDK calls, as synchronisation can happen
rapidly between multiple machines. This means that information can be outdated
in seconds, rather than the minutes the cache system assumes.
"""

import logging
import pathlib
import tempfile
import shutil

import bpy

import pillarsdk
from pillarsdk import exceptions as sdk_exceptions
from .pillar import pillar_call
from . import async_loop, pillar, cache

SETTINGS_FILES_TO_UPLOAD = ['bookmarks.txt', 'recent-files.txt', 'userpref.blend', 'startup.blend']
LOCAL_SETTINGS = [
    'system.dpi',
    'system.virtual_pixel_mode',
    'system.compute_device',
    'filepaths.temporary_directory',
]

HOME_PROJECT_ENDPOINT = '/bcloud/home-project'
SYNC_GROUP_NODE_NAME = 'Blender Sync'
SYNC_GROUP_NODE_DESC = 'The [Blender Cloud Addon](https://cloud.blender.org/services' \
                       '#blender-addon) will synchronize your Blender settings here.'
log = logging.getLogger(__name__)


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


# noinspection PyAttributeOutsideInit
class PILLAR_OT_sync(async_loop.AsyncModalOperatorMixin, bpy.types.Operator):
    bl_idname = 'pillar.sync'
    bl_label = 'Synchronise with Blender Cloud'

    log = logging.getLogger('bpy.ops.%s' % bl_idname)

    action = bpy.props.EnumProperty(
        items=[
            ('PUSH', 'Push', 'Push settings to the Blender Cloud'),
            ('PULL', 'Pull', 'Pull settings from the Blender Cloud'),
        ],
        name='action',
        description='Synchronises settings with the Blender Cloud.')

    blender_version = bpy.props.StringProperty(name='blender_version',
                                               description='Blender version to sync for',
                                               default='%i.%i' % bpy.app.version[:2])

    def invoke(self, context, event):
        if not self.blender_version:
            self.report({'ERROR'}, 'No Blender version to sync for was given.')
            return {'CANCELLED'}

        async_loop.AsyncModalOperatorMixin.invoke(self, context, event)

        log.info('Starting synchronisation')
        self._new_async_task(self.async_execute(context))
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        result = async_loop.AsyncModalOperatorMixin.modal(self, context, event)
        if not {'PASS_THROUGH', 'RUNNING_MODAL'}.intersection(result):
            self.log.info('Stopped')
            return result

        return {'PASS_THROUGH'}

    async def check_credentials(self, context) -> bool:
        """Checks credentials with Pillar, and if ok async-executes the operator."""

        self.report({'INFO'}, 'Checking Blender Cloud credentials')

        try:
            user_id = await pillar.check_pillar_credentials()
        except pillar.NotSubscribedToCloudError:
            self.log.warning(
                'Please subscribe to the blender cloud at https://cloud.blender.org/join')
            self.report({'INFO'},
                        'Please subscribe to the blender cloud at https://cloud.blender.org/join')
            return None
        except pillar.CredentialsNotSyncedError:
            self.log.info('Credentials not synced, re-syncing automatically.')
        else:
            self.log.info('Credentials okay.')
            return user_id

        try:
            user_id = await pillar.refresh_pillar_credentials()
        except pillar.NotSubscribedToCloudError:
            self.log.warning(
                'Please subscribe to the blender cloud at https://cloud.blender.org/join')
            self.report({'INFO'},
                        'Please subscribe to the blender cloud at https://cloud.blender.org/join')
            return None
        except pillar.UserNotLoggedInError:
            self.log.error('User not logged in on Blender ID.')
        else:
            self.log.info('Credentials refreshed and ok.')
            return user_id

        return None

    async def async_execute(self, context):
        """Entry point of the asynchronous operator."""

        self.report({'INFO'}, 'Synchronizing settings %s with Blender Cloud' % self.action)

        try:
            self.user_id = await self.check_credentials(context)
            try:
                self.home_project_id = await get_home_project_id()
            except sdk_exceptions.ForbiddenAccess:
                self.log.exception('Forbidden access to home project.')
                self.report({'ERROR'}, 'Did not get access to home project.')
                self._state = 'QUIT'
                return

            try:
                self.sync_group_id = await self.find_sync_group_id()
                self.log.info('Found group node ID: %s', self.sync_group_id)
            except sdk_exceptions.ForbiddenAccess:
                self.log.exception('Unable to find Group ID')
                self.report({'ERROR'}, 'Unable to find sync folder.')
                self._state = 'QUIT'
                return

            action = {
                'PUSH': self.action_push,
                'PULL': self.action_pull,
            }[self.action]
            await action(context)
        except Exception as ex:
            self.log.exception('Unexpected exception caught.')
            self.report({'ERROR'}, 'Unexpected error: %s' % ex)

        try:
            self._state = 'QUIT'
        except ReferenceError:
            # This happens after the call to bpy.ops.wm.read_homefile() in action_pull().
            # That call erases the StructRNA of this operator. As a result, it no longer
            # runs as a modal operator. The currently running Python code is allowed
            # to finish, though.
            pass

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

            self.report({'INFO'}, 'Uploading %s' % fname)
            await self.attach_file_to_group(path)

        self.report({'INFO'}, 'Settings pushed to Blender Cloud.')

    async def action_pull(self, context):
        """Loads files from the Pillar server."""

        # Refuse to start if the file hasn't been saved.
        if context.blend_data.is_dirty:
            self.report({'ERROR'}, 'Please save your Blend file before pulling'
                                   ' settings from the Blender Cloud.')
            return

        self.report({'INFO'}, 'Pulling settings from Blender Cloud')

        with tempfile.TemporaryDirectory(prefix='bcloud-sync') as tempdir:
            for fname in SETTINGS_FILES_TO_UPLOAD:
                await self.download_settings_file(fname, tempdir)
        await self.reload_after_pull()

    async def download_settings_file(self, fname: str, temp_dir: str):
        config_dir = pathlib.Path(bpy.utils.user_resource('CONFIG'))
        meta_path = cache.cache_directory('home-project', 'blender-sync')

        self.report({'INFO'}, 'Downloading %s from Cloud' % fname)

        # Get the asset node
        node_props = {'project': self.home_project_id,
                      'node_type': 'asset',
                      'parent': self.sync_group_id,
                      'name': fname}
        node = await pillar_call(pillarsdk.Node.find_first, {
            'where': node_props,
            'projection': {'_id': 1, 'properties.file': 1}
        }, caching=False)
        if node is None:
            self.report({'WARNING'}, 'Unable to find %s on Blender Cloud' % fname)
            self.log.warning('Unable to find node on Blender Cloud for %s', fname)
            return

        def file_downloaded(file_path: str, file_desc: pillarsdk.File):
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
                                           file_loaded=file_downloaded,
                                           future=self.signalling_future)

    def move_file(self, src, dst):
        self.log.info('Moving %s to %s', src, dst)
        shutil.move(str(src), str(dst))

    async def reload_after_pull(self):
        self.report({'WARNING'}, 'Settings pulled from Blender Cloud, reloading.')
        from pprint import pprint

        # Remember some settings that should not be overwritten.
        up = bpy.context.user_preferences
        remembered = {}
        for key in LOCAL_SETTINGS:
            try:
                value = up.path_resolve(key)
            except ValueError:
                # Setting doesn't exist. This can happen, for example Cycles
                # settings on a build that doesn't have Cycles enabled.
                continue
            remembered[key] = value
        print('REMEMBERED:')
        pprint(remembered)

        # This call is tricy, as Blender destroys this modal operator's StructRNA.
        # However, the Python code keeps running, so we have to be very careful
        # what we do afterwards.
        log.warning('Reloading home files (i.e. userprefs and startup)')
        bpy.ops.wm.read_homefile()

        # Restore those settings again.
        up = bpy.context.user_preferences
        for key, value in remembered.items():
            if '.' in key:
                last_dot = key.rindex('.')
                parent, key = key[:last_dot], key[last_dot + 1:]
                set_on = up.path_resolve(parent)
            else:
                set_on = up
            print('RESTORING: %s.%s=%s' % (set_on, key, value))
            setattr(set_on, key, value)

        # Save the now-adjusted user settings.
        bpy.ops.wm.save_userpref()

        # The read_homefile() call stops any running modal operator, so we have to be
        # very careful with our asynchronous loop. Since it didn't stop by
        # its own accord (because the current async task is still running),
        # we need to shut it down forcefully.
        async_loop.erase_async_loop()

    async def find_or_create_node(self,
                                  where: dict,
                                  additional_create_props: dict,
                                  projection: dict = None) -> (pillarsdk.Node, bool):
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

    async def find_sync_group_id(self) -> str:
        """Finds the group node in which to store sync assets.

        If the group node doesn't exist, it creates it.
        """

        # Find/create the top-level sync group node.
        try:
            sync_group, created = await self.find_or_create_node(
                where={'project': self.home_project_id,
                       'node_type': 'group',
                       'parent': None,
                       'name': SYNC_GROUP_NODE_NAME,
                       'user': self.user_id},
                additional_create_props={
                    'description': SYNC_GROUP_NODE_DESC,
                    'properties': {'status': 'published'},
                },
                projection={'_id': 1})
        except pillar.PillarError:
            raise pillar.PillarError('Unable to create sync folder on the Cloud')

        # Find/create the sub-group for the requested Blender version
        try:
            sub_sync_group, created = await self.find_or_create_node(
                where={'project': self.home_project_id,
                       'node_type': 'group',
                       'parent': sync_group['_id'],
                       'name': self.blender_version,
                       'user': self.user_id},
                additional_create_props={
                    'description': 'Sync folder for Blender %s' % self.blender_version,
                    'properties': {'status': 'published'},
                },
                projection={'_id': 1})
        except pillar.PillarError:
            raise pillar.PillarError('Unable to create sync folder on the Cloud')

        return sub_sync_group['_id']

    async def attach_file_to_group(self, file_path: pathlib.Path) -> pillarsdk.Node:
        """Creates an Asset node and attaches a file document to it."""

        # First upload the file...
        file_id = await pillar.upload_file(self.home_project_id, file_path,
                                           future=self.signalling_future)

        # Then attach it to a node.
        node, created = await self.find_or_create_node(
            where={
                'project': self.home_project_id,
                'node_type': 'asset',
                'parent': self.sync_group_id,
                'name': file_path.name,
                'user': self.user_id},
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


def draw_userpref_header(self: bpy.types.USERPREF_HT_header, context):
    """Adds some buttons to the userprefs header."""

    layout = self.layout
    layout.operator('pillar.sync', icon='FILE_REFRESH',
                    text='Push to Cloud').action = 'PUSH'
    layout.operator('pillar.sync', icon='FILE_REFRESH',
                    text='Pull from Cloud').action = 'PULL'


def register():
    bpy.utils.register_class(PILLAR_OT_sync)
    bpy.types.USERPREF_HT_header.append(draw_userpref_header)


def unregister():
    bpy.utils.unregister_class(PILLAR_OT_sync)
    bpy.types.USERPREF_HT_header.remove(draw_userpref_header)
