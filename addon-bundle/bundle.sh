#!/bin/bash

cd $(dirname $(readlink -f $0))

BCLOUD=$(ls ../dist/blender_cloud-*.addon.zip | tail -n 1)
BID=$(ls ../../../blender-id-addon/dist/blender_id-*.addon.zip | tail -n 1)

cp -va $BCLOUD $BID .

BUNDLE=$(basename $BCLOUD)
BUNDLE=${BUNDLE/.addon.zip/-bundle-UNZIP_ME_FIRST.zip}

zip -9 $BUNDLE $(basename $BCLOUD) $(basename $BID) README.txt

dolphin --select $BUNDLE 2>/dev/null >/dev/null & disown
echo "CREATED: $BUNDLE"
