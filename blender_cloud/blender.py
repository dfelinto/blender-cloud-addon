# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""Blender-specific code.

Separated from __init__.py so that we can import & run from non-Blender environments.
"""

import logging
import os.path

import bpy
from bpy.types import AddonPreferences, Operator, WindowManager, Scene, PropertyGroup
from bpy.props import StringProperty, EnumProperty, PointerProperty, BoolProperty
import rna_prop_ui

from . import pillar

PILLAR_SERVER_URL = 'https://cloud.blender.org/api/'
# PILLAR_SERVER_URL = 'http://pillar:5001/api/'

ADDON_NAME = 'blender_cloud'
log = logging.getLogger(__name__)

icons = None


def redraw(self, context):
    context.area.tag_redraw()


def blender_syncable_versions(self, context):
    bss = context.window_manager.blender_sync_status
    versions = bss.available_blender_versions
    if not versions:
        return [('', 'No settings stored in your Blender Cloud', '')]
    return [(v, v, '') for v in versions]


class SyncStatusProperties(PropertyGroup):
    status = EnumProperty(
        items=[
            ('NONE', 'NONE', 'We have done nothing at all yet.'),
            ('IDLE', 'IDLE', 'User requested something, which is done, and we are now idle.'),
            ('SYNCING', 'SYNCING', 'Synchronising with Blender Cloud.'),
        ],
        name='status',
        description='Current status of Blender Sync',
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
            ('SUBSCRIBE', 'SUBSCRIBE', ''),
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
            try:
                loglevel = logging._nameToLevel[self.level]
            except KeyError:
                loglevel = logging.WARNING
            log.log(loglevel, message)

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

    open_browser_after_share = BoolProperty(
        name='Open browser after sharing file',
        description='When enabled, Blender will open a webbrowser',
        default=True
    )

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
            msg_icon = 'ERROR'
            text = 'This add-on requires Blender ID'
            help_text = 'Make sure that the Blender ID add-on is installed and activated'
        elif not blender_id_profile:
            msg_icon = 'ERROR'
            text = 'You are logged out.'
            help_text = 'To login, go to the Blender ID add-on preferences.'
        elif bpy.app.debug and pillar.SUBCLIENT_ID not in blender_id_profile.subclients:
            msg_icon = 'QUESTION'
            text = 'No Blender Cloud credentials.'
            help_text = ('You are logged in on Blender ID, but your credentials have not '
                         'been synchronized with Blender Cloud yet. Press the Update '
                         'Credentials button.')
        else:
            msg_icon = 'WORLD_DATA'
            text = 'You are logged in as %s.' % blender_id_profile.username
            help_text = ('To logout or change profile, '
                         'go to the Blender ID add-on preferences.')

        # Authentication stuff
        auth_box = layout.box()
        auth_box.label(text=text, icon=msg_icon)

        help_lines = textwrap.wrap(help_text, 80)
        for line in help_lines:
            auth_box.label(text=line)
        if bpy.app.debug:
            auth_box.operator("pillar.credentials_update")

        # Texture browser stuff
        texture_box = layout.box()
        texture_box.enabled = msg_icon != 'ERROR'
        sub = texture_box.column()
        sub.label(text='Local directory for downloaded textures', icon_value=icon('CLOUD'))
        sub.prop(self, "local_texture_dir", text='Default')
        sub.prop(context.scene, "local_texture_dir", text='Current scene')

        # Blender Sync stuff
        bss = context.window_manager.blender_sync_status
        bsync_box = layout.box()
        bsync_box.enabled = msg_icon != 'ERROR'
        row = bsync_box.row().split(percentage=0.33)
        row.label('Blender Sync with Blender Cloud', icon_value=icon('CLOUD'))

        icon_for_level = {
            'INFO': 'NONE',
            'WARNING': 'INFO',
            'ERROR': 'ERROR',
            'SUBSCRIBE': 'ERROR',
        }
        msg_icon = icon_for_level[bss.level] if bss.message else 'NONE'
        message_container = row.row()
        message_container.label(bss.message, icon=msg_icon)

        sub = bsync_box.column()

        if bss.level == 'SUBSCRIBE':
            self.draw_subscribe_button(sub)
        self.draw_sync_buttons(sub, bss)

        # Image Share stuff
        share_box = layout.box()
        share_box.label('Image Sharing on Blender Cloud', icon_value=icon('CLOUD'))
        texture_box.enabled = msg_icon != 'ERROR'
        share_box.prop(self, 'open_browser_after_share')

    def draw_subscribe_button(self, layout):
        layout.operator('pillar.subscribe', icon='WORLD')

    def draw_sync_buttons(self, layout, bss):
        layout.enabled = bss.status in {'NONE', 'IDLE'}

        buttons = layout.column()
        row_buttons = buttons.row().split(percentage=0.5)
        row_push = row_buttons.row()
        row_pull = row_buttons.row(align=True)

        row_push.operator('pillar.sync',
                          text='Save %i.%i settings' % bpy.app.version[:2],
                          icon='TRIA_UP').action = 'PUSH'

        versions = bss.available_blender_versions
        version = bss.version
        if bss.status in {'NONE', 'IDLE'}:
            if not versions or not version:
                row_pull.operator('pillar.sync',
                                  text='Find version to load',
                                  icon='TRIA_DOWN').action = 'REFRESH'
            else:
                props = row_pull.operator('pillar.sync',
                                          text='Load %s settings' % version,
                                          icon='TRIA_DOWN')
                props.action = 'PULL'
                props.blender_version = version
                row_pull.operator('pillar.sync',
                                  text='',
                                  icon='DOTSDOWN').action = 'SELECT'
        else:
            row_pull.label('Cloud Sync is running.')


class PillarCredentialsUpdate(pillar.PillarOperatorMixin,
                              Operator):
    """Updates the Pillar URL and tests the new URL."""
    bl_idname = 'pillar.credentials_update'
    bl_label = 'Update credentials'

    log = logging.getLogger('bpy.ops.%s' % bl_idname)

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
            loop.run_until_complete(self.check_credentials(context, set()))
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


class PILLAR_OT_subscribe(Operator):
    """Opens a browser to subscribe the user to the Cloud."""
    bl_idname = 'pillar.subscribe'
    bl_label = 'Subscribe to the Cloud'

    def execute(self, context):
        import webbrowser

        webbrowser.open_new_tab('https://cloud.blender.org/join')
        self.report({'INFO'}, 'We just started a browser for you.')

        return {'FINISHED'}


class PILLAR_PT_image_custom_properties(rna_prop_ui.PropertyPanel, bpy.types.Panel):
    """Shows custom properties in the image editor."""

    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_label = 'Custom Properties'

    _context_path = 'edit_image'
    _property_type = bpy.types.Image


def preferences() -> BlenderCloudPreferences:
    return bpy.context.user_preferences.addons[ADDON_NAME].preferences


def load_custom_icons():
    global icons

    if icons is not None:
        # Already loaded
        return

    import bpy.utils.previews
    icons = bpy.utils.previews.new()
    my_icons_dir = os.path.join(os.path.dirname(__file__), 'icons')
    icons.load('CLOUD', os.path.join(my_icons_dir, 'icon-cloud.png'), 'IMAGE')


def unload_custom_icons():
    global icons

    if icons is None:
        # Already unloaded
        return

    bpy.utils.previews.remove(icons)
    icons = None


def icon(icon_name: str) -> int:
    """Returns the icon ID for the named icon.

    Use with layout.operator('pillar.image_share', icon_value=icon('CLOUD'))
    """

    return icons[icon_name].icon_id


def register():
    bpy.utils.register_class(BlenderCloudPreferences)
    bpy.utils.register_class(PillarCredentialsUpdate)
    bpy.utils.register_class(SyncStatusProperties)
    bpy.utils.register_class(PILLAR_OT_subscribe)
    bpy.utils.register_class(PILLAR_PT_image_custom_properties)

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

    load_custom_icons()


def unregister():
    unload_custom_icons()

    bpy.utils.unregister_class(PillarCredentialsUpdate)
    bpy.utils.unregister_class(BlenderCloudPreferences)
    bpy.utils.unregister_class(SyncStatusProperties)
    bpy.utils.unregister_class(PILLAR_OT_subscribe)
    bpy.utils.unregister_class(PILLAR_PT_image_custom_properties)

    del WindowManager.last_blender_cloud_location
    del WindowManager.blender_sync_status
