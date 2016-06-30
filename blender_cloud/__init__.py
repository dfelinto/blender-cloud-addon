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
    'name': 'Blender Cloud',
    'author': 'Sybren A. Stüvel and Francesco Siddi',
    'version': (1, 2, 2),
    'blender': (2, 77, 0),
    'location': 'Addon Preferences panel, and Ctrl+Shift+Alt+A anywhere for texture browser',
    'description': 'Texture library browser and Blender Sync. Requires the Blender ID addon '
                   'and Blender 2.77a or newer.',
    'wiki_url': 'http://wiki.blender.org/index.php/Extensions:2.6/Py/'
                'Scripts/System/BlenderCloud',
    'category': 'System',
    'support': 'OFFICIAL'
}

import logging

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

log = logging.getLogger(__name__)


def register():
    """Late-loads and registers the Blender-dependent submodules."""

    import sys

    _monkey_patch_requests()

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
        settings_sync = reload_mod('settings_sync')
        reload_mod('blendfile')
    else:
        from . import blender, gui, async_loop, settings_sync, blendfile

    async_loop.setup_asyncio_executor()
    async_loop.register()

    gui.register()
    blender.register()
    settings_sync.register()


def _monkey_patch_requests():
    """Monkey-patch old versions of Requests.

    This is required for the Mac version of Blender 2.77a.
    """

    import requests

    if requests.__build__ >= 0x020601:
        return

    log.info('Monkey-patching requests version %s', requests.__version__)
    from requests.packages.urllib3.response import HTTPResponse
    HTTPResponse.chunked = False
    HTTPResponse.chunk_left = None


def unregister():
    from . import blender, gui, async_loop, settings_sync

    settings_sync.unregister()
    blender.unregister()
    gui.unregister()
    async_loop.unregister()
