#!/bin/bash
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

cd $(dirname $(readlink -f $0))

BCLOUD=$(ls ../dist/blender_cloud-*.addon.zip | tail -n 1)
BID=$(ls ../../../blender-id-addon/dist/blender_id-*.addon.zip | tail -n 1)

cp -va $BCLOUD $BID .

BUNDLE=$(basename $BCLOUD)
BUNDLE=${BUNDLE/.addon.zip/-bundle-UNZIP_ME_FIRST.zip}

zip -9 $BUNDLE $(basename $BCLOUD) $(basename $BID) README.txt

dolphin --select $BUNDLE 2>/dev/null >/dev/null & disown
echo "CREATED: $BUNDLE"
