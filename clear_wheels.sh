#!/bin/bash

git clean -n -d -X blender_cloud/wheels/

echo "Press [ENTER] to actually delete those files."
read dummy

git clean -f -d -X blender_cloud/wheels/
