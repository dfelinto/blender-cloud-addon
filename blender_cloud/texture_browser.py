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
import os

import bpy
import bgl
import blf

import pillarsdk
from . import async_loop, pillar, cache

REQUIRED_ROLES_FOR_TEXTURE_BROWSER = {'subscriber', 'demo'}
MOUSE_SCROLL_PIXELS_PER_TICK = 50

ICON_WIDTH = 128
ICON_HEIGHT = 128
TARGET_ITEM_WIDTH = 400
TARGET_ITEM_HEIGHT = 128
ITEM_MARGIN_X = 5
ITEM_MARGIN_Y = 5
ITEM_PADDING_X = 5

library_path = '/tmp'
library_icons_path = os.path.join(os.path.dirname(__file__), "icons")


class SpecialFolderNode(pillarsdk.Node):
    pass


class UpNode(SpecialFolderNode):
    def __init__(self):
        super().__init__()
        self['_id'] = 'UP'
        self['node_type'] = 'UP'


class ProjectNode(SpecialFolderNode):
    def __init__(self, project):
        super().__init__()

        assert isinstance(project, pillarsdk.Project), 'wrong type for project: %r' % type(project)

        self.merge(project.to_dict())
        self['node_type'] = 'PROJECT'


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

    SUPPORTED_NODE_TYPES = {'UP', 'PROJECT', 'group_texture', 'texture'}

    def __init__(self, node, file_desc, thumb_path: str, label_text):
        self.log = logging.getLogger('%s.MenuItem' % __name__)
        if node['node_type'] not in self.SUPPORTED_NODE_TYPES:
            self.log.info('Invalid node type in node: %s', node)
            raise TypeError('Node of type %r not supported; supported are %r.' % (
                node['node_type'], self.SUPPORTED_NODE_TYPES))

        assert isinstance(node, pillarsdk.Node), 'wrong type for node: %r' % type(node)
        assert isinstance(node['_id'], str), 'wrong type for node["_id"]: %r' % type(node['_id'])
        self.node = node  # pillarsdk.Node, contains 'node_type' key to indicate type
        self.file_desc = file_desc  # pillarsdk.File object, or None if a 'folder' node.
        self.label_text = label_text
        self._thumb_path = ''
        self.icon = None
        self._is_folder = (node['node_type'] == 'group_texture' or
                           isinstance(node, SpecialFolderNode))

        # Determine sorting order.
        # by default, sort all the way at the end and folders first.
        self._order = 0 if self._is_folder else 10000
        if node and node.properties and node.properties.order is not None:
            self._order = node.properties.order

        self.thumb_path = thumb_path

        # Updated when drawing the image
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0

    def sort_key(self):
        """Key for sorting lists of MenuItems."""
        return self._order, self.label_text

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

    @property
    def node_uuid(self) -> str:
        return self.node['_id']

    def update(self, node, file_desc, thumb_path: str, label_text):
        # We can get updated information about our Node, but a MenuItem should
        # always represent one node, and it shouldn't be shared between nodes.
        if self.node_uuid != node['_id']:
            raise ValueError("Don't change the node ID this MenuItem reflects, "
                             "just create a new one.")
        self.node = node
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
        bgl.glVertex2d(self.x + self.icon_margin_x, self.y + ICON_HEIGHT)
        bgl.glTexCoord2d(1, 1)
        bgl.glVertex2d(self.x + self.icon_margin_x + ICON_WIDTH, self.y + ICON_HEIGHT)
        bgl.glTexCoord2d(1, 0)
        bgl.glVertex2d(self.x + self.icon_margin_x + ICON_WIDTH, self.y)
        bgl.glEnd()
        bgl.glDisable(bgl.GL_TEXTURE_2D)
        bgl.glDisable(bgl.GL_BLEND)

        texture.gl_free()

        # draw some text
        font_id = 0
        blf.position(font_id,
                     self.x + self.icon_margin_x + ICON_WIDTH + self.text_margin_x,
                     self.y + ICON_HEIGHT * 0.5 - 0.25 * self.text_height, 0)
        blf.size(font_id, self.text_height, self.text_width)
        blf.draw(font_id, self.label_text)

    def hits(self, mouse_x: int, mouse_y: int) -> bool:
        return self.x < mouse_x < self.x + self.width and self.y < mouse_y < self.y + self.height


class BlenderCloudBrowser(pillar.PillarOperatorMixin,
                          async_loop.AsyncModalOperatorMixin,
                          bpy.types.Operator):
    bl_idname = 'pillar.browser'
    bl_label = 'Blender Cloud Texture Browser'

    _draw_handle = None

    current_path = pillar.CloudPath('/')
    project_name = ''

    # This contains a stack of Node objects that lead up to the currently browsed node.
    path_stack = []

    timer = None
    log = logging.getLogger('%s.BlenderCloudBrowser' % __name__)

    _menu_item_lock = threading.Lock()
    current_display_content = []
    loaded_images = set()
    thumbnails_cache = ''
    maximized_area = False

    mouse_x = 0
    mouse_y = 0
    scroll_offset = 0
    scroll_offset_target = 0
    scroll_offset_max = 0
    scroll_offset_space_left = 0

    def invoke(self, context, event):
        # Refuse to start if the file hasn't been saved.
        if context.blend_data.is_dirty:
            self.report({'ERROR'}, 'Please save your Blend file before using '
                                   'the Blender Cloud addon.')
            return {'CANCELLED'}

        wm = context.window_manager

        self.current_path = pillar.CloudPath(wm.last_blender_cloud_location)
        self.path_stack = []  # list of nodes that make up the current path.

        self.thumbnails_cache = cache.cache_directory('thumbnails')
        self.mouse_x = event.mouse_x
        self.mouse_y = event.mouse_y

        # See if we have to maximize the current area
        if not context.screen.show_fullscreen:
            self.maximized_area = True
            bpy.ops.screen.screen_full_area(use_hide_panels=True)

        # Add the region OpenGL drawing callback
        # draw in view space with 'POST_VIEW' and 'PRE_VIEW'
        self._draw_handle = context.space_data.draw_handler_add(
            self.draw_menu, (context,), 'WINDOW', 'POST_PIXEL')

        self.current_display_content = []
        self.loaded_images = set()
        self._scroll_reset()

        context.window.cursor_modal_set('DEFAULT')
        async_loop.AsyncModalOperatorMixin.invoke(self, context, event)
        self._new_async_task(self.async_execute(context))

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        result = async_loop.AsyncModalOperatorMixin.modal(self, context, event)
        if not {'PASS_THROUGH', 'RUNNING_MODAL'}.intersection(result):
            return result

        if event.type == 'TAB' and event.value == 'RELEASE':
            self.log.info('Ensuring async loop is running')
            async_loop.ensure_async_loop()

        if event.type == 'TIMER':
            self._scroll_smooth()
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if 'MOUSE' in event.type:
            context.area.tag_redraw()
            self.mouse_x = event.mouse_x
            self.mouse_y = event.mouse_y

        left_mouse_release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if self._state == 'PLEASE_SUBSCRIBE' and left_mouse_release:
            self.open_browser_subscribe()
            self._finish(context)
            return {'FINISHED'}

        if self._state == 'BROWSING':
            selected = self.get_clicked()

            if selected:
                context.window.cursor_set('HAND')
            else:
                context.window.cursor_set('DEFAULT')

            # Scrolling
            if event.type == 'WHEELUPMOUSE':
                self._scroll_by(MOUSE_SCROLL_PIXELS_PER_TICK)
                context.area.tag_redraw()
            elif event.type == 'WHEELDOWNMOUSE':
                self._scroll_by(-MOUSE_SCROLL_PIXELS_PER_TICK)
                context.area.tag_redraw()
            elif event.type == 'TRACKPADPAN':
                self._scroll_by(event.mouse_prev_y - event.mouse_y,
                                smooth=False)
                context.area.tag_redraw()

            if left_mouse_release:
                if selected is None:
                    # No item clicked, ignore it.
                    return {'RUNNING_MODAL'}

                if selected.is_folder:
                    self.descend_node(selected.node)
                else:
                    if selected.file_desc is None:
                        # This can happen when the thumbnail information isn't loaded yet.
                        # Just ignore the click for now.
                        # TODO: think of a way to handle this properly.
                        self.log.debug('Selected item %r has no file_desc', selected)
                        return {'RUNNING_MODAL'}
                    self.handle_item_selection(context, selected)

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    async def async_execute(self, context):
        self._state = 'CHECKING_CREDENTIALS'
        self.log.debug('Checking credentials')

        try:
            user_id = await self.check_credentials(context, REQUIRED_ROLES_FOR_TEXTURE_BROWSER)
        except pillar.NotSubscribedToCloudError:
            self.log.info('User not subscribed to Blender Cloud.')
            self._show_subscribe_screen()
            return None

        if user_id is None:
            raise pillar.UserNotLoggedInError()

        await self.async_download_previews()

    def _show_subscribe_screen(self):
        """Shows the "You need to subscribe" screen."""

        self._state = 'PLEASE_SUBSCRIBE'
        bpy.context.window.cursor_set('HAND')

    def descend_node(self, node):
        """Descends the node hierarchy by visiting this node.

        Also keeps track of the current node, so that we know where the "up" button should go.
        """

        assert isinstance(node, pillarsdk.Node), 'Wrong type %s' % node

        if isinstance(node, UpNode):
            # Going up.
            self.log.debug('Going up to %r', self.current_path)
            self.current_path = self.current_path.parent
            if self.path_stack:
                self.path_stack.pop()
            if not self.path_stack:
                self.project_name = ''
        else:
            # Going down, keep track of where we were
            if isinstance(node, ProjectNode):
                self.project_name = node['name']

            self.current_path /= node['_id']
            self.log.debug('Going down to %r', self.current_path)
            self.path_stack.append(node)

        self.browse_assets()

    @property
    def node(self):
        if not self.path_stack:
            return None
        return self.path_stack[-1]

    def _finish(self, context):
        self.log.debug('Finishing the modal operator')
        async_loop.AsyncModalOperatorMixin._finish(self, context)
        self.clear_images()

        context.space_data.draw_handler_remove(self._draw_handle, 'WINDOW')
        context.window.cursor_modal_restore()

        if self.maximized_area:
            bpy.ops.screen.screen_full_area(use_hide_panels=True)

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

        self.sort_menu()

        return menu_item

    def update_menu_item(self, node, *args) -> MenuItem:
        node_uuid = node['_id']

        # Just make this thread-safe to be on the safe side.
        with self._menu_item_lock:
            for menu_item in self.current_display_content:
                if menu_item.node_uuid == node_uuid:
                    menu_item.update(node, *args)
                    self.loaded_images.add(menu_item.icon.filepath_raw)
                    break
            else:
                raise ValueError('Unable to find MenuItem(node_uuid=%r)' % node_uuid)

        self.sort_menu()

    def sort_menu(self):
        """Sorts the self.current_display_content list."""

        if not self.current_display_content:
            return

        with self._menu_item_lock:
            self.current_display_content.sort(key=MenuItem.sort_key)

    async def async_download_previews(self):
        self._state = 'BROWSING'

        thumbnails_directory = self.thumbnails_cache
        self.log.info('Asynchronously downloading previews to %r', thumbnails_directory)
        self.log.info('Current BCloud path is %r', self.current_path)
        self.clear_images()
        self._scroll_reset()

        def thumbnail_loading(node, texture_node):
            self.add_menu_item(node, None, 'SPINNER', texture_node['name'])

        def thumbnail_loaded(node, file_desc, thumb_path):
            self.update_menu_item(node, file_desc, thumb_path, file_desc['filename'])

        project_uuid = self.current_path.project_uuid
        node_uuid = self.current_path.node_uuid

        if node_uuid:
            # Query for sub-nodes of this node.
            self.log.debug('Getting subnodes for parent node %r', node_uuid)
            children = await pillar.get_nodes(parent_node_uuid=node_uuid,
                                              node_type='group_texture')
        elif project_uuid:
            # Query for top-level nodes.
            self.log.debug('Getting subnodes for project node %r', project_uuid)
            children = await pillar.get_nodes(project_uuid=project_uuid,
                                              parent_node_uuid='',
                                              node_type='group_texture')
        else:
            # Query for projects
            self.log.debug('No node UUID and no project UUID, listing available projects')
            children = await pillar.get_texture_projects()
            for proj_dict in children:
                self.add_menu_item(ProjectNode(proj_dict), None, 'FOLDER', proj_dict['name'])
            return

        # Make sure we can go up again.
        self.add_menu_item(UpNode(), None, 'FOLDER', '.. up ..')

        # Download all child nodes
        self.log.debug('Iterating over child nodes of %r', self.current_path)
        for child in children:
            # print('  - %(_id)s = %(name)s' % child)
            if child['node_type'] not in MenuItem.SUPPORTED_NODE_TYPES:
                self.log.debug('Skipping node of type %r', child['node_type'])
                continue
            self.add_menu_item(child, None, 'FOLDER', child['name'])

        # There are only sub-nodes at the project level, no texture nodes,
        # so we won't have to bother looking for textures.
        if not node_uuid:
            return

        directory = os.path.join(thumbnails_directory, project_uuid, node_uuid)
        os.makedirs(directory, exist_ok=True)

        self.log.debug('Fetching texture thumbnails for node %r', node_uuid)
        await pillar.fetch_texture_thumbs(node_uuid, 's', directory,
                                          thumbnail_loading=thumbnail_loading,
                                          thumbnail_loaded=thumbnail_loaded,
                                          future=self.signalling_future)

    def browse_assets(self):
        self.log.debug('Browsing assets at %r', self.current_path)
        self._new_async_task(self.async_download_previews())

    def draw_menu(self, context):
        """Draws the GUI with OpenGL."""

        drawers = {
            'CHECKING_CREDENTIALS': self._draw_checking_credentials,
            'BROWSING': self._draw_browser,
            'DOWNLOADING_TEXTURE': self._draw_downloading,
            'EXCEPTION': self._draw_exception,
            'PLEASE_SUBSCRIBE': self._draw_subscribe,
        }

        if self._state in drawers:
            drawer = drawers[self._state]
            drawer(context)

        # For debugging: draw the state
        font_id = 0
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
        blf.size(font_id, 20, 72)
        blf.position(font_id, 5, 5, 0)
        blf.draw(font_id, '%s %s' % (self._state, self.project_name))
        bgl.glDisable(bgl.GL_BLEND)

    @staticmethod
    def _window_region(context):
        window_regions = [region
                          for region in context.area.regions
                          if region.type == 'WINDOW']
        return window_regions[0]

    def _draw_browser(self, context):
        """OpenGL drawing code for the BROWSING state."""

        window_region = self._window_region(context)
        content_width = window_region.width - ITEM_MARGIN_X * 2
        content_height = window_region.height - ITEM_MARGIN_Y * 2

        content_x = ITEM_MARGIN_X
        content_y = context.area.height - ITEM_MARGIN_Y - TARGET_ITEM_HEIGHT

        col_count = content_width // TARGET_ITEM_WIDTH

        item_width = (content_width - (col_count * ITEM_PADDING_X)) / col_count
        item_height = TARGET_ITEM_HEIGHT

        block_width = item_width + ITEM_PADDING_X
        block_height = item_height + ITEM_MARGIN_Y

        bgl.glEnable(bgl.GL_BLEND)
        bgl.glColor4f(0.0, 0.0, 0.0, 0.6)
        bgl.glRectf(0, 0, window_region.width, window_region.height)

        if self.current_display_content:
            bottom_y = float('inf')

            # The -1 / +2 are for extra rows that are drawn only half at the top/bottom.
            first_item_idx = max(0, int(-self.scroll_offset // block_height - 1) * col_count)
            items_per_page = int(content_height // item_height + 2) * col_count
            last_item_idx = first_item_idx + items_per_page

            for item_idx, item in enumerate(self.current_display_content):
                x = content_x + (item_idx % col_count) * block_width
                y = content_y - (item_idx // col_count) * block_height - self.scroll_offset

                item.update_placement(x, y, item_width, item_height)

                if first_item_idx <= item_idx < last_item_idx:
                    # Only draw if the item is actually on screen.
                    item.draw(highlighted=item.hits(self.mouse_x, self.mouse_y))

                bottom_y = min(y, bottom_y)
            bgl.glColor4f(0.24, 0.68, 0.91, 1)
            bgl.glRectf(0,
                        bottom_y - ITEM_MARGIN_Y,
                        window_region.width,
                        bottom_y+1 - ITEM_MARGIN_Y)
            self.scroll_offset_space_left = window_region.height - bottom_y
            self.scroll_offset_max = (self.scroll_offset -
                                      self.scroll_offset_space_left +
                                      0.25 * block_height)

        else:
            font_id = 0
            text = "Communicating with Blender Cloud"
            bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
            blf.size(font_id, 20, 72)
            text_width, text_height = blf.dimensions(font_id, text)
            blf.position(font_id,
                         content_x + content_width * 0.5 - text_width * 0.5,
                         content_y - content_height * 0.3 + text_height * 0.5, 0)
            blf.draw(font_id, text)

        bgl.glDisable(bgl.GL_BLEND)
        # bgl.glColor4f(0.0, 0.0, 0.0, 1.0)

    def _draw_downloading(self, context):
        """OpenGL drawing code for the DOWNLOADING_TEXTURE state."""

        self._draw_text_on_colour(context,
                                  'Downloading texture from Blender Cloud',
                                  (0.0, 0.0, 0.2, 0.6))

    def _draw_checking_credentials(self, context):
        """OpenGL drawing code for the CHECKING_CREDENTIALS state."""

        self._draw_text_on_colour(context,
                                  'Checking login credentials',
                                  (0.0, 0.0, 0.2, 0.6))

    def _draw_text_on_colour(self, context, text, bgcolour):
        content_height, content_width = self._window_size(context)
        bgl.glEnable(bgl.GL_BLEND)
        bgl.glColor4f(*bgcolour)
        bgl.glRectf(0, 0, content_width, content_height)

        font_id = 0
        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
        blf.size(font_id, 20, 72)
        text_width, text_height = blf.dimensions(font_id, text)

        blf.position(font_id,
                     content_width * 0.5 - text_width * 0.5,
                     content_height * 0.7 + text_height * 0.5, 0)
        blf.draw(font_id, text)
        bgl.glDisable(bgl.GL_BLEND)

    def _window_size(self, context):
        window_region = self._window_region(context)
        content_width = window_region.width
        content_height = window_region.height
        return content_height, content_width

    def _draw_exception(self, context):
        """OpenGL drawing code for the EXCEPTION state."""

        import textwrap

        content_height, content_width = self._window_size(context)

        bgl.glEnable(bgl.GL_BLEND)
        bgl.glColor4f(0.2, 0.0, 0.0, 0.6)
        bgl.glRectf(0, 0, content_width, content_height)

        font_id = 0
        ex = self.async_task.exception()
        if isinstance(ex, pillar.UserNotLoggedInError):
            ex_msg = 'You are not logged in on Blender ID. Please log in at User Preferences, ' \
                     'System, Blender ID.'
        else:
            ex_msg = str(ex)
            if not ex_msg:
                ex_msg = str(type(ex))
        text = "An error occurred:\n%s" % ex_msg
        lines = textwrap.wrap(text)

        bgl.glColor4f(1.0, 1.0, 1.0, 1.0)
        blf.size(font_id, 20, 72)
        _, text_height = blf.dimensions(font_id, 'yhBp')

        def position(line_nr):
            blf.position(font_id,
                         content_width * 0.1,
                         content_height * 0.8 - line_nr * text_height, 0)

        for line_idx, line in enumerate(lines):
            position(line_idx)
            blf.draw(font_id, line)
        bgl.glDisable(bgl.GL_BLEND)

    def _draw_subscribe(self, context):
        self._draw_text_on_colour(context,
                                  'Click to subscribe to the Blender Cloud',
                                  (0.0, 0.0, 0.2, 0.6))

    def get_clicked(self) -> MenuItem:

        for item in self.current_display_content:
            if item.hits(self.mouse_x, self.mouse_y):
                return item

        return None

    def handle_item_selection(self, context, item: MenuItem):
        """Called when the user clicks on a menu item that doesn't represent a folder."""

        from pillarsdk.utils import sanitize_filename

        self.clear_images()
        self._state = 'DOWNLOADING_TEXTURE'

        node_path_components = (node['name'] for node in self.path_stack if node is not None)
        local_path_components = [sanitize_filename(comp) for comp in node_path_components]

        top_texture_directory = bpy.path.abspath(context.scene.local_texture_dir)
        local_path = os.path.join(top_texture_directory, *local_path_components)
        meta_path = os.path.join(top_texture_directory, '.blender_cloud')

        self.log.info('Downloading texture %r to %s', item.node_uuid, local_path)
        self.log.debug('Metadata will be stored at %s', meta_path)

        file_paths = []

        def texture_downloading(file_path, file_desc, *args):
            self.log.info('Texture downloading to %s', file_path)

        def texture_downloaded(file_path, file_desc, *args):
            self.log.info('Texture downloaded to %r.', file_path)
            image_dblock = bpy.data.images.load(filepath=file_path)
            image_dblock['bcloud_file_uuid'] = file_desc['_id']
            image_dblock['bcloud_texture_node_uuid'] = item.node_uuid
            file_paths.append(file_path)

        def texture_download_completed(_):
            self.log.info('Texture download complete, inspect:\n%s', '\n'.join(file_paths))
            self._state = 'QUIT'

        signalling_future = asyncio.Future()
        self._new_async_task(pillar.download_texture(item.node, local_path,
                                                     metadata_directory=meta_path,
                                                     texture_loading=texture_downloading,
                                                     texture_loaded=texture_downloaded,
                                                     future=signalling_future))
        self.async_task.add_done_callback(texture_download_completed)

    def open_browser_subscribe(self):
        import webbrowser

        webbrowser.open_new_tab('https://cloud.blender.org/join')

        self.report({'INFO'}, 'We just started a browser for you.')

    def _scroll_smooth(self):
        diff = self.scroll_offset_target - self.scroll_offset
        if diff == 0:
            return

        if abs(round(diff)) < 1:
            self.scroll_offset = self.scroll_offset_target
            return

        self.scroll_offset += diff * 0.5

    def _scroll_by(self, amount, *, smooth=True):
        # Slow down scrolling up
        if smooth and amount < 0 and -amount > self.scroll_offset_space_left / 4:
            amount = -self.scroll_offset_space_left / 4

        self.scroll_offset_target = min(0,
                                        max(self.scroll_offset_max,
                                            self.scroll_offset_target + amount))

        if not smooth:
            self._scroll_offset = self.scroll_offset_target

    def _scroll_reset(self):
        self.scroll_offset_target = self.scroll_offset = 0


# store keymaps here to access after registration
addon_keymaps = []


def menu_draw(self, context):
    layout = self.layout
    layout.separator()
    layout.operator(BlenderCloudBrowser.bl_idname, icon='MOD_SCREW')


def register():
    bpy.utils.register_class(BlenderCloudBrowser)
    # bpy.types.INFO_MT_mesh_add.append(menu_draw)

    # handle the keymap
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        print('No addon key configuration space found, so no custom hotkeys added.')
        return

    km = kc.keymaps.new(name='Screen')
    kmi = km.keymap_items.new('pillar.browser', 'A', 'PRESS', ctrl=True, shift=True, alt=True)
    addon_keymaps.append((km, kmi))


def unregister():
    # handle the keymap
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.utils.unregister_class(BlenderCloudBrowser)
