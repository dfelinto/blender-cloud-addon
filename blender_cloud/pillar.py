import sys
import os

# Add our shipped Pillar SDK wheel to the Python path
if not any('pillar_sdk' in path for path in sys.path):
    import glob

    # TODO: gracefully handle errors when the wheel cannot be found.
    my_dir = os.path.dirname(__file__)
    pillar_wheel = glob.glob(os.path.join(my_dir, 'pillar_sdk*.whl'))[0]
    sys.path.append(pillar_wheel)

import pillarsdk
import pillarsdk.exceptions
import bpy

_pillar_api = None  # will become a pillarsdk.Api object.


class UserNotLoggedInError(RuntimeError):
    """Raised when the user should be logged in on Blender ID, but isn't.

    This is basically for every interaction with Pillar.
    """


def blender_id_profile() -> dict:
    """Returns the Blender ID profile of the currently logged in user."""

    active_user_id = getattr(bpy.context.window_manager, 'blender_id_active_profile', None)
    if not active_user_id:
        return None

    import blender_id.profiles
    return blender_id.profiles.get_active_profile()


def pillar_api() -> pillarsdk.Api:
    """Returns the Pillar SDK API object for the current user.

    The user must be logged in.
    """

    global _pillar_api

    # Only return the Pillar API object if the user is still logged in.
    profile = blender_id_profile()
    if not profile:
        raise UserNotLoggedInError()

    if _pillar_api is None:
        endpoint = bpy.context.user_preferences.addons['blender_cloud'].preferences.pillar_server
        _pillar_api = pillarsdk.Api(endpoint=endpoint,
                                    username=profile['username'],
                                    password=None,
                                    token=profile['token'])

    return _pillar_api


def get_project_uuid(project_url: str) -> str:
    """Returns the UUID for the project, given its '/p/<project_url>' string."""

    try:
        project = pillarsdk.Project.find_one({
            'where': {'url': project_url},
            'projection': {'permissions': 1},
        }, api=pillar_api())
    except pillarsdk.exceptions.ResourceNotFound:
        print('Project with URL %r does not exist' % project_url)
        return None

    print('Found project %r' % project)
    return project['_id']


def get_nodes(project_uuid: str, parent_node_uuid: str = '') -> list:
    if not parent_node_uuid:
        parent_spec = {'$exists': False}
    else:
        parent_spec = parent_node_uuid

    children = pillarsdk.Node.all({
        'projection': {'name': 1, 'parent': 1, 'node_type': 1,
                       'properties.order': 1, 'properties.status': 1,
                       'properties.content_type': 1,
                       'permissions': 1, 'project': 1,  # for permission checking
                       },
        'where': {'project': project_uuid,
                  'parent': parent_spec,
                  'properties.status': 'published'},
        'sort': 'properties.order'},
        api=pillar_api())

    return children['_items']
