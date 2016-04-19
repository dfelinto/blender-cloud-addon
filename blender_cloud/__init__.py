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
    "version": (0, 2, 0),
    "blender": (2, 77, 0),
    "location": "Ctrl+Shift+Alt+A anywhere",
    "description": "Allows downloading of textures from the Blender Cloud. Requires "
                   "the Blender ID addon.",
    "wiki_url": "http://wiki.blender.org/index.php/Extensions:2.6/Py/"
                "Scripts/System/BlenderCloud",
    "category": "System",
    "support": "TESTING"
}

import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)-15s %(levelname)8s %(name)s %(message)s')
logging.getLogger('cachecontrol').setLevel(logging.DEBUG)
logging.getLogger(__name__).setLevel(logging.DEBUG)

# Support reloading
if 'pillar' in locals():
    import importlib

    wheels = importlib.reload(wheels)
    wheels.load_wheels()

    pillar = importlib.reload(pillar)
    cache = importlib.reload(cache)
else:
    from . import wheels
    wheels.load_wheels()

    from . import pillar, cache


def register():
    """Late-loads and registers the Blender-dependent submodules."""

    import sys

    # Support reloading
    if '%s.blender' % __name__ in sys.modules:
        import importlib

        def reload_mod(name):
            modname = '%s.%s' % (__name__, name)
            module = importlib.reload(sys.modules[modname])
            sys.modules[modname] = module
            return module

        blender = reload_mod('blender')
        gui = reload_mod('gui')
        async_loop = reload_mod('async_loop')
    else:
        from . import blender, gui, async_loop

    async_loop.setup_asyncio_executor()
    async_loop.register()

    blender.register()
    gui.register()


def unregister():
    from . import blender, gui, async_loop

    gui.unregister()
    blender.unregister()
    async_loop.unregister()

