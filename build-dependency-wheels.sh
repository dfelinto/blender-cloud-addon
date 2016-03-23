#!/bin/bash

MYDIR=$(dirname $(readlink -f $0))
WHEELS=$MYDIR/blender_cloud/wheels
cd $MYDIR

PILLAR_SDK_DIR=$MYDIR/../pillar-python-sdk
CACHECONTROL_DIR=$MYDIR/../cachecontrol

# Build the Pillar Python SDK wheel from ../pillar-python-sdk
if [ ! -e $WHEELS/lockfile*.whl ]; then
    echo "Building pillar_sdk wheel"
    if [ ! -e $PILLAR_SDK_DIR ]; then
        cd $(dirname $PILLAR_SDK_DIR)
        git clone https://github.com/armadillica/pillar-python-sdk.git $PILLAR_SDK_DIR
    fi

    cd $PILLAR_SDK_DIR
    python setup.py bdist_wheel
    cp $(ls dist/*.whl -rt | tail -n 1) $WHEELS
fi

# Download lockfile wheel
if [ ! -e $WHEELS/lockfile*.whl ]; then
    echo "Downloading lockfile"
    pip download --dest $WHEELS $(grep -i lockfile $MYDIR/requirements.txt)
fi

# Build CacheControl wheel
if [ ! -e $WHEELS/CacheControl*.whl ]; then
    echo "Building CacheControl wheel"
    if [ ! -e $CACHECONTROL_DIR ]; then
        cd $(dirname $CACHECONTROL_DIR)
        git clone https://github.com/ionrock/cachecontrol.git $CACHECONTROL_DIR
        cd $CACHECONTROL_DIR
        git checkout v0.11.6  # TODO: get from requirements.txt
    fi

    cd $CACHECONTROL_DIR
    rm -f dist/*.whl
    python setup.py bdist_wheel
    cp $(ls dist/*.whl -rt | tail -n 1) $WHEELS
fi

