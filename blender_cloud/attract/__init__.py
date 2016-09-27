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

# Old info, kept here for reference, so that we can merge wiki pages,
# descriptions, etc.
#
# bl_info = {
#     "name": "Attract",
#     "author": "Francesco Siddi, Inês Almeida, Antony Riakiotakis",
#     "version": (0, 2, 0),
#     "blender": (2, 76, 0),
#     "location": "Video Sequence Editor",
#     "description":
#         "Blender integration with the Attract task tracking service"
#         ". *requires the Blender ID add-on",
#     "wiki_url": "http://wiki.blender.org/index.php/Extensions:2.6/Py/"
#                 "Scripts/Workflow/Attract",
#     "category": "Workflow",
#     "support": "TESTING"
# }

import functools
import logging

if "bpy" in locals():
    import importlib

    importlib.reload(draw)
else:
    from . import draw

import bpy
from pillarsdk.nodes import Node
from pillarsdk.projects import Project
from pillarsdk.exceptions import ResourceNotFound

from bpy.types import Operator, Panel, AddonPreferences

log = logging.getLogger(__name__)


def active_strip(context):
    try:
        return context.scene.sequence_editor.active_strip
    except AttributeError:
        return None


def remove_atc_props(strip):
    """Resets the attract custom properties assigned to a VSE strip"""

    strip.atc_name = ""
    strip.atc_description = ""
    strip.atc_object_id = ""
    strip.atc_is_synced = False


class ToolsPanel(Panel):
    bl_label = 'Attract'
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'

    def draw_header(self, context):
        strip = active_strip(context)
        if strip and strip.atc_object_id:
            self.layout.prop(strip, 'atc_is_synced', text='')

    def draw(self, context):
        strip = active_strip(context)
        layout = self.layout
        strip_types = {'MOVIE', 'IMAGE'}
        if strip and strip.atc_object_id and strip.type in strip_types:
            layout.prop(strip, 'atc_name', text='Name')
            layout.prop(strip, 'atc_status', text='Status')

            # Create a special sub-layout for read-only properties.
            ro_sub = layout.column(align=True)
            ro_sub.enabled = False
            ro_sub.prop(strip, 'atc_description', text='Description')
            ro_sub.prop(strip, 'atc_notes', text='Notes')

            if strip.atc_is_synced:
                row = layout.row(align=True)
                row.operator('attract.shot_submit_update')
                row.operator(AttractShotFetchUpdate.bl_idname,
                             text='', icon='FILE_REFRESH')

                # Group more dangerous operations.
                dangerous_sub = layout.column(align=True)
                dangerous_sub.operator('attract.shot_delete')
                dangerous_sub.operator('attract.strip_unlink')

        elif strip and strip.type in strip_types:
            layout.operator('attract.shot_submit_new')
            layout.operator('attract.shot_relink')
        else:
            layout.label(text='Select a Movie or Image strip')
        layout.operator('attract.shots_order_update')


class AttractOperatorMixin:
    """Mix-in class for all Attract operators."""

    def _project_needs_setup_error(self):
        self.report({'ERROR'}, 'Your Blender Cloud project is not set up for Attract.')
        return {'CANCELLED'}

    @functools.lru_cache()
    def find_project(self, project_uuid: str) -> Project:
        """Finds a single project.

        Caches the result in memory to prevent more than one call to Pillar.
        """

        from .. import pillar

        project = pillar.sync_call(Project.find_one, {'where': {'_id': project_uuid}})
        return project

    def find_node_type(self, node_type_name: str) -> dict:
        from .. import pillar, blender

        prefs = blender.preferences()
        project = self.find_project(prefs.attract_project.project)

        # FIXME: Eve doesn't seem to handle the $elemMatch projection properly,
        # even though it works fine in MongoDB itself. As a result, we have to
        # search for the node type.
        node_type_list = project['node_types']
        node_type = next((nt for nt in node_type_list if nt['name'] == node_type_name), None)

        if not node_type:
            return self._project_needs_setup_error()

        return node_type


class AttractShotSubmitNew(AttractOperatorMixin, Operator):
    bl_idname = "attract.shot_submit_new"
    bl_label = "Submit to Attract"

    @classmethod
    def poll(cls, context):
        strip = active_strip(context)
        return not strip.atc_object_id

    def execute(self, context):
        from .. import pillar, blender

        strip = active_strip(context)
        if strip.atc_object_id:
            return

        node_type = self.find_node_type('attract_shot')
        if isinstance(node_type, set):  # in case of error
            return node_type

        # Define the shot properties
        user_uuid = pillar.pillar_user_uuid()
        if not user_uuid:
            self.report({'ERROR'}, 'Your Blender Cloud user ID is not known, '
                                   'update your credentials.')
            return {'CANCELLED'}

        prop = {'name': strip.name,
                'description': '',
                'properties': {'status': 'on_hold',
                               'notes': '',
                               'trim_start_in_frames': strip.frame_offset_start,
                               'duration_in_edit_in_frames': strip.frame_final_duration,
                               'cut_in_timeline_in_frames': strip.frame_final_start},
                'order': 0,
                'node_type': 'attract_shot',
                'project': blender.preferences().attract_project.project,
                'user': user_uuid}

        # Create a Node item with the attract API
        node = Node(prop)
        post = pillar.sync_call(node.create)

        # Populate the strip with the freshly generated ObjectID and info
        if not post:
            self.report({'ERROR'}, 'Error creating node! Check the console for now.')
            return {'CANCELLED'}

        strip.atc_object_id = node['_id']
        strip.atc_is_synced = True
        strip.atc_name = node['name']
        strip.atc_description = node['description']
        strip.atc_notes = node['properties']['notes']

        draw.tag_redraw_all_sequencer_editors()

        return {'FINISHED'}


class AttractShotFetchUpdate(AttractOperatorMixin, Operator):
    bl_idname = "attract.shot_fetch_update"
    bl_label = "Fetch update from Attract"

    @classmethod
    def poll(cls, context):
        strip = active_strip(context)
        return strip is not None and getattr(strip, 'atc_object_id', None)

    def execute(self, context):
        from .. import pillar

        strip = active_strip(context)
        try:
            node = pillar.sync_call(Node.find, strip.atc_object_id)
        except ResourceNotFound:
            self.report({'ERROR'}, 'Shot %r not found on the Attract server, unable to relink.'
                        % strip.strip_atc_object_id)
            return {'CANCELLED'}

        strip.atc_is_synced = True
        strip.atc_name = node.name

        # We do NOT set the position/cuts of the shot, that always has to come from Blender.
        strip.atc_status = node.properties.status
        strip.atc_notes = node.properties.notes or ''
        strip.atc_description = node.description or ''

        draw.tag_redraw_all_sequencer_editors()

        self.report({'INFO'}, "Shot {0} refreshed".format(strip.atc_name))

        return {'FINISHED'}


class AttractShotRelink(AttractShotFetchUpdate):
    bl_idname = "attract.shot_relink"
    bl_label = "Relink from Attract"

    strip_atc_object_id = bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        strip = active_strip(context)
        return strip is not None and not getattr(strip, 'atc_object_id', None)

    def execute(self, context):
        strip = active_strip(context)
        strip.atc_object_id = self.strip_atc_object_id
        status = AttractShotFetchUpdate.execute(self, context)

        if 'FINISHED' in status:
            self.report({'INFO'}, "Shot {0} relinked".format(strip.atc_name))
        return status

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, 'strip_atc_object_id', text='Shot ID')


class AttractShotSubmitUpdate(AttractOperatorMixin, Operator):
    bl_idname = 'attract.shot_submit_update'
    bl_label = 'Submit update'
    bl_description = 'Sends local changes to Attract'

    def execute(self, context):
        import pillarsdk
        from .. import pillar

        strip = active_strip(context)
        patch = {
            'op': 'from-blender',
            '$set': {
                'name': strip.atc_name,
                'properties.trim_start_in_frames': strip.frame_offset_start,
                'properties.duration_in_edit_in_frames': strip.frame_final_duration,
                'properties.cut_in_timeline_in_frames': strip.frame_final_start,
                'properties.status': strip.atc_status,
            }
        }

        node = pillarsdk.Node({'_id': strip.atc_object_id})
        result = pillar.sync_call(node.patch, patch)
        log.info('PATCH result: %s', result)

        self.report({'INFO'}, 'Shot was updated on Attract')
        return {'FINISHED'}


class AttractShotDelete(AttractOperatorMixin, Operator):
    bl_idname = 'attract.shot_delete'
    bl_label = 'Delete'
    bl_description = 'Remove from Attract'

    def execute(self, context):
        from .. import pillar

        strip = active_strip(context)
        node = pillar.sync_call(Node.find, strip.atc_object_id)
        if not pillar.sync_call(node.delete):
            print('Unable to delete the strip node on Attract.')
            return {'CANCELLED'}

        remove_atc_props(strip)
        draw.tag_redraw_all_sequencer_editors()
        return {'FINISHED'}


class AttractStripUnlink(AttractOperatorMixin, Operator):
    bl_idname = 'attract.strip_unlink'
    bl_label = 'Unlink'
    bl_description = 'Remove Attract props from the strip'

    def execute(self, context):
        strip = active_strip(context)

        atc_object_id = getattr(strip, 'atc_object_id')
        remove_atc_props(strip)

        if atc_object_id:
            self.report({'INFO'}, 'Shot %s has been unlinked from Attract.' % atc_object_id)

        draw.tag_redraw_all_sequencer_editors()
        return {'FINISHED'}


class AttractShotsOrderUpdate(AttractOperatorMixin, Operator):
    bl_idname = 'attract.shots_order_update'
    bl_label = 'Update shots order'

    def execute(self, context):
        from .. import pillar

        # Get all shot nodes from server, build dictionary using ObjectID
        # as indexes
        node_type = self.find_node_type('attract_shot')
        if isinstance(node_type, set):  # in case of error
            return node_type

        shots = pillar.sync_call(Node.all, {
            'where': {'node_type': node_type._id},
            'max_results': 100})

        shots = shots._items

        # TODO (fsiddi) take into account pagination. Currently we do not do it
        # and it makes this dict useless.
        # We should use the pagination info from the node_type_list query and
        # keep querying until we have all the items.
        shots_dict = {}
        for shot in shots:
            shots_dict[shot._id] = shot

        # Build ordered list of strips from the edit.
        strips_with_atc_object_id = [strip
                                     for strip in context.scene.sequence_editor.sequences_all
                                     if strip.atc_object_id]

        strips_with_atc_object_id.sort(
            key=lambda strip: strip.frame_final_start)

        for index, strip in enumerate(strips_with_atc_object_id):
            """
            # Currently we use the code below to force update all nodes.
            # Check that the shot is in the list of retrieved shots
            if strip.atc_order != index: #or shots_dict[strip.atc_object_id]['order'] != index:
                # If there is an update in the order, retrieve and update
                # the node, as well as the VSE strip
                # shot_node = Node.find(strip.atc_object_id)
                # shot_node.order = index
                # shot_node.update()
                # strip.atc_order = index
                print ("{0} > {1}".format(strip.atc_order, index))
            """
            # We get all nodes one by one. This is bad and stupid.
            try:
                shot_node = pillar.sync_call(Node.find, strip.atc_object_id)
                shot_node.order = index + 1
                pillar.sync_call(shot_node.update)
                print('{0} - updating {1}'.format(shot_node.order, shot_node.name))
                strip.atc_order = index
            except ResourceNotFound:
                # Reset the attract properties for any shot not found on the server
                print("Warning: shot {0} not found".format(strip.atc_object_id))
                remove_atc_props(strip)

        draw.tag_redraw_all_sequencer_editors()
        return {'FINISHED'}


def register():
    bpy.types.Sequence.atc_is_synced = bpy.props.BoolProperty(name="Is synced")
    bpy.types.Sequence.atc_object_id = bpy.props.StringProperty(name="Attract Object ID")
    bpy.types.Sequence.atc_name = bpy.props.StringProperty(name="Shot Name")
    bpy.types.Sequence.atc_description = bpy.props.StringProperty(name="Shot description")
    bpy.types.Sequence.atc_notes = bpy.props.StringProperty(name="Shot notes")

    # TODO: get this from the project's node type definition.
    bpy.types.Sequence.atc_status = bpy.props.EnumProperty(
        items=[
            ('on_hold', 'On hold', 'The shot is on hold'),
            ('todo', 'Todo', 'Waiting'),
            ('in_progress', 'In progress', 'The show has been assigned'),
            ('review', 'Review', ''),
            ('final', 'Final', ''),
        ],
        name="Status")
    bpy.types.Sequence.atc_order = bpy.props.IntProperty(name="Order")

    bpy.utils.register_class(ToolsPanel)
    bpy.utils.register_class(AttractShotSubmitNew)
    bpy.utils.register_class(AttractShotRelink)
    bpy.utils.register_class(AttractShotSubmitUpdate)
    bpy.utils.register_class(AttractShotDelete)
    bpy.utils.register_class(AttractStripUnlink)
    bpy.utils.register_class(AttractShotsOrderUpdate)
    bpy.utils.register_class(AttractShotFetchUpdate)
    draw.callback_enable()


def unregister():
    draw.callback_disable()
    del bpy.types.Sequence.atc_is_synced
    del bpy.types.Sequence.atc_object_id
    del bpy.types.Sequence.atc_name
    del bpy.types.Sequence.atc_description
    del bpy.types.Sequence.atc_notes
    del bpy.types.Sequence.atc_status
    del bpy.types.Sequence.atc_order
    bpy.utils.unregister_module(__name__)
