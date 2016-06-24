"""Blender-specific code.

Separated from __init__.py so that we can import & run from non-Blender environments.
"""

import logging

import bpy
from bpy.types import AddonPreferences, Operator, WindowManager, Scene, PropertyGroup
from bpy.props import StringProperty, EnumProperty, PointerProperty

from . import pillar, gui

PILLAR_SERVER_URL = 'https://cloudapi.blender.org/'
# PILLAR_SERVER_URL = 'http://localhost:5000/'

ADDON_NAME = 'blender_cloud'
log = logging.getLogger(__name__)


def redraw(self, context):
    context.area.tag_redraw()


def blender_syncable_versions(self, context):
    bss = context.window_manager.blender_sync_status
    versions = bss.available_blender_versions
    if not versions:
        return [('', 'No settings stored in your home project.', '')]
    return [(v, v, '') for v in versions]


class SyncStatusProperties(PropertyGroup):
    status = EnumProperty(
        items=[
            ('NONE', 'NONE', 'We have done nothing at all yet.'),
            ('IDLE', 'IDLE', 'User requested something, which is done, and we are now idle.'),
            ('SYNCING', 'SYNCING', 'Synchronising with Blender Cloud.'),
        ],
        name='status',
        description='Current status of Blender Sync.',
        update=redraw)

    version = EnumProperty(
        items=blender_syncable_versions,
        name='Version of Blender from which to pull',
        description='Version of Blender from which to pull')

    message = StringProperty(name='message', update=redraw)
    level = EnumProperty(
        items=[
            ('INFO', 'INFO', ''),
            ('WARNING', 'WARNING', ''),
            ('ERROR', 'ERROR', ''),
        ],
        name='level',
        update=redraw)

    def report(self, level: set, message: str):
        assert len(level) == 1, 'level should be a set of one string, not %r' % level
        self.level = level.pop()
        self.message = message

        # Message can also be empty, just to erase it from the GUI.
        # No need to actually log those.
        if message:
            log.log(logging._nameToLevel[self.level], message)

    # List of syncable versions is stored in 'available_blender_versions' ID property,
    # because I don't know how to store a variable list of strings in a proper RNA property.
    @property
    def available_blender_versions(self) -> list:
        return self.get('available_blender_versions', [])

    @available_blender_versions.setter
    def available_blender_versions(self, new_versions):
        self['available_blender_versions'] = new_versions


class BlenderCloudPreferences(AddonPreferences):
    bl_idname = ADDON_NAME

    # The following two properties are read-only to limit the scope of the
    # addon and allow for proper testing within this scope.
    pillar_server = StringProperty(
        name='Blender Cloud Server',
        description='URL of the Blender Cloud backend server',
        default=PILLAR_SERVER_URL,
        get=lambda self: PILLAR_SERVER_URL
    )

    local_texture_dir = StringProperty(
        name='Default Blender Cloud texture storage directory',
        subtype='DIR_PATH',
        default='//textures')

    def draw(self, context):
        import textwrap

        layout = self.layout

        # Carefully try and import the Blender ID addon
        try:
            import blender_id
        except ImportError:
            blender_id = None
            blender_id_profile = None
        else:
            blender_id_profile = blender_id.get_active_profile()

        if blender_id is None:
            icon = 'ERROR'
            text = 'This add-on requires Blender ID'
            help_text = 'Make sure that the Blender ID add-on is installed and activated'
        elif not blender_id_profile:
            icon = 'ERROR'
            text = 'You are logged out.'
            help_text = 'To login, go to the Blender ID add-on preferences.'
        elif pillar.SUBCLIENT_ID not in blender_id_profile.subclients:
            icon = 'QUESTION'
            text = 'No Blender Cloud credentials.'
            help_text = ('You are logged in on Blender ID, but your credentials have not '
                         'been synchronized with Blender Cloud yet. Press the Update '
                         'Credentials button.')
        else:
            icon = 'WORLD_DATA'
            text = 'You are logged in as %s.' % blender_id_profile.username
            help_text = ('To logout or change profile, '
                         'go to the Blender ID add-on preferences.')

        # Authentication stuff
        auth_box = layout.box()
        auth_box.label(text=text, icon=icon)

        help_lines = textwrap.wrap(help_text, 80)
        for line in help_lines:
            auth_box.label(text=line)
        auth_box.operator("pillar.credentials_update")

        # Texture browser stuff
        texture_box = layout.box()
        texture_box.enabled = icon != 'ERROR'
        sub = texture_box.column()
        sub.label(text='Local directory for downloaded textures')
        sub.prop(self, "local_texture_dir", text='Default')
        sub.prop(context.scene, "local_texture_dir", text='Current scene')

        # Blender Sync stuff
        bss = context.window_manager.blender_sync_status
        bsync_box = layout.box()
        bsync_box.enabled = icon != 'ERROR'
        row = bsync_box.row().split(percentage=0.33)
        row.label('Blender Sync')

        icon_for_level = {
            'INFO': 'NONE',
            'WARNING': 'INFO',
            'ERROR': 'ERROR',
        }
        message_container = row.row()
        message_container.label(bss.message, icon=icon_for_level[bss.level])
        message_container.alert = True  # bss.level in {'WARNING', 'ERROR'}

        sub = bsync_box.column()
        sub.enabled = bss.status in {'NONE', 'IDLE'}

        buttons = sub.column()
        row_buttons = buttons.row().split(percentage=0.5)
        row_pull = row_buttons.row(align=True)
        row_push = row_buttons.row()

        row_push.operator('pillar.sync',
                          text='Save %i.%i settings to Cloud' % bpy.app.version[:2],
                          icon='TRIA_UP').action = 'PUSH'

        versions = bss.available_blender_versions
        version = bss.version
        if bss.status in {'NONE', 'IDLE'}:
            if not versions or not version:
                row_pull.operator('pillar.sync',
                                  text='Find version to load from Cloud',
                                  icon='TRIA_DOWN').action = 'REFRESH'
            else:
                props = row_pull.operator('pillar.sync',
                                          text='Load %s settings from Cloud' % version,
                                          icon='TRIA_DOWN')
                props.action = 'PULL'
                props.blender_version = version
                row_pull.operator('pillar.sync',
                                  text='',
                                  icon='DOTSDOWN').action = 'SELECT'
        else:
            row_pull.label('Cloud Sync is running.')


class PillarCredentialsUpdate(Operator):
    """Updates the Pillar URL and tests the new URL."""
    bl_idname = 'pillar.credentials_update'
    bl_label = 'Update credentials'

    @classmethod
    def poll(cls, context):
        # Only allow activation when the user is actually logged in.
        return cls.is_logged_in(context)

    @classmethod
    def is_logged_in(cls, context):
        try:
            import blender_id
        except ImportError:
            return False

        return blender_id.is_logged_in()

    def execute(self, context):
        import blender_id
        import asyncio

        # Only allow activation when the user is actually logged in.
        if not self.is_logged_in(context):
            self.report({'ERROR'}, 'No active profile found')
            return {'CANCELLED'}

        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(pillar.refresh_pillar_credentials())
        except blender_id.BlenderIdCommError as ex:
            log.exception('Error sending subclient-specific token to Blender ID')
            self.report({'ERROR'}, 'Failed to sync Blender ID to Blender Cloud')
            return {'CANCELLED'}
        except Exception as ex:
            log.exception('Error in test call to Pillar')
            self.report({'ERROR'}, 'Failed test connection to Blender Cloud')
            return {'CANCELLED'}

        self.report({'INFO'}, 'Blender Cloud credentials & endpoint URL updated.')
        return {'FINISHED'}


def preferences() -> BlenderCloudPreferences:
    return bpy.context.user_preferences.addons[ADDON_NAME].preferences


def register():
    bpy.utils.register_class(BlenderCloudPreferences)
    bpy.utils.register_class(PillarCredentialsUpdate)
    bpy.utils.register_class(SyncStatusProperties)

    addon_prefs = preferences()

    WindowManager.last_blender_cloud_location = StringProperty(
        name="Last Blender Cloud browser location",
        default="/")

    def default_if_empty(scene, context):
        """The scene's local_texture_dir, if empty, reverts to the addon prefs."""

        if not scene.local_texture_dir:
            scene.local_texture_dir = addon_prefs.local_texture_dir

    Scene.local_texture_dir = StringProperty(
        name='Blender Cloud texture storage directory for current scene',
        subtype='DIR_PATH',
        default=addon_prefs.local_texture_dir,
        update=default_if_empty)

    WindowManager.blender_sync_status = PointerProperty(type=SyncStatusProperties)


def unregister():
    gui.unregister()

    bpy.utils.unregister_class(PillarCredentialsUpdate)
    bpy.utils.unregister_class(BlenderCloudPreferences)
    bpy.utils.unregister_class(SyncStatusProperties)

    del WindowManager.last_blender_cloud_location
    del WindowManager.blender_sync_status
