import logging

import pillarsdk
from pillarsdk import exceptions as sdk_exceptions
from .pillar import pillar_call

log = logging.getLogger(__name__)
HOME_PROJECT_ENDPOINT = '/bcloud/home-project'


async def get_home_project(params=None) -> pillarsdk.Project:
    """Returns the home project."""

    log.debug('Getting home project')
    try:
        return await pillar_call(pillarsdk.Project.find_from_endpoint,
                                 HOME_PROJECT_ENDPOINT, params=params)
    except sdk_exceptions.ForbiddenAccess:
        log.warning('Access to the home project was denied. '
                    'Double-check that you are logged in with valid BlenderID credentials.')
        raise
    except sdk_exceptions.ResourceNotFound:
        log.warning('No home project available.')
        raise


async def get_home_project_id() -> str:
    """Returns just the ID of the home project."""

    home_proj = await get_home_project({'projection': {'_id': 1}})
    home_proj_id = home_proj['_id']
    return home_proj_id
