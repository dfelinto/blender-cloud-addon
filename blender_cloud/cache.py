"""Cache management."""

import os
import sys
import logging

from . import appdirs

log = logging.getLogger(__name__)


# Add our shipped Requests-Cache wheel to the Python path
if not any('requests_cache' in path for path in sys.path):
    import glob

    # TODO: gracefully handle errors when the wheel cannot be found.
    my_dir = os.path.dirname(__file__)
    wheel = glob.glob(os.path.join(my_dir, 'requests_cache*.whl'))[0]
    sys.path.append(wheel)

import requests_cache


def cache_directory() -> str:
    """Returns an OS-specifc cache location, and ensures it exists.

    Should be replaced with a call to bpy.utils.user_resource('CACHE', ...)
    once https://developer.blender.org/T47684 is finished.
    """

    # TODO: just use bpy.utils.user_resource('CACHE', ...)

    cache_dir = os.path.join(appdirs.user_cache_dir(appname='Blender', appauthor=False), 'blender-cloud')

    os.makedirs(cache_dir, exist_ok=True)

    return cache_dir


def requests_session() -> requests_cache.CachedSession:
    """Creates a Requests-Cache session object."""

    cache_dir = cache_directory()
    cache_name = os.path.join(cache_dir, 'blender_cloud_cache')
    log.info('Storing cache in %s' % cache_name)

    req_sess = requests_cache.CachedSession(backend='sqlite',
                                            cache_name=cache_name)

    return req_sess


def debug_show_responses():

    req_sess = requests_session()

    log.info('Cache type: %s', type(req_sess.cache))
    log.info('Cached URLs:')
    for key in req_sess.cache.keys_map:
        value = req_sess.cache.keys_map[key]
        log.info('  %s = %s' % (key, value))

    log.info('Cached responses:')
    for key in req_sess.cache.responses:
        response, timekey = req_sess.cache.get_response_and_time(key)
        log.info('  %s = %s' % (key, response.content))
