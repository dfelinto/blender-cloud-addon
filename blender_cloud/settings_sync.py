"""Synchronizes settings & startup file with the Cloud."""
import asyncio
import logging

import bpy
import pillarsdk
from pillarsdk import exceptions as sdk_exceptions
from .pillar import pillar_call, check_pillar_credentials, PillarError
from . import async_loop

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

    def invoke(self, context, event):
        async_loop.AsyncModalOperatorMixin.invoke(self, context, event)

        log.info('Starting synchronisation')
        self._new_async_task(self.async_execute())
        self.report({'INFO'}, 'Synchronizing settings with Blender Cloud')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        result = async_loop.AsyncModalOperatorMixin.modal(self, context, event)
        if not {'PASS_THROUGH', 'RUNNING_MODAL'}.intersection(result):
            self.log.info('Stopped')
            return result

        return {'PASS_THROUGH'}

    async def async_execute(self):
        self.user_id = await check_pillar_credentials()
        try:
            group_id = await self.find_sync_group_id()
            self.log.info('Found group node ID: %s', group_id)
        except sdk_exceptions.ForbiddenAccess as ex:
            self.log.exception('Unable to find Group ID')

        self._state = 'QUIT'

    async def find_sync_group_id(self) -> pillarsdk.Node:
        """Finds the group node in which to store sync assets.

        If the group node doesn't exist, it creates it.
        """

        home_proj_id = await get_home_project_id()

        node_props = {'project': home_proj_id,
                      'node_type': 'group',
                      'parent': None,
                      'name': SYNC_GROUP_NODE_NAME,
                      'user': self.user_id}
        sync_group = await pillar_call(pillarsdk.Node.find_first, {
            'where': node_props,
            'projection': {'_id': 1}
        })

        if sync_group is None:
            log.debug('Creating new sync group node')

            # Augment the node properties to form a complete node.
            node_props['description'] = SYNC_GROUP_NODE_DESC
            node_props['properties'] = {'status': 'published'}

            sync_group = pillarsdk.Node.new(node_props)
            created_ok = await pillar_call(sync_group.create)
            if not created_ok:
                log.error('Blender Cloud addon: unable to create sync folder on the Cloud.')
                raise PillarError('Unable to create sync folder on the Cloud')

        return sync_group['_id']


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
