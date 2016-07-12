#!/usr/bin/env python3

import glob
import sys
import shutil
import subprocess
import re
import pathlib

from distutils import log
from distutils.core import Command
from distutils.command.bdist import bdist
from distutils.command.install import install
from distutils.command.install_egg_info import install_egg_info
from setuptools import setup, find_packages

requirement_re = re.compile('[><=]+')
sys.dont_write_bytecode = True


def set_default_path(var, default):
    """convert CLI-arguments (string) to Paths"""

    if var is None:
        return default
    return pathlib.Path(var)


# noinspection PyAttributeOutsideInit
class BuildWheels(Command):
    """Builds or downloads the dependencies as wheel files."""

    description = "builds/downloads the dependencies as wheel files"
    user_options = [
        ('wheels-path=', None, "wheel file installation path"),
        ('deps-path=', None, "path in which dependencies are built"),
        ('cachecontrol-path=', None, "subdir of deps-path containing CacheControl"),
    ]

    def initialize_options(self):
        self.wheels_path = None  # path that will contain the installed wheels.
        self.deps_path = None  # path in which dependencies are built.
        self.cachecontrol_path = None  # subdir of deps_path containing CacheControl

    def finalize_options(self):
        self.my_path = pathlib.Path(__file__).resolve().parent
        package_path = self.my_path / self.distribution.get_name()

        self.wheels_path = set_default_path(self.wheels_path, package_path / 'wheels')
        self.deps_path = set_default_path(self.deps_path, self.my_path / 'build/deps')
        self.cachecontrol_path = set_default_path(self.cachecontrol_path,
                                                  self.deps_path / 'cachecontrol')

    def run(self):
        log.info('Storing wheels in %s', self.wheels_path)

        # Parse the requirements.txt file
        requirements = {}
        with open(str(self.my_path / 'requirements.txt')) as reqfile:
            for line in reqfile.readlines():
                line = line.strip()

                if not line or line.startswith('#'):
                    # comments are lines that start with # only
                    continue

                line_req = requirement_re.split(line)
                package = line_req[0]
                version = line_req[-1]
                requirements[package] = (line, version)
                # log.info('   - %s = %s / %s', package, line, line_req[-1])

        self.wheels_path.mkdir(parents=True, exist_ok=True)

        # Download lockfile, as there is a suitable wheel on pypi.
        if not list(self.wheels_path.glob('lockfile*.whl')):
            log.info('Downloading lockfile wheel')
            self.download_wheel(requirements['lockfile'])

        # Download Pillar Python SDK from pypi.
        if not list(self.wheels_path.glob('pillarsdk*.whl')):
            log.info('Downloading Pillar Python SDK wheel')
            self.download_wheel(requirements['pillarsdk'])

        # Build CacheControl.
        if not list(self.wheels_path.glob('CacheControl*.whl')):
            log.info('Building CacheControl in %s', self.cachecontrol_path)
            # self.git_clone(self.cachecontrol_path,
            #                'https://github.com/ionrock/cachecontrol.git',
            #                'v%s' % requirements['CacheControl'][1])
            # FIXME: we need my clone until pull request #125 has been merged & released
            self.git_clone(self.cachecontrol_path,
                           'https://github.com/sybrenstuvel/cachecontrol.git',
                           'sybren-filecache-delete-crash-fix')
            self.build_copy_wheel(self.cachecontrol_path)

        # Ensure that the wheels are added to the data files.
        self.distribution.data_files.append(
            ('blender_cloud/wheels', (str(p) for p in self.wheels_path.glob('*.whl')))
        )

    def download_wheel(self, requirement):
        """Downloads a wheel from PyPI and saves it in self.wheels_path."""

        subprocess.check_call([
            'pip', 'download',
            '--no-deps',
            '--dest', str(self.wheels_path),
            requirement[0]
        ])

    def git_clone(self, workdir: pathlib.Path, git_url: str, checkout: str = None):
        if workdir.exists():
            # Directory exists, expect it to be set up correctly.
            return

        workdir.mkdir(parents=True)

        subprocess.check_call(['git', 'clone', git_url, str(workdir)],
                              cwd=str(workdir.parent))

        if checkout:
            subprocess.check_call(['git', 'checkout', checkout],
                                  cwd=str(workdir))

    def build_copy_wheel(self, package_path: pathlib.Path):
        # Make sure no wheels exist yet, so that we know which one to copy later.
        to_remove = list((package_path / 'dist').glob('*.whl'))
        for fname in to_remove:
            fname.unlink()

        subprocess.check_call([sys.executable, 'setup.py', 'bdist_wheel'],
                              cwd=str(package_path))

        wheel = next((package_path / 'dist').glob('*.whl'))
        log.info('copying %s to %s', wheel, self.wheels_path)
        shutil.copy(str(wheel), str(self.wheels_path))


# noinspection PyAttributeOutsideInit
class BlenderAddonBdist(bdist):
    """Ensures that 'python setup.py bdist' creates a zip file."""

    def initialize_options(self):
        super().initialize_options()
        self.formats = ['zip']
        self.plat_name = 'addon'  # use this instead of 'linux-x86_64' or similar.

    def run(self):
        self.run_command('wheels')
        super().run()


# noinspection PyAttributeOutsideInit
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
              'install_egg_info': AvoidEggInfo,
              'wheels': BuildWheels},
    name='blender_cloud',
    description='The Blender Cloud addon allows browsing the Blender Cloud from Blender.',
    version='1.3.0',
    author='Sybren A. Stüvel',
    author_email='sybren@stuvel.eu',
    packages=find_packages('.'),
    data_files=[('blender_cloud', ['README.md']),
                ('blender_cloud/icons', glob.glob('blender_cloud/icons/*'))],
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
