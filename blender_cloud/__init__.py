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

# <pep8 compliant>

bl_info = {
    "name": "Blender Cloud Texture Browser",
    "author": "Sybren A. Stüvel and Francesco Siddi",
    "version": (0, 1, 0),
    "blender": (2, 77, 0),
    "location": "TO BE DETERMINED",
    "description": "Allows downloading of textures from the Blender Cloud. Requires "
                   "the Blender ID addon.",
    "wiki_url": "http://wiki.blender.org/index.php/Extensions:2.6/Py/"
                "Scripts/System/BlenderCloud",
    "category": "System",
    "support": "TESTING"
}

from . import pillar

import bpy
from bpy.types import AddonPreferences, Operator, PropertyGroup
from bpy.props import PointerProperty, StringProperty


class BlenderCloudPreferences(AddonPreferences):
    bl_idname = __name__

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
        endpoint = bpy.context.user_preferences.addons[__name__].preferences.pillar_server
        pillar._pillar_api = None
        try:
            pillar.get_project_uuid('textures')  # Just any query will do.
        except Exception as e:
            print(e)
            self.report({'ERROR'}, 'Failed connection to %s' % endpoint)
            return {'FINISHED'}

        self.report({'INFO'}, 'Updated cloud server address to %s' % endpoint)
        return {'FINISHED'}


def register():
    bpy.utils.register_module(__name__)


def unregister():
    bpy.utils.unregister_module(__name__)


if __name__ == "__main__":
    register()
