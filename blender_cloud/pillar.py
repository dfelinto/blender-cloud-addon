import sys
import os
import concurrent.futures
import functools

# Add our shipped Pillar SDK wheel to the Python path
if not any('pillar_sdk' in path for path in sys.path):
    import glob

    # TODO: gracefully handle errors when the wheel cannot be found.
    my_dir = os.path.dirname(__file__)
    pillar_wheel = glob.glob(os.path.join(my_dir, 'pillar_sdk*.whl'))[0]
    sys.path.append(pillar_wheel)

import pillarsdk
import pillarsdk.exceptions

_pillar_api = None  # will become a pillarsdk.Api object.


class UserNotLoggedInError(RuntimeError):
    """Raised when the user should be logged in on Blender ID, but isn't.

    This is basically for every interaction with Pillar.
    """


def blender_id_profile() -> dict:
    """Returns the Blender ID profile of the currently logged in user."""

    import bpy

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
    import bpy

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


def get_nodes(project_uuid: str = None, parent_node_uuid: str = None) -> list:
    """Gets nodes for either a project or given a parent node.

    @param project_uuid: the UUID of the project, or None if only querying by parent_node_uuid.
    @param parent_node_uuid: the UUID of the parent node. Can be the empty string if the
        node should be a top-level node in the project. Can also be None to query all nodes in a
        project. In both these cases the project UUID should be given.
    """

    if not project_uuid and not parent_node_uuid:
        raise ValueError('get_nodes(): either project_uuid or parent_node_uuid must be given.')

    where = {'properties.status': 'published'}

    # Build the parent node where-clause
    if parent_node_uuid == '':
        where['parent'] = {'$exists': False}
    elif parent_node_uuid is not None:
        where['parent'] = parent_node_uuid

    # Build the project where-clause
    if project_uuid:
        where['project'] = project_uuid

    children = pillarsdk.Node.all({
        'projection': {'name': 1, 'parent': 1, 'node_type': 1,
                       'properties.order': 1, 'properties.status': 1,
                       'properties.content_type': 1, 'picture': 1,
                       'permissions': 1, 'project': 1,  # for permission checking
                       },
        'where': where,
        'sort': 'properties.order'},
        api=pillar_api())

    return children['_items']


def fetch_texture_thumbs(parent_node_uuid: str, desired_size: str, thumbnail_directory: str):
    """Generator, fetches all texture thumbnails in a certain parent node.

    @param parent_node_uuid: the UUID of the parent node. All sub-nodes will be downloaded.
    @param desired_size: size indicator, from 'sbtmlh'.
    @param thumbnail_directory: directory in which to store the downloaded thumbnails.
    @returns: generator that yields (pillarsdk.File object, thumbnail path) tuples
    """

    api = pillar_api()

    def fetch_thumbnail_from_node(texture_node: pillarsdk.Node):
        # Fetch the File description JSON
        pic_uuid = texture_node['picture']
        file_desc = pillarsdk.File.find(pic_uuid, {
            'projection': {'filename': 1, 'variations': 1, 'width': 1, 'height': 1},
        }, api=api)

        if file_desc is None:
            print('Unable to find picture {}'.format(pic_uuid))
            return None, None

        # Save the thumbnail
        thumb_path = file_desc.stream_thumb_to_file(thumbnail_directory, desired_size, api=api)

        return file_desc, thumb_path

    texture_nodes = (node for node in get_nodes(parent_node_uuid=parent_node_uuid)
                     if node['node_type'] == 'texture')

    # # Single-threaded, not maintained:
    # for node in texture_nodes:
    #     node, file = fetch_thumbnail_from_node(node)
    #     print('Node {} has picture {}'.format(node, file))

    # Multi-threaded:
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Queue up fetching of thumbnails
        futures = [executor.submit(fetch_thumbnail_from_node, node)
                   for node in texture_nodes]

        for future in futures:
            file_desc, thumb_path = future.result()
            yield file_desc, thumb_path

    print('Done downloading texture thumbnails')


@functools.lru_cache(128)
def parent_node_uuid(node_uuid: str) -> str:
    """Returns the UUID of the node's parent node, or an empty string if this is the top level."""

    api = pillar_api()
    node = pillarsdk.Node.find(node_uuid, {'projection': {'parent': 1}}, api=api)
    if node is None:
        return ''

    print('Found node {}'.format(node))
    try:
        return node['parent']
    except KeyError:
        return ''
