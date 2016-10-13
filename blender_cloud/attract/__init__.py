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
from pillarsdk import exceptions as sdk_exceptions

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

        layout.operator(AttractShotSubmitSelected.bl_idname)


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

    def submit_new_strip(self, strip):
        from .. import pillar, blender

        # Define the shot properties
        user_uuid = pillar.pillar_user_uuid()
        if not user_uuid:
            self.report({'ERROR'}, 'Your Blender Cloud user ID is not known, '
                                   'update your credentials.')
            return {'CANCELLED'}

        prop = {'name': strip.name,
                'description': '',
                'properties': {'status': 'todo',
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
        strip.atc_status = node['properties']['status']

        draw.tag_redraw_all_sequencer_editors()

    def submit_update(self, strip):
        import pillarsdk
        from .. import pillar

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

    def relink(self, strip, atc_object_id):
        from .. import pillar

        try:
            node = pillar.sync_call(Node.find, atc_object_id)
        except (sdk_exceptions.ResourceNotFound, sdk_exceptions.MethodNotAllowed):
            self.report({'ERROR'}, 'Shot %r not found on the Attract server, unable to relink.'
                        % atc_object_id)
            return {'CANCELLED'}

        strip.atc_is_synced = True
        strip.atc_name = node.name
        strip.atc_object_id = node['_id']

        # We do NOT set the position/cuts of the shot, that always has to come from Blender.
        strip.atc_status = node.properties.status
        strip.atc_notes = node.properties.notes or ''
        strip.atc_description = node.description or ''

        draw.tag_redraw_all_sequencer_editors()


class AttractShotSubmitNew(AttractOperatorMixin, Operator):
    bl_idname = "attract.shot_submit_new"
    bl_label = "Submit to Attract"

    @classmethod
    def poll(cls, context):
        strip = active_strip(context)
        return not strip.atc_object_id

    def execute(self, context):
        strip = active_strip(context)
        if strip.atc_object_id:
            return

        node_type = self.find_node_type('attract_shot')
        if isinstance(node_type, set):  # in case of error
            return node_type

        return self.submit_new_strip(strip) or {'FINISHED'}


class AttractShotFetchUpdate(AttractOperatorMixin, Operator):
    bl_idname = "attract.shot_fetch_update"
    bl_label = "Fetch update from Attract"

    @classmethod
    def poll(cls, context):
        strip = active_strip(context)
        return strip is not None and getattr(strip, 'atc_object_id', None)

    def execute(self, context):
        strip = active_strip(context)

        status = self.relink(strip, strip.atc_object_id)
        if isinstance(status, set):
            return status

        self.report({'INFO'}, "Shot {0} refreshed".format(strip.atc_name))
        return {'FINISHED'}


class AttractShotRelink(AttractShotFetchUpdate):
    bl_idname = "attract.shot_relink"
    bl_label = "Relink with Attract"

    strip_atc_object_id = bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        strip = active_strip(context)
        return strip is not None and not getattr(strip, 'atc_object_id', None)

    def execute(self, context):
        strip = active_strip(context)

        status = self.relink(strip, self.strip_atc_object_id)
        if isinstance(status, set):
            return status

        strip.atc_object_id = self.strip_atc_object_id
        self.report({'INFO'}, "Shot {0} relinked".format(strip.atc_name))

        return {'FINISHED'}

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
        strip = active_strip(context)
        self.submit_update(strip)

        self.report({'INFO'}, 'Shot was updated on Attract')
        return {'FINISHED'}


class AttractShotDelete(AttractOperatorMixin, Operator):
    bl_idname = 'attract.shot_delete'
    bl_label = 'Delete'
    bl_description = 'Remove from Attract'

    confirm = bpy.props.BoolProperty(name='confirm')

    def execute(self, context):
        from .. import pillar

        if not self.confirm:
            self.report({'WARNING'}, 'Delete aborted.')
            return {'CANCELLED'}

        strip = active_strip(context)
        node = pillar.sync_call(Node.find, strip.atc_object_id)
        if not pillar.sync_call(node.delete):
            print('Unable to delete the strip node on Attract.')
            return {'CANCELLED'}

        remove_atc_props(strip)
        draw.tag_redraw_all_sequencer_editors()
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, 'confirm', text="I hereby confirm I want to delete this shot.")


class AttractStripUnlink(AttractOperatorMixin, Operator):
    bl_idname = 'attract.strip_unlink'
    bl_label = 'Unlink'
    bl_description = 'Remove Attract props from the selected strip(s)'

    def execute(self, context):
        for strip in context.selected_sequences:
            atc_object_id = getattr(strip, 'atc_object_id')
            remove_atc_props(strip)

            if atc_object_id:
                self.report({'INFO'}, 'Shot %s has been unlinked from Attract.' % atc_object_id)

        draw.tag_redraw_all_sequencer_editors()
        return {'FINISHED'}


class AttractShotSubmitSelected(AttractOperatorMixin, Operator):
    bl_idname = 'attract.submit_selected'
    bl_label = 'Submit all selected'
    bl_description = 'Submits all selected strips to Attract'

    @classmethod
    def poll(cls, context):
        return bool(context.selected_sequences)

    def execute(self, context):
        # Check that the project is set up for Attract.
        node_type = self.find_node_type('attract_shot')
        if isinstance(node_type, set):
            return node_type

        for strip in context.selected_sequences:
            status = self.submit(strip)
            if isinstance(status, set):
                return status

        self.report({'INFO'}, 'All selected strips sent to Attract.')

        return {'FINISHED'}

    def submit(self, strip):
        atc_object_id = getattr(strip, 'atc_object_id', None)

        # Submit as new?
        if not atc_object_id:
            return self.submit_new_strip(strip)

        # Or just save to Attract.
        return self.submit_update(strip)


class ATTRACT_OT_open_meta_blendfile(AttractOperatorMixin, Operator):
    bl_idname = 'attract.open_meta_blendfile'
    bl_label = 'Open Blendfile'
    bl_description = 'Open Blendfile from movie strip metadata'

    @classmethod
    def poll(cls, context):
        return bool(any(cls.filename_from_metadata(s) for s in context.selected_sequences))

    @staticmethod
    def filename_from_metadata(strip):
        """Returns the blendfile name from the strip metadata, or None."""

        # Metadata is a dict like:
        # meta = {'END_FRAME': '88',
        #         'BLEND_FILE': 'metadata-test.blend',
        #         'SCENE': 'SüperSčene',
        #         'FRAME_STEP': '1',
        #         'START_FRAME': '32'}

        meta = strip.get('metadata', None)
        if not meta:
            return None

        return meta.get('BLEND_FILE', None) or None

    def execute(self, context):
        for strip in context.selected_sequences:
            meta = strip.get('metadata', None)
            if not meta:
                continue

            fname = meta.get('BLEND_FILE', None)
            if not fname: continue

            scene = meta.get('SCENE', None)
            self.open_in_new_blender(fname, scene)

        return {'FINISHED'}

    def open_in_new_blender(self, fname, scene):
        """
        :type fname: str
        :type scene: str
        """
        import subprocess
        import sys

        cmd = [
            bpy.app.binary_path,
            str(fname),
        ]

        cmd[1:1] = [v for v in sys.argv if v.startswith('--enable-')]

        if scene:
            cmd.extend(['--python-expr',
                       'import bpy; bpy.context.screen.scene = bpy.data.scenes["%s"]' % scene])
            cmd.extend(['--scene', scene])

        subprocess.Popen(cmd)


def draw_strip_movie_meta(self, context):
    strip = active_strip(context)
    if not strip:
        return

    meta = strip.get('metadata', None)
    if not meta:
        return None

    box = self.layout.column(align=True)
    row = box.row(align=True)
    fname = meta.get('BLEND_FILE', None) or None
    if fname:
        row.label('Original Blendfile: %s' % fname)
        row.operator(ATTRACT_OT_open_meta_blendfile.bl_idname,
                     text='', icon='FILE_BLEND')
    sfra = meta.get('START_FRAME', '?')
    efra = meta.get('END_FRAME', '?')
    box.label('Original frame range: %s-%s' % (sfra, efra))


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

    bpy.types.SEQUENCER_PT_edit.append(draw_strip_movie_meta)

    bpy.utils.register_class(ToolsPanel)
    bpy.utils.register_class(AttractShotSubmitNew)
    bpy.utils.register_class(AttractShotRelink)
    bpy.utils.register_class(AttractShotSubmitUpdate)
    bpy.utils.register_class(AttractShotDelete)
    bpy.utils.register_class(AttractStripUnlink)
    bpy.utils.register_class(AttractShotFetchUpdate)
    bpy.utils.register_class(AttractShotSubmitSelected)
    bpy.utils.register_class(ATTRACT_OT_open_meta_blendfile)
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
