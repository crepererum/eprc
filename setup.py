#!/usr/bin/env python2

"""An **experimental** approach to fix the pip dependency calculation issue."""

import os

from setuptools import find_packages, setup


# get basic description
readme = open('README.rst').read()

# get version
g = {}
with open(os.path.join('eprc', 'version.py'), 'rt') as fp:
    exec(fp.read(), g)
version = g['__version__']

# autolist all packages
packages = find_packages(exclude=['docs'])
packages.append('eprc_docs')

# ready!
setup(
    name='eprc',
    version=version,
    url='https://github.com/crepererum/eprc',
    author='Marco Neumann',
    author_email='marco@crepererum.net',
    keywords='eprc requirements calculation pip',
    license='GPLv2',
    description=__doc__,
    long_description=readme,
    platforms='any',
    zip_safe=False,
    include_package_data=True,
    packages=packages,
    package_dir={
        'eprc_docs': 'docs'
    },
    entry_points={
        'console_scripts': [
            'eprc = eprc.__main__:run'
        ]
    },
    install_requires=[
        'pip>=6.1.0',
        'pkgtools>=0.7.0',
        'redis>=2.10.0',
    ],
    extras_require={
        'docs': [
            'Sphinx>=1.3',
            'sphinx_rtd_theme>=0.1.8',
        ],
    },
)
