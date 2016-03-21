"""Blender-specific code.

Separated from __init__.py so that we can import & run from non-Blender environments.
"""

import os.path

import bpy
from bpy.types import AddonPreferences, Operator, WindowManager, Scene
from bpy.props import StringProperty

from . import pillar, gui, http_cache

ADDON_NAME = 'blender_cloud'


class BlenderCloudPreferences(AddonPreferences):
    bl_idname = ADDON_NAME

    pillar_server = bpy.props.StringProperty(
        name='Blender Cloud Server',
        description='URL of the Blender Cloud backend server',
        default='https://pillar.blender.org:5000/'
    )

    def draw(self, context):
        layout = self.layout

        # Carefully try and import the Blender ID addon
        try:
            import blender_id.profiles as blender_id_profiles
        except ImportError:
            blender_id_profiles = None
            blender_id_profile = None
        else:
            blender_id_profile = blender_id_profiles.get_active_profile()

        if blender_id_profiles is None:
            blender_id_icon = 'ERROR'
            blender_id_text = "This add-on requires Blender ID"
            blender_id_help = "Make sure that the Blender ID add-on is installed and activated"
        elif not blender_id_profile:
            blender_id_icon = 'ERROR'
            blender_id_text = "You are logged out."
            blender_id_help = "To login, go to the Blender ID add-on preferences."
        else:
            blender_id_icon = 'WORLD_DATA'
            blender_id_text = "You are logged in as %s." % blender_id_profile['username']
            blender_id_help = "To logout or change profile, " \
                              "go to the Blender ID add-on preferences."

        sub = layout.column()
        sub.label(text=blender_id_text, icon=blender_id_icon)
        sub.label(text="* " + blender_id_help)

        # options for Pillar
        sub = layout.column()
        sub.enabled = blender_id_icon != 'ERROR'
        sub.prop(self, "pillar_server")
        sub.operator("pillar.credentials_update")


class PillarCredentialsUpdate(Operator):
    """Updates the Pillar URL and tests the new URL."""
    bl_idname = "pillar.credentials_update"
    bl_label = "Update credentials"

    @classmethod
    def poll(cls, context):
        # Only allow activation when the user is actually logged in.
        return cls.is_logged_in(context)

    @classmethod
    def is_logged_in(cls, context):
        active_user_id = getattr(context.window_manager, 'blender_id_active_profile', None)
        return bool(active_user_id)

    def execute(self, context):
        # Only allow activation when the user is actually logged in.
        if not self.is_logged_in(context):
            self.report({'ERROR'}, "No active profile found")
            return {'CANCELLED'}

        # Test the new URL
        endpoint = bpy.context.user_preferences.addons[ADDON_NAME].preferences.pillar_server
        pillar._pillar_api = None
        try:
            pillar.get_project_uuid('textures')  # Just any query will do.
        except Exception as e:
            print(e)
            self.report({'ERROR'}, 'Failed connection to %s' % endpoint)
            return {'FINISHED'}

        self.report({'INFO'}, 'Updated cloud server address to %s' % endpoint)
        return {'FINISHED'}


def preferences() -> BlenderCloudPreferences:
    return bpy.context.user_preferences.addons[ADDON_NAME].preferences


def register():
    bpy.utils.register_class(BlenderCloudPreferences)
    bpy.utils.register_class(PillarCredentialsUpdate)

    WindowManager.thumbnails_cache = StringProperty(
        name="Thumbnails cache",
        subtype='DIR_PATH',
        default=os.path.join(http_cache.cache_directory(), 'thumbnails'))

    WindowManager.blender_cloud_project = StringProperty(
        name="Blender Cloud project UUID",
        default='5672beecc0261b2005ed1a33')  # TODO: don't hard-code this

    WindowManager.blender_cloud_node = StringProperty(
        name="Blender Cloud node UUID",
        default='')  # empty == top-level of project

    Scene.blender_cloud_dir = StringProperty(
        name='Blender Cloud texture storage directory',
        subtype='DIR_PATH',
        default='//textures')


def unregister():
    gui.unregister()

    bpy.utils.unregister_class(PillarCredentialsUpdate)
    bpy.utils.unregister_class(BlenderCloudPreferences)

    del WindowManager.blender_cloud_project
    del WindowManager.blender_cloud_node
    del WindowManager.blender_cloud_thumbnails
