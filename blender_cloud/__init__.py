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

import os.path
import typing
import asyncio

# Support reloading
if 'pillar' in locals():
    import importlib

    pillar = importlib.reload(pillar)
    async_loop = importlib.reload(async_loop)
else:
    from . import pillar, async_loop

import bpy
import bpy.utils.previews
from bpy.types import AddonPreferences, Operator, PropertyGroup, WindowManager
from bpy.props import PointerProperty, StringProperty, EnumProperty


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


# We can store multiple preview collections here,
# however in this example we only store "main"
preview_collections = {}


def enum_previews_from_directory_items(self, context) -> typing.List[typing.AnyStr]:
    """EnumProperty callback"""

    if context is None:
        return []

    wm = context.window_manager
    project_uuid = wm.blender_cloud_project
    node_uuid = wm.blender_cloud_node

    # Get the preview collection (defined in register func).
    pcoll = preview_collections["blender_cloud"]
    if pcoll.project_uuid == project_uuid and pcoll.node_uuid == node_uuid:
        return pcoll.previews

    print('Loading previews for project {!r} node {!r}'.format(project_uuid, node_uuid))

    if pcoll.async_task is not None and not pcoll.async_task.done():
        # We're still asynchronously downloading, but the UUIDs changed.
        print('Cancelling running async download task {}'.format(pcoll.async_task))
        pcoll.async_task.cancel()

    # Download the previews asynchronously.
    pcoll.previews = []
    pcoll.project_uuid = project_uuid
    pcoll.node_uuid = node_uuid
    pcoll.async_task = asyncio.ensure_future(async_download_previews(wm.thumbnails_cache, pcoll))

    # Start the async manager so everything happens.
    async_loop.ensure_async_loop()

    return pcoll.previews


async def async_download_previews(thumbnails_directory, pcoll):
    # If we have a node UUID, we fetch the textures
    # FIXME: support mixture of sub-nodes and textures under one node.
    enum_items = pcoll.previews

    node_uuid = pcoll.node_uuid
    project_uuid = pcoll.project_uuid

    def thumbnail_loading(file_desc):
        # TODO: trigger re-draw
        pass

    def thumbnail_loaded(file_desc, thumb_path):
        thumb = pcoll.get(thumb_path)
        if thumb is None:
            thumb = pcoll.load(thumb_path, thumb_path, 'IMAGE')
        enum_items.append(('thumb-{}'.format(thumb_path), file_desc['filename'],
                           thumb_path,
                           thumb.icon_id,
                           len(enum_items)))
        # TODO: trigger re-draw

    if node_uuid:
        # Make sure we can go up again.
        parent = await pillar.parent_node_uuid(node_uuid)
        enum_items.append(('node-{}'.format(parent), 'up', 'up',
                           'FILE_FOLDER',
                           len(enum_items)))

        directory = os.path.join(thumbnails_directory, project_uuid, node_uuid)
        os.makedirs(directory, exist_ok=True)

        await pillar.fetch_texture_thumbs(node_uuid, 's', directory,
                                          thumbnail_loading=thumbnail_loading,
                                          thumbnail_loaded=thumbnail_loaded)
    elif project_uuid:
        children = await pillar.get_nodes(project_uuid, '')

        for child in children:
            print('  - %(_id)s = %(name)s' % child)
            enum_items.append(('node-{}'.format(child['_id']), child['name'],
                               'description',
                               'FILE_FOLDER',
                               len(enum_items)))
            # TODO: trigger re-draw
    else:
        # TODO: add "nothing here" icon and trigger re-draw
        pass


def enum_previews_from_directory_update(self, context):
    print('Updating from {!r}'.format(self.blender_cloud_thumbnails))

    sel_type, sel_id = self.blender_cloud_thumbnails.split('-', 1)

    if sel_type == 'node':
        # Go into this node
        self.blender_cloud_node = sel_id
    elif sel_type == 'thumb':
        # Select this image
        pass
    else:
        print("enum_previews_from_directory_update: Don't know what to do with {!r}"
              .format(self.blender_cloud_thumbnails))


class PreviewsExamplePanel(bpy.types.Panel):
    """Creates a Panel in the Object properties window"""

    bl_label = "Previews Example Panel"
    bl_idname = "OBJECT_PT_previews"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        row = layout.column()
        row.prop(wm, "thumbnails_cache")
        row.prop(wm, "blender_cloud_project")
        row.prop(wm, "blender_cloud_node")
        row.template_icon_view(wm, "blender_cloud_thumbnails", show_labels=True)
        # row.prop(wm, "blender_cloud_thumbnails")


class AsyncOperator(Operator):
    bl_idname = 'async.action'
    bl_label = 'Asynchronous action'
    bl_description = ''

    def execute(self, context):
        print('{}: executing'.format(self))

        asyncio.ensure_future(do_async_stuff(context))
        async_loop.ensure_async_loop()
        print('{}: done'.format(self))

        return {'FINISHED'}


async def do_async_stuff(context):
    print('do_async_stuff(): starting')

    wm = context.window_manager
    project_uuid = wm.blender_cloud_project
    print('Loading nodes for project {!r}'.format(project_uuid))

    children = await pillar.get_nodes(project_uuid, '')

    for child in children:
        print('  - %(_id)s = %(name)s' % child)
        await asyncio.sleep(0.5)

    print('do_async_stuff(): done')


def register():
    bpy.utils.register_module(__name__)

    WindowManager.thumbnails_cache = StringProperty(
        name="Thumbnails cache",
        subtype='DIR_PATH',
        default='/home/sybren/.cache/blender_cloud/thumbnails')

    WindowManager.blender_cloud_project = StringProperty(
        name="Blender Cloud project UUID",
        default='5672beecc0261b2005ed1a33')  # TODO: don't hard-code this

    WindowManager.blender_cloud_node = StringProperty(
        name="Blender Cloud node UUID",
        default='')  # empty == top-level of project

    WindowManager.blender_cloud_thumbnails = EnumProperty(
        items=enum_previews_from_directory_items,
        update=enum_previews_from_directory_update,
    )

    # Note that preview collections returned by bpy.utils.previews
    # are regular Python objects - you can use them to store custom data.
    #
    # This is especially useful here, since:
    # - It avoids us regenerating the whole enum over and over.
    # - It can store enum_items' strings
    #   (remember you have to keep those strings somewhere in py,
    #   else they get freed and Blender references invalid memory!).
    pcoll = bpy.utils.previews.new()
    pcoll.previews = ()
    pcoll.project_uuid = ''
    pcoll.node_uuid = ''
    pcoll.async_task = None

    preview_collections["blender_cloud"] = pcoll


def unregister():
    bpy.utils.unregister_module(__name__)

    del WindowManager.thumbnails_cache
    del WindowManager.blender_cloud_project
    del WindowManager.blender_cloud_node
    del WindowManager.blender_cloud_thumbnails

    for pcoll in preview_collections.values():
        bpy.utils.previews.remove(pcoll)
    preview_collections.clear()


if __name__ == "__main__":
    register()
