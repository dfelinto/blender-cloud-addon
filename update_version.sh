#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: $0 new-version" >&2
    exit 1
fi

BL_INFO_VER=$(echo "$1" | sed 's/\./, /g')

sed "s/version='[^']*'/version='$1'/" -i setup.py
sed "s/'version': ([^)]*)/'version': ($BL_INFO_VER)/" -i blender_cloud/__init__.py

git diff
echo
echo "Don't forget to commit!"
