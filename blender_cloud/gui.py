# ##### BEGIN GPL LICENSE BLOCK #####
#
#  Copyright (C) 2014 Blender Aid
#  http://www.blendearaid.com
#  blenderaid@gmail.com

#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# ##### END GPL LICENSE BLOCK #####
import asyncio
import logging
import threading
import traceback

import bpy
import bgl
import blf
import os

from bpy.types import AddonPreferences
from bpy.props import (BoolProperty, EnumProperty,
                       FloatProperty, FloatVectorProperty,
                       IntProperty, StringProperty)

from . import async_loop, pillar

icon_width = 128
icon_height = 128
target_item_width = 400
target_item_height = 128

library_path = '/tmp'
library_icons_path = os.path.join(os.path.dirname(__file__), "icons")


class MenuItem:
    """GUI menu item for the 3D View GUI."""

    icon_margin_x = 4
    icon_margin_y = 4
    text_margin_x = 6

    text_height = 16
    text_width = 72

    DEFAULT_ICONS = {
        'FOLDER': os.path.join(library_icons_path, 'folder.png'),
        'SPINNER': os.path.join(library_icons_path, 'spinner.png'),
    }

    def __init__(self, node_uuid: str, file_desc, thumb_path: str, label_text):
        # TODO: change node_uuid to the actual pillarsdk.Node object.
        # That way we can simply inspect node.node_type to distinguish between
        # folders ('group_texture' type) and files ('texture' type).

        self.node_uuid = node_uuid  # pillarsdk.Node UUID of a texture node
        self.file_desc = file_desc  # pillarsdk.File object, or None if a 'folder' node.
        self.label_text = label_text
        self._thumb_path = ''
        self.icon = None
        self._is_folder = file_desc is None and thumb_path == 'FOLDER'

        self.thumb_path = thumb_path

        # Updated when drawing the image
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0

    @property
    def thumb_path(self) -> str:
        return self._thumb_path

    @thumb_path.setter
    def thumb_path(self, new_thumb_path: str):
        self._thumb_path = self.DEFAULT_ICONS.get(new_thumb_path, new_thumb_path)
        if self._thumb_path:
            self.icon = bpy.data.images.load(filepath=self._thumb_path)
        else:
            self.icon = None

    def update(self, file_desc, thumb_path: str, label_text):
        self.file_desc = file_desc  # pillarsdk.File object, or None if a 'folder' node.
        self.thumb_path = thumb_path
        self.label_text = label_text

    @property
    def is_folder(self) -> bool:
        return self._is_folder

    def update_placement(self, x, y, width, height):
        """Use OpenGL to draw this one menu item."""

        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def draw(self, highlighted: bool):
        bgl.glEnable(bgl.GL_BLEND)
        if highlighted:
            bgl.glColor4f(0.555, 0.555, 0.555, 0.8)
        else:
            bgl.glColor4f(0.447, 0.447, 0.447, 0.8)

        bgl.glRectf(self.x, self.y, self.x + self.width, self.y + self.height)

        texture = self.icon
        err = texture.gl_load(filter=bgl.GL_NEAREST, mag=bgl.GL_NEAREST)
        assert not err, 'OpenGL error: %i' % err

        bgl.glColor4f(0.0, 0.0, 1.0, 0.5)
        # bgl.glLineWidth(1.5)

        # ------ TEXTURE ---------#
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, texture.bindcode[0])
        bgl.glEnable(bgl.GL_TEXTURE_2D)
        bgl.glBlendFunc(bgl.GL_SRC_ALPHA, bgl.GL_ONE_MINUS_SRC_ALPHA)

        bgl.glColor4f(1, 1, 1, 1)
        bgl.glBegin(bgl.GL_QUADS)
        bgl.glTexCoord2d(0, 0)
        bgl.glVertex2d(self.x + self.icon_margin_x, self.y)
        bgl.glTexCoord2d(0, 1)
        bgl.glVertex2d(self.x + self.icon_margin_x, self.y + icon_height)
        bgl.glTexCoord2d(1, 1)
        bgl.glVertex2d(self.x + self.icon_margin_x + icon_width, self.y + icon_height)
        bgl.glTexCoord2d(1, 0)
        bgl.glVertex2d(self.x + self.icon_margin_x + icon_width, self.y)
        bgl.glEnd()
        bgl.glDisable(bgl.GL_TEXTURE_2D)
        bgl.glDisable(bgl.GL_BLEND)

        texture.gl_free()

        # draw some text
        font_id = 0
        blf.position(font_id,
                     self.x + self.icon_margin_x + icon_width + self.text_margin_x,
                     self.y + icon_height * 0.5 - 0.25 * self.text_height, 0)
        blf.size(font_id, self.text_height, self.text_width)
        blf.draw(font_id, self.label_text)

    def hits(self, mouse_x: int, mouse_y: int) -> bool:
        return self.x < mouse_x < self.x + self.width and self.y < mouse_y < self.y + self.height


class BlenderCloudBrowser(bpy.types.Operator):
    bl_idname = 'pillar.browser'
    bl_label = 'Blender Cloud Texture Browser'

    _draw_handle = None

    _state = 'BROWSING'

    project_uuid = '5672beecc0261b2005ed1a33'  # Blender Cloud project UUID
    node_uuid = ''  # Blender Cloud node UUID
    async_task = None  # asyncio task for fetching thumbnails
    signalling_future = None  # asyncio future for signalling that we want to cancel everything.
    timer = None
    log = logging.getLogger('%s.BlenderCloudBrowser' % __name__)

    _menu_item_lock = threading.Lock()
    current_path = ''
    current_display_content = []
    loaded_images = set()
    thumbnails_cache = ''

    mouse_x = 0
    mouse_y = 0

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "View3D not found, cannot show Blender Cloud browser")
            return {'CANCELLED'}

        wm = context.window_manager
        self.thumbnails_cache = wm.thumbnails_cache
        self.project_uuid = wm.blender_cloud_project
        self.node_uuid = wm.blender_cloud_node

        self.mouse_x = event.mouse_region_x
        self.mouse_y = event.mouse_region_y

        # Add the region OpenGL drawing callback
        # draw in view space with 'POST_VIEW' and 'PRE_VIEW'
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self.draw_menu, (context,), 'WINDOW', 'POST_PIXEL')

        self.current_display_content = []
        self.loaded_images = set()
        self.browse_assets()

        context.window_manager.modal_handler_add(self)
        self.timer = context.window_manager.event_timer_add(1 / 30, context.window)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self._state == 'QUIT':
            self._finish(context)
            return {'FINISHED'}

        if event.type == 'TAB' and event.value == 'RELEASE':
            self.log.info('Ensuring async loop is running')
            async_loop.ensure_async_loop()

        if event.type == 'TIMER':
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if 'MOUSE' in event.type:
            context.area.tag_redraw()
            self.mouse_x = event.mouse_region_x
            self.mouse_y = event.mouse_region_y

        if self._state == 'BROWSING' and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            selected = self.get_clicked()

            if selected is None:
                self._finish(context)
                return {'FINISHED'}

            if selected.is_folder:
                self.node_uuid = selected.node_uuid
                self.browse_assets()
            else:
                if selected.file_desc is None:
                    # This can happen when the thumbnail information isn't loaded yet.
                    # Just ignore the click for now.
                    # TODO: think of a way to handle this properly.
                    return {'RUNNING_MODAL'}
                self.handle_item_selection(context, selected)

        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _stop_async_task(self):
        self.log.debug('Stopping async task')
        if self.async_task is None:
            self.log.debug('No async task, trivially stopped')
            return

        # Signal that we want to stop.
        if not self.signalling_future.done():
            self.log.info("Signalling that we want to cancel anything that's running.")
            self.signalling_future.cancel()

        # Wait until the asynchronous task is done.
        if not self.async_task.done():
            self.log.info("blocking until async task is done.")
            loop = asyncio.get_event_loop()
            try:
                loop.run_until_complete(self.async_task)
            except asyncio.CancelledError:
                self.log.info('Asynchronous task was cancelled')
                return

        # noinspection PyBroadException
        try:
            self.async_task.result()  # This re-raises any exception of the task.
        except asyncio.CancelledError:
            self.log.info('Asynchronous task was cancelled')
        except Exception:
            self.log.exception("Exception from asynchronous task")

    def _finish(self, context):
        self.log.debug('Finishing the modal operator')
        self._stop_async_task()
        bpy.types.SpaceView3D.draw_handler_remove(self._draw_handle, 'WINDOW')
        context.window_manager.event_timer_remove(self.timer)
        context.area.tag_redraw()
        self.log.debug('Modal operator finished')

    def clear_images(self):
        """Removes all images we loaded from Blender's memory."""

        for image in bpy.data.images:
            if image.filepath_raw not in self.loaded_images:
                continue

            image.user_clear()
            bpy.data.images.remove(image)

        self.loaded_images.clear()
        self.current_display_content.clear()

    def add_menu_item(self, *args) -> MenuItem:
        menu_item = MenuItem(*args)

        # Just make this thread-safe to be on the safe side.
        with self._menu_item_lock:
            self.current_display_content.append(menu_item)
            self.loaded_images.add(menu_item.icon.filepath_raw)

        return menu_item

    def update_menu_item(self, node_uuid, *args) -> MenuItem:
        # Just make this thread-safe to be on the safe side.
        with self._menu_item_lock:
            for menu_item in self.current_display_content:
                if menu_item.node_uuid == node_uuid:
                    menu_item.update(*args)
                    self.loaded_images.add(menu_item.icon.filepath_raw)
                    break
            else:
                raise ValueError('Unable to find MenuItem(node_uuid=%r)' % node_uuid)

    async def async_download_previews(self, thumbnails_directory):
        self.log.info('Asynchronously downloading previews to %r', thumbnails_directory)
        self.clear_images()

        def thumbnail_loading(node_uuid, texture_node):
            self.add_menu_item(node_uuid, None, 'SPINNER', texture_node['name'])

        def thumbnail_loaded(node_uuid, file_desc, thumb_path):
            self.update_menu_item(node_uuid, file_desc, thumb_path, file_desc['filename'])

        if self.node_uuid:
            self.log.debug('Getting subnodes for parent node %r', self.node_uuid)
            children = await pillar.get_nodes(parent_node_uuid=self.node_uuid,
                                              node_type='group_textures')

            self.log.debug('Finding parent of node %r', self.node_uuid)
            # Make sure we can go up again.
            parent_uuid = await pillar.parent_node_uuid(self.node_uuid)
            self.add_menu_item(parent_uuid, None, 'FOLDER', '.. up ..')

            self.log.debug('Iterating over child nodes of %r', self.node_uuid)
            for child in children:
                # print('  - %(_id)s = %(name)s' % child)
                self.add_menu_item(child['_id'], None, 'FOLDER', child['name'])

            directory = os.path.join(thumbnails_directory, self.project_uuid, self.node_uuid)
            os.makedirs(directory, exist_ok=True)

            self.log.debug('Fetching texture thumbnails for node %r', self.node_uuid)
            await pillar.fetch_texture_thumbs(self.node_uuid, 's', directory,
                                              thumbnail_loading=thumbnail_loading,
                                              thumbnail_loaded=thumbnail_loaded,
                                              future=self.signalling_future)
        elif self.project_uuid:
            self.log.debug('Getting subnodes for project node %r', self.project_uuid)
            children = await pillar.get_nodes(self.project_uuid, '')

            self.log.debug('Iterating over child nodes of project %r', self.project_uuid)
            for child in children:
                # print('  - %(_id)s = %(name)s' % child)
                self.add_menu_item(child['_id'], None, 'FOLDER', child['name'])
        else:
            # TODO: add "nothing here" icon and trigger re-draw
            self.log.warning("Not node UUID and no project UUID, I can't do anything!")

    def browse_assets(self):
        self._state = 'BROWSING'
        self.log.debug('Browsing assets at project %r node %r', self.project_uuid, self.node_uuid)
        self._new_async_task(self.async_download_previews(self.thumbnails_cache))

    def _new_async_task(self, async_task: asyncio.coroutine):
        """Stops the currently running async task, and starts another one."""

        self.log.debug('Setting up a new task %r, so any existing task must be stopped', async_task)
        self._stop_async_task()

        # Download the previews asynchronously.
        self.signalling_future = asyncio.Future()
        self.async_task = asyncio.ensure_future(async_task)
        self.log.debug('Created new task %r', self.async_task)

        # Start the async manager so everything happens.
        async_loop.ensure_async_loop()

    def draw_menu(self, context):
        """Draws the GUI with OpenGL."""

        drawers = {
            'BROWSING': self._draw_browser,
            'DOWNLOADING_TEXTURE': self._draw_downloading,
        }

        if self._state in drawers:
            drawer = drawers[self._state]
            drawer(context)

        # For debugging: draw the state
        font_id = 0
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
        blf.size(font_id, 20, 72)
        blf.position(font_id, 5, 5, 0)
        blf.draw(font_id, self._state)
        bgl.glDisable(bgl.GL_BLEND)

    def _draw_browser(self, context):
        """OpenGL drawing code for the BROWSING state."""

        margin_x = 20
        margin_y = 5
        padding_x = 5

        content_width = context.area.regions[4].width - margin_x * 2
        content_height = context.area.regions[4].height - margin_y * 2

        content_x = margin_x
        content_y = context.area.height - margin_y - target_item_height - 50

        col_count = content_width // target_item_width

        item_width = (content_width - (col_count * padding_x)) / col_count
        item_height = target_item_height

        block_width = item_width + padding_x
        block_height = item_height + margin_y

        bgl.glEnable(bgl.GL_BLEND)
        bgl.glColor4f(0.0, 0.0, 0.0, 0.6)
        bgl.glRectf(0, 0, context.area.regions[4].width, context.area.regions[4].height)

        if self.current_display_content:
            for item_idx, item in enumerate(self.current_display_content):
                x = (item_idx % col_count) * block_width
                y = content_y - (item_idx // col_count) * block_height

                item.update_placement(x, y, item_width, item_height)
                item.draw(highlighted=item.hits(self.mouse_x, self.mouse_y))
        else:
            font_id = 0
            text = "Communicating with Blender Cloud"
            bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
            blf.size(font_id, 20, 72)
            text_width, text_height = blf.dimensions(font_id, text)
            blf.position(font_id, content_x + content_width * 0.5 - text_width * 0.5,
                         content_y - content_height * 0.3 + text_height * 0.5, 0)
            blf.draw(font_id, text)

        bgl.glDisable(bgl.GL_BLEND)
        # bgl.glColor4f(0.0, 0.0, 0.0, 1.0)

    def _draw_downloading(self, context):
        """OpenGL drawing code for the DOWNLOADING_TEXTURE state."""

        content_width = context.area.regions[4].width
        content_height = context.area.regions[4].height

        bgl.glEnable(bgl.GL_BLEND)
        bgl.glColor4f(0.0, 0.0, 0.2, 0.6)
        bgl.glRectf(0, 0, content_width, content_height)

        font_id = 0
        text = "Downloading texture from Blender Cloud"
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
        blf.size(font_id, 20, 72)
        text_width, text_height = blf.dimensions(font_id, text)

        blf.position(font_id,
                     content_width * 0.5 - text_width * 0.5,
                     content_height * 0.7 + text_height * 0.5, 0)
        blf.draw(font_id, text)
        bgl.glDisable(bgl.GL_BLEND)

    def get_clicked(self) -> MenuItem:

        for item in self.current_display_content:
            if item.hits(self.mouse_x, self.mouse_y):
                return item

        return None

    def handle_item_selection(self, context, item: MenuItem):
        """Called when the user clicks on a menu item that doesn't represent a folder."""

        self._state = 'DOWNLOADING_TEXTURE'
        url = item.file_desc.link
        local_path = os.path.join(context.scene.blender_cloud_dir, item.file_desc.filename)
        local_path = bpy.path.abspath(local_path)
        self.log.info('Downloading %s to %s', url, local_path)

        def texture_download_completed(_):
            self.log.info('Texture download complete, inspect %r.', local_path)
            self._state = 'QUIT'

        self._new_async_task(pillar.download_to_file(url, local_path))
        self.async_task.add_done_callback(texture_download_completed)


# store keymaps here to access after registration
addon_keymaps = []


def menu_draw(self, context):
    layout = self.layout
    layout.separator()
    layout.operator(BlenderCloudBrowser.bl_idname, icon='MOD_SCREW')


def register():
    bpy.utils.register_class(BlenderCloudBrowser)
    bpy.types.INFO_MT_mesh_add.append(menu_draw)

    # handle the keymap
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        print('No addon key configuration space found, so no custom hotkeys added.')
        return

    km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
    kmi = km.keymap_items.new('pillar.browser', 'A', 'PRESS', ctrl=True, shift=True, alt=True)
    addon_keymaps.append((km, kmi))


def unregister():
    bpy.utils.unregister_class(BlenderCloudBrowser)

    # handle the keymap
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()


if __name__ == "__main__":
    register()
