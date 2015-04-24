from __future__ import print_function

import distutils.core
import json
import mock
import os
import pkg_resources
import runpy
import setuptools
import sys
import types


def ensure_list(obj):
    result = []

    for pkg in pkg_resources.parse_requirements(obj):
        result.append({
            'name': pkg.key,
            'extras': [e.lower() for e in pkg.extras],
            'specs': [{'op': op, 'version': version.lower()} for op, version in pkg.specs]
        })

    return result


def ensure_dict(obj):
    result = {}

    if isinstance(obj, dict):
        for k, v in obj.iteritems():
            if isinstance(k, str):
                result[k.lower()] = ensure_list(v)

    return result


def ensure_string(obj):
    return str(obj)

def always_false(*args, **kwargs):
    return False

mock._is_magic = always_false

class SuperMockMetaMeta(mock.MagicMock):
    __metaclass__ = mock.MagicMock()

class SuperMockMeta(mock.MagicMock):
    __metaclass__ = SuperMockMetaMeta

class SuperMock(mock.MagicMock):
    __metaclass__ = SuperMockMeta


class MockedModule(types.ModuleType):
    def __init__(self, name):
        super(types.ModuleType, self).__init__(name)
        self.__name__ = super.__name__
        self.__file__ = self.__name__.replace('.', '/') + '.py'
        sys.modules[self.__name__] = self

    def __getattr__(self, key):
        obj = SuperMock
        setattr(self, key, obj)
        return obj


orig_import = __import__
def import_mock(name, *args, **kwargs):
    try:
        return orig_import(name, *args, **kwargs)
    except ImportError:
        return MockedModule(name)


def run():
    argv = ['setup.py', 'install']
    sys.path.insert(0, os.getcwd())

    with mock.patch.object(setuptools, 'setup') as mock_setuptools_setup, \
            mock.patch.object(distutils.core, 'setup') as mock_distutils_setup, \
            mock.patch.object(sys, 'argv', argv), \
            mock.patch('__builtin__.__import__', side_effect=import_mock):
        runpy.run_module('setup', run_name='__main__')

        if mock_setuptools_setup.call_args:
            args, kwargs = mock_setuptools_setup.call_args
            data = {
                'name': ensure_string(kwargs.get('name')),
                'version': ensure_string(kwargs.get('version')),
                'install_requires': ensure_list(kwargs.get('install_requires', [])),
                'extras_require': ensure_dict(kwargs.get('extras_require', {})),
                'setup_requires': ensure_list(kwargs.get('setup_requires', [])),
                'tests_require': ensure_list(kwargs.get('tests_require', []))
            }
        elif mock_distutils_setup.call_args:
            args, kwargs = mock_distutils_setup.call_args
            data = {
                'name': ensure_string(kwargs.get('name')),
                'version': ensure_string(kwargs.get('version')),
                'install_requires': ensure_list(kwargs.get('install_requires', [])),
                'extras_require': ensure_dict(kwargs.get('extras_require', {})),
                'setup_requires': ensure_list(kwargs.get('setup_requires', [])),
                'tests_require': ensure_list(kwargs.get('tests_require', []))
            }
        else:
            raise Exception("WTF?!")

    with open(os.getenv('ILLUVATAR_EXTRACT_PATH'), 'w') as outfile:
        json.dump(
            data,
            outfile,
            sort_keys=True,
            indent=4,
            separators=(',', ': ')
        )

if __name__ == '__main__':
    run()
