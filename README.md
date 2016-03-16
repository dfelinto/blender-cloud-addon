Blender Cloud addon
===================

Installation requires the [Pillar SDK](https://github.com/armadillica/pillar-python-sdk)
to be installed. It can either be installed regularly somewhere on
the Python PATH, or be stored as a [wheel file](http://pythonwheels.com/)
at `blender_cloud/pillar_sdk-*.whl`.

Communication & File Structure
------------------------------

### Assumptions

* Cache is user-global, stored in an OS-specific location, and can be removed/recreated. This
  document refers to that location as `$CACHE`, and is typically a directory like
  `$HOME/.cache/blender/blender_cloud`. Also see
   [T47684](https://developer.blender.org/T47684). This directory should not be tied to the
   version of Blender -- malformed/invalid caches should just be ignored or removed.
* At the moment, versioning of files is limited to re-syncing to get the latest versions. More
  extensive versioning falls under the umbrella of "asset management" and is out of scope of
  this addon.
* Users can download texture nodes. Such a download may result in multiple files on the local
  filesystem (think of one texture node containing diffuse, bump and specular maps).

### Caching

Caching is performed at different levels:

* Caching of HTTP GET requests is performed by [Requests-Cache](https://readthedocs.org/projects/requests-cache/).
  
    * Cache is stored in `$CACHE/blender_cloud.sqlite`; by using the SQLite backend,
      we ensure cache persistence across Blender runs.
    * The code should be made cache-aware and use the Requests-Cache `CachedSession` sessions.
      This allows for more granular control over where in the code cache is (not) used.

* Downloaded thumbnails are cached (by us) in `$CACHE/thumbnails/node_uuid/filename`.

    * Use a non-cached HTTP GET to download these (using [session.cache_disabled()](http://requests-cache.readthedocs.org/en/stable/api.html#requests_cache.core.CachedSession.cache_disabled)).
    * HTTP headers are stored as JSON key/value pairs in `$CACHE/thumbnails/node_uuid/filename.headers`.
    * Use the ETag and If-Modified-Since headers to prevent unnecessary re-downloading.
    * Check Content-Length header against actual file size to detect partially-downloaded files that need re-downloading.
    
* Downloaded files (such as textures) are handled in the same way as thumbnails (described above),
  but have their metadata stored somewhere else (described below).

### Filesystem Layout

* Top-level texture directory, referred to as `$TEXTURES` in this document, can be configured per
  Blender scene. It  defaults to a global addon preference, which in turns defaults to `//textures`.
* Texture files are stored in `$TEXTURES/{project_name}/{node_name}/{node_name}/.../{file_variant}-{file_name}`.
  `file_variant` is "col" for colour/diffuse textures, "nor" for normal/bump textures, etc.
* Metadata is stored in `$TEXTURES/.blender_cloud`, with the following subdirectories:

    * `projects/{project_uuid}.json` containing the project document from Pillar
    * `nodes/{node_uuid}.json` containing the node document from Pillar
    * `files/{file_uuid}.json` containing the file document from Pillar
    * `index.sqlite` containing a mapping from `{project_name}/{node_name}/{node_name}/...`
      to the UUID of the last-named path component (project, node, or file). This allows us
      to map the path of a filesystem path to its Pillar document.

