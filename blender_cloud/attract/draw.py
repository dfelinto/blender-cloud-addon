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

# <pep8-80 compliant>

import bpy

def get_strip_rectf(strip):
     # Get x and y in terms of the grid's frames and channels
    x1 = strip.frame_final_start
    x2 = strip.frame_final_end
    y1 = strip.channel + 0.2
    y2 = y1 + 0.25

    return [x1, y1, x2, y2]


def draw_underline_in_strip(scroller_width, strip_coords, curx, color):
    from bgl import glColor4f, glRectf, glEnable, glDisable, GL_BLEND

    context = bpy.context

    # Strip coords
    s_x1, s_y1, s_x2, s_y2 = strip_coords

    # Drawing coords
    x = 0 
    d_y1 = s_y1
    d_y2 = s_y2
    d_x1 = s_x1
    d_x2 = s_x2

    # be careful not to override the current frame line
    cf_x = context.scene.frame_current_final
    y = 0

    r, g, b, a = color
    glColor4f(r, g, b, a)
    glEnable(GL_BLEND)

    # // this checks if the strip range overlaps the current f. label range
    # // then it would need a polygon? to draw around it
    # // TODO: check also if label display is ON
    # Check if the current frame label overlaps the strip
    # label_height = scroller_width * 2
    # if d_y1 < label_height:
    #    if cf_x < d_x2 and d_x1 < cf_x + label_height:
    #        print("ALARM!!")

    if d_x1 < cf_x and cf_x < d_x2:
        # Bad luck, the line passes our strip
        glRectf(d_x1, d_y1, cf_x - curx, d_y2)
        glRectf(cf_x + curx, d_y1, d_x2, d_y2)
    else:
        # Normal, full rectangle draw
        glRectf(d_x1, d_y1, d_x2, d_y2)

    glDisable(GL_BLEND)


def draw_callback_px():
    context = bpy.context

    if not context.scene.sequence_editor:
        return

    # Calculate scroller width, dpi and pixelsize dependent
    pixel_size = context.user_preferences.system.pixel_size
    dpi = context.user_preferences.system.dpi
    dpi_fac = pixel_size * dpi / 72
    # A normal widget unit is 20, but the scroller is apparently 16
    scroller_width = 16 * dpi_fac

    region = context.region
    xwin1, ywin1 = region.view2d.region_to_view(0, 0)
    xwin2, ywin2 = region.view2d.region_to_view(region.width, region.height)
    curx, cury = region.view2d.region_to_view(1, 0)
    curx = curx - xwin1

    for strip in context.scene.sequence_editor.sequences:
        if strip.atc_object_id:

            # Get corners (x1, y1), (x2, y2) of the strip rectangle in px region coords
            strip_coords = get_strip_rectf(strip)

            #check if any of the coordinates are out of bounds
            if strip_coords[0] > xwin2 or strip_coords[2] < xwin1 or strip_coords[1] > ywin2 or strip_coords[3] < ywin1:
                continue

            # Draw
            color = [1.0, 0, 1.0, 0.5]
            draw_underline_in_strip(scroller_width, strip_coords, curx, color)


def tag_redraw_all_sequencer_editors():
    context = bpy.context

    # Py cant access notifiers
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()

# This is a list so it can be changed instead of set
# if it is only changed, it does not have to be declared as a global everywhere
cb_handle = []


def callback_enable():
    if cb_handle:
        return

    cb_handle[:] = bpy.types.SpaceSequenceEditor.draw_handler_add(
        draw_callback_px, (), 'WINDOW', 'POST_VIEW'),

    tag_redraw_all_sequencer_editors()


def callback_disable():
    if not cb_handle:
        return

    bpy.types.SpaceSequenceEditor.draw_handler_remove(cb_handle[0], 'WINDOW')

    tag_redraw_all_sequencer_editors()
