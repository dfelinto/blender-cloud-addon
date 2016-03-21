"""HTTP Cache management.

This module configures a cached session for the Requests package.
It allows for filesystem-based caching of HTTP requests.

Requires the 3rd party packages CacheControl and lockfile.
"""

import os
import logging
import requests
import cachecontrol
from cachecontrol.caches import FileCache

from . import appdirs

log = logging.getLogger(__name__)
_session = None  # requests.Session object that's set up for caching by requests_session().


def cache_directory() -> str:
    """Returns an OS-specifc cache location, and ensures it exists.

    Should be replaced with a call to bpy.utils.user_resource('CACHE', ...)
    once https://developer.blender.org/T47684 is finished.
    """

    # TODO: just use bpy.utils.user_resource('CACHE', ...)

    cache_dir = os.path.join(appdirs.user_cache_dir(appname='Blender', appauthor=False), 'blender_cloud')

    os.makedirs(cache_dir, exist_ok=True)

    return cache_dir


def requests_session() -> requests.Session:
    """Creates a Requests-Cache session object."""

    global _session

    if _session is not None:
        return _session

    cache_dir = cache_directory()
    cache_name = os.path.join(cache_dir, 'blender_cloud_http')
    log.info('Storing cache in %s' % cache_name)

    _session = cachecontrol.CacheControl(sess=requests.session(),
                                         cache=FileCache(cache_name))

    return _session
