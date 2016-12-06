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

import contextlib
import functools
import logging

if "bpy" in locals():
    import importlib

    pillar = importlib.reload(pillar)
    async_loop = importlib.reload(async_loop)
else:
    from .. import pillar, async_loop

import bpy
import pillarsdk
from pillarsdk.nodes import Node
from pillarsdk.projects import Project
from pillarsdk import exceptions as sdk_exceptions

from bpy.types import Operator, Panel

from bpy.props import (
        EnumProperty,
        )

log = logging.getLogger(__name__)


def prettify(text):
    """Prepare the job_type names to the UI"""
    return text.title().replace("_", " ")


class DynamicProperty():
    lookup_type = {
            'string': "StringProperty",
            'integer': "IntProperty",
            'bool': "BoolProperty"}

    def __init__(self, name, data):
        self._name = name
        self._data = data
        self._text = ""

        if self._validate(data):
            self._process()

    @classmethod
    def _validate(cls, data):
        return data.get('type') in cls.lookup_type

    def _class_name(self):
        return self.lookup_type.get(self._data.get('type'))

    def _process(self):
        self._text = "{0}(".format(self._class_name())
        self._text += "name='{0}'".format(prettify(self._name))
        self._text += self._get_value('min')
        self._text += self._get_value('max')
        self._text += self._get_value('default')
        self._text += self._get_value('description')
        self._text += ")"

    def _get_value(self, value_name, name=None):
        value = self._data.get(value_name, None)
        name = value_name if name is None else name

        if value is None:
            return ""

        if type(value) == str:
            return ',{0}="{1}"'.format(name, value)
        else:
            return ",{0}={1}".format(name, value)

    @property
    def text(self):
        return self._text


class FLAMENCO_OT_job_dispatch(Operator):
    bl_idname = "flamenco.job_dispatch"
    bl_label = "Dispatch Job"

    def execute(self, context):
        self.report({'ERROR'}, "Not implemented yet")
        return {'FINISHED'}

class FLAMENCO_OT_managers_refresh(Operator):
    bl_idname = "flamenco.managers_refresh"
    bl_label = "Refresh Managers"

    def execute(self, context):
        from .. import blender
        blender.bcloud_available_managers_refresh(self, context)
        return {'FINISHED'}


class FLAMENCO_PT_main(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS'
    bl_category = 'Flamenco'
    bl_label = 'Render Manager'

    def draw(self, context):
        from .. import blender

        prefs = blender.preferences()
        project_info = prefs.attract_project

        layout = self.layout
        col = layout.column()

        row = col.row(align=True)
        row.prop(project_info, "manager")
        row.operator("flamenco.managers_refresh", icon='FILE_REFRESH', text="")

        col.prop(project_info, "job_type")
        col.separator()

        box = col.box()
        job_type = blender.bcloud_job_type_get()
        job_type_data = project_info.job_type_props

        for var in job_type:
            box.prop(job_type_data, var)

        col.separator()
        col.operator("flamenco.job_dispatch")


def register():
    bpy.utils.register_class(FLAMENCO_OT_managers_refresh)
    bpy.utils.register_class(FLAMENCO_OT_job_dispatch)
    bpy.utils.register_class(FLAMENCO_PT_main)


def unregister():
    bpy.utils.unregister_class(FLAMENCO_PT_main)
    bpy.utils.unregister_class(FLAMENCO_OT_managers_refresh)
    bpy.utils.unregister_class(FLAMENCO_OT_job_dispatch)
