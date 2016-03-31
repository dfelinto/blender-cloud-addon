#!/usr/bin/env python

from distutils.command.bdist import bdist
from distutils.command.install import install
from distutils.command.install_egg_info import install_egg_info
from setuptools import setup, find_packages
from glob import glob


class BlenderAddonBdist(bdist):
    """Ensures that 'python setup.py bdist' creates a zip file."""

    def initialize_options(self):
        super().initialize_options()
        self.formats = ['zip']
        self.plat_name = 'addon'  # use this instead of 'linux-x86_64' or similar.


class BlenderAddonInstall(install):
    """Ensures the module is placed at the root of the zip file."""

    def initialize_options(self):
        super().initialize_options()
        self.prefix = ''
        self.install_lib = ''


class AvoidEggInfo(install_egg_info):
    """Makes sure the egg-info directory is NOT created.

    If we skip this, the user's addon directory will be polluted by egg-info
    directories, which Blender doesn't use anyway.
    """

    def run(self):
        pass


setup(
    cmdclass={'bdist': BlenderAddonBdist,
              'install': BlenderAddonInstall,
              'install_egg_info': AvoidEggInfo},
    name='blender_cloud',
    description='The Blender Cloud addon allows browsing the Blender Cloud from Blender.',
    version='1.0.0',
    author='Sybren A. Stüvel',
    author_email='sybren@stuvel.eu',
    packages=find_packages('.'),
    data_files=[('blender_cloud', ['README.md']),
                ('blender_cloud/wheels', glob('blender_cloud/wheels/*.whl'))],
    scripts=[],
    url='https://developer.blender.org/diffusion/BCA/',
    license='GNU General Public License v2 or later (GPLv2+)',
    platforms='',
    classifiers=[
        'Intended Audience :: End Users/Desktop',
        'Operating System :: OS Independent',
        'Environment :: Plugins',
        'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.5',
    ],
    zip_safe=False,
)
