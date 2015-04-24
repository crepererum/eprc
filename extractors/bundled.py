import json
import os
import pip
import re
import sys


def normalize(string):
    string = string.strip()\
        .lower()\
        .replace("_", "-")

    return re.sub("[^a-z0-9.-]", "", string)


def run():
    installed = set(
        normalize(pkg.key)
        for pkg in pip.get_installed_distributions()
    )
    name = normalize(sys.argv[1])

    if name in installed:
        sys.exit(1)

    try:
        module = __import__(name)
        if hasattr(module, "__version__"):
            data = {
                'name': normalize(name),
                'version': normalize(getattr(module, "__version__")),
                'setup_requires': [],
                'install_requires': [],
                'tests_require': [],
                'extras_require': {}
            }
            with open(os.getenv('ILLUVATAR_EXTRACT_PATH'), 'w') as outfile:
                json.dump(
                    data,
                    outfile,
                    sort_keys=True,
                    indent=4,
                    separators=(',', ': ')
                )
        else:
            sys.exit(1)
    except ImportError:
        sys.exit(1)


if __name__ == '__main__':
    run()
