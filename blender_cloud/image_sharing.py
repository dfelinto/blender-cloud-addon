import logging
import os.path
import tempfile

import bpy
import pillarsdk
from pillarsdk import exceptions as sdk_exceptions
from .pillar import pillar_call
from . import async_loop, pillar, home_project
from .blender import PILLAR_WEB_SERVER_URL

REQUIRES_ROLES_FOR_IMAGE_SHARING = {'subscriber', 'demo'}
IMAGE_SHARING_GROUP_NODE_NAME = 'Image sharing'
log = logging.getLogger(__name__)


async def find_image_sharing_group_id(home_project_id, user_id):
    # Find the top-level image sharing group node.
    try:
        share_group, created = await pillar.find_or_create_node(
            where={'project': home_project_id,
                   'node_type': 'group',
                   'parent': None,
                   'name': IMAGE_SHARING_GROUP_NODE_NAME},
            additional_create_props={
                'user': user_id,
                'properties': {},
            },
            projection={'_id': 1},
            may_create=True)
    except pillar.PillarError:
        log.exception('Pillar error caught')
        raise pillar.PillarError('Unable to find image sharing folder on the Cloud')

    return share_group['_id']


class PILLAR_OT_image_share(pillar.PillarOperatorMixin,
                            async_loop.AsyncModalOperatorMixin,
                            bpy.types.Operator):
    bl_idname = 'pillar.image_share'
    bl_label = 'Share an image via Blender Cloud'
    bl_description = 'Uploads an image for sharing via Blender Cloud'

    log = logging.getLogger('bpy.ops.%s' % bl_idname)

    home_project_id = None
    share_group_id = None  # top-level share group node ID
    user_id = None

    target = bpy.props.EnumProperty(
        items=[
            ('FILE', 'File', 'Upload an image file'),
            ('DATABLOCK', 'Datablock', 'Upload an image datablock'),
        ],
        name='target')

    name = bpy.props.StringProperty(name='name',
                                    description='File or datablock name to sync')

    def invoke(self, context, event):
        # Do a quick test on datablock dirtyness. If it's not packed and dirty,
        # the user should save it first.
        if self.target == 'DATABLOCK':
            datablock = bpy.data.images[self.name]
            if datablock.type == 'IMAGE' and datablock.is_dirty and not datablock.packed_file:
                self.report({'ERROR'}, 'Datablock is dirty, save it first.')
                return {'CANCELLED'}

        async_loop.AsyncModalOperatorMixin.invoke(self, context, event)

        self.log.info('Starting sharing')
        self._new_async_task(self.async_execute(context))
        return {'RUNNING_MODAL'}

    async def async_execute(self, context):
        """Entry point of the asynchronous operator."""

        self.report({'INFO'}, 'Communicating with Blender Cloud')

        try:
            # Refresh credentials
            try:
                self.user_id = await self.check_credentials(context,
                                                            REQUIRES_ROLES_FOR_IMAGE_SHARING)
                self.log.debug('Found user ID: %s', self.user_id)
            except pillar.NotSubscribedToCloudError:
                self.log.exception('User not subscribed to cloud.')
                self.report({'ERROR'}, 'Please subscribe to the Blender Cloud.')
                self._state = 'QUIT'
                return
            except pillar.CredentialsNotSyncedError:
                self.log.exception('Error checking/refreshing credentials.')
                self.report({'ERROR'}, 'Please log in on Blender ID first.')
                self._state = 'QUIT'
                return

            # Find the home project.
            try:
                self.home_project_id = await home_project.get_home_project_id()
            except sdk_exceptions.ForbiddenAccess:
                self.log.exception('Forbidden access to home project.')
                self.report({'ERROR'}, 'Did not get access to home project.')
                self._state = 'QUIT'
                return
            except sdk_exceptions.ResourceNotFound:
                self.report({'ERROR'}, 'Home project not found.')
                self._state = 'QUIT'
                return

            try:
                gid = await find_image_sharing_group_id(self.home_project_id,
                                                        self.user_id)
                self.share_group_id = gid
                self.log.debug('Found group node ID: %s', self.share_group_id)
            except sdk_exceptions.ForbiddenAccess:
                self.log.exception('Unable to find Group ID')
                self.report({'ERROR'}, 'Unable to find sync folder.')
                self._state = 'QUIT'
                return

            await self.share_image(context)
        except Exception as ex:
            self.log.exception('Unexpected exception caught.')
            self.report({'ERROR'}, 'Unexpected error: %s' % ex)

        self._state = 'QUIT'

    async def share_image(self, context):
        """Sends files to the Pillar server."""

        self.report({'INFO'}, 'Uploading %s %s' % (self.target.lower(), self.name))
        if self.target == 'FILE':
            await self.upload_file(self.name)
        else:
            await self.upload_datablock(context)

    async def upload_file(self, filename: str):
        """Uploads a file to the cloud, attached to the image sharing node."""

        self.log.info('Uploading file %s', filename)
        node = await pillar_call(pillarsdk.Node.create_asset_from_file,
                                 self.home_project_id,
                                 self.share_group_id,
                                 'image',
                                 filename,
                                 extra_where={'user': self.user_id})
        self.log.info('Created node %s', node['_id'])
        self.report({'INFO'}, 'File succesfully uploaded to the cloud!')

        import webbrowser
        import urllib.parse
        url = urllib.parse.urljoin(PILLAR_WEB_SERVER_URL, '/p/p-home/%s' % node['_id'])
        self.log.info('Opening browser at %s', url)
        webbrowser.open_new_tab(url)

    async def upload_datablock(self, context):
        """Saves a datablock to file if necessary, then upload."""

        self.log.info("Uploading datablock '%s'" % self.name)
        datablock = bpy.data.images[self.name]

        if datablock.type == 'RENDER_RESULT':
            # Construct a sensible name for this render.
            filename = '%s-%s-render%s' % (
                os.path.splitext(os.path.basename(context.blend_data.filepath))[0],
                context.scene.name,
                context.scene.render.file_extension)
            await self.upload_via_tempdir(datablock, filename)
            return

        if datablock.is_dirty:
            # We can handle dirty datablocks like this if we want.
            # However, I (Sybren) do NOT think it's a good idea to:
            # - Share unsaved data to the cloud; users can assume it's saved
            #   to disk and close blender, losing their file.
            # - Save unsaved data first; this can overwrite a file a user
            #   didn't want to overwrite.
            filename = bpy.path.basename(datablock.filepath)
            await self.upload_via_tempdir(datablock, filename)
            return

        if datablock.packed_file is not None:
            # TODO: support packed files.
            self.report({'ERROR'}, 'Packed files are not supported yet.')
            return

        filepath = bpy.path.abspath(datablock.filepath)
        await self.upload_file(filepath)

    async def upload_via_tempdir(self, datablock, filename_on_cloud):
        """Saves the datablock to file, and uploads it to the cloud.

        Saving is done to a temporary directory, which is removed afterwards.
        """

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, filename_on_cloud)
            self.log.debug('Saving %s to %s', datablock, filepath)
            datablock.save_render(filepath)
            await self.upload_file(filepath)


def image_editor_menu(self, context):
    image = context.space_data.image

    box = self.layout.row()
    if image and image.has_data:
        text = 'Share on Blender Cloud'
        if image.type == 'IMAGE' and image.is_dirty and not image.packed_file:
            box.enabled = False
            text = 'Save image before sharing on Blender Cloud'

        props = box.operator(PILLAR_OT_image_share.bl_idname, text=text)
        props.target = 'DATABLOCK'
        props.name = image.name


def register():
    bpy.utils.register_class(PILLAR_OT_image_share)

    bpy.types.IMAGE_HT_header.append(image_editor_menu)


def unregister():
    bpy.utils.unregister_class(PILLAR_OT_image_share)

    bpy.types.IMAGE_HT_header.remove(image_editor_menu)
