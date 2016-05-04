"""Blender-specific code.

Separated from __init__.py so that we can import & run from non-Blender environments.
"""

import logging

import bpy
from bpy.types import AddonPreferences, Operator, WindowManager, Scene
from bpy.props import StringProperty

from . import pillar, gui

PILLAR_SERVER_URL = 'https://cloudapi.blender.org/'
# PILLAR_SERVER_URL = 'http://localhost:5000/'

ADDON_NAME = 'blender_cloud'
log = logging.getLogger(__name__)


class BlenderCloudPreferences(AddonPreferences):
    bl_idname = ADDON_NAME

    # The following two properties are read-only to limit the scope of the
    # addon and allow for proper testing within this scope.
    pillar_server = bpy.props.StringProperty(
        name='Blender Cloud Server',
        description='URL of the Blender Cloud backend server',
        default=PILLAR_SERVER_URL,
        get=lambda self: PILLAR_SERVER_URL
    )

    # TODO: Move to the Scene properties?
    project_uuid = bpy.props.StringProperty(
        name='Project UUID',
        description='UUID of the current Blender Cloud project',
        default='5672beecc0261b2005ed1a33',
        get=lambda self: '5672beecc0261b2005ed1a33'
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

        sub = layout.column(align=True)
        sub.label(text=text, icon=icon)

        help_lines = textwrap.wrap(help_text, 80)
        for line in help_lines:
            sub.label(text=line)

        sub = layout.column()
        sub.label(text='Local directory for downloaded textures')
        sub.prop(self, "local_texture_dir", text='Default')
        sub.prop(context.scene, "local_texture_dir", text='Current scene')

        # options for Pillar
        sub = layout.column()
        sub.enabled = icon != 'ERROR'

        # TODO: let users easily pick a project. For now, we just use the
        # hard-coded server URL and UUID of the textures project.
        # sub.prop(self, "pillar_server")
        # sub.prop(self, "project_uuid")
        sub.operator("pillar.credentials_update")


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

    WindowManager.blender_cloud_project = StringProperty(
        name="Blender Cloud project UUID",
        default='5672beecc0261b2005ed1a33')  # TODO: don't hard-code this

    WindowManager.blender_cloud_node = StringProperty(
        name="Blender Cloud node UUID",
        default='')  # empty == top-level of project

    addon_prefs = preferences()

    def default_if_empty(scene, context):
        """The scene's local_texture_dir, if empty, reverts to the addon prefs."""

        if not scene.local_texture_dir:
            scene.local_texture_dir = addon_prefs.local_texture_dir

    Scene.local_texture_dir = StringProperty(
        name='Blender Cloud texture storage directory for current scene',
        subtype='DIR_PATH',
        default=addon_prefs.local_texture_dir,
        update=default_if_empty)


def unregister():
    gui.unregister()

    bpy.utils.unregister_class(PillarCredentialsUpdate)
    bpy.utils.unregister_class(BlenderCloudPreferences)

    del WindowManager.blender_cloud_project
    del WindowManager.blender_cloud_node
    del WindowManager.blender_cloud_thumbnails
