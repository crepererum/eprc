import itertools
import logging
import urllib2

import utils

class Scheduler(object):
    def __init__(self, db, extractor, pypi, verbosity=1):
        self.db = db
        self.extractor = extractor
        self.pypi = pypi
        self.done = set()
        self.todo = set()
        self.blacklist = set()
        self.report_counter = 0
        self.verbosity = verbosity

    def __str__(self):
        return "Scheduler done={} todo={} blacklisted={}".format(
            len(self.done),
            len(self.todo),
            len(self.blacklist)
        )

    def get(self):
        entry = None
        while self.todo and not entry:
            candidate = self.todo.pop()
            if candidate not in self.done:
                entry = candidate

        self.report_counter += 1
        if self.report_counter >= self.verbosity:
            self.report_counter = 0
            logging.info(str(self))

        return entry

    def add_todos_from_db(self, name, version, extra=''):
        def add_to_todo(pkg):
            for extra_wish in itertools.chain([''], pkg['extras']):
                candidate = (utils.normalize(pkg['name']), utils.normalize(extra_wish))
                if candidate not in self.done:
                    self.todo.add(candidate)

        data = self.db.get(name, version)

        # always add the defaults (without extras)
        for pkg in itertools.chain(
                data['setup_requires'],
                data['install_requires'],
                data['tests_require']):
            add_to_todo(pkg)

        if extra:
            for pkg in data['extras_require'].get(extra, []):
                add_to_todo(pkg)

    def done_with_all_versions(self, name, extra):
        self.done.add((utils.normalize(name), utils.normalize(extra)))

    def blacklist_version(self, name, version):
        self.blacklist.add((utils.normalize(name), utils.normalize(version)))

    def is_version_blacklisted(self, name, version):
        return (utils.normalize(name), utils.normalize(version)) in self.blacklist

    def process_cached(self, name, extra):
        all_versions = self.db.all_versions(name)
        if not all_versions:
            logging.warn("No versions found for {}".format(name))

        for version in all_versions:
            self.add_todos_from_db(name, version, extra)

        self.done_with_all_versions(name, extra)

    def process_extract(self, name, extra):
        native_result = self.extractor.from_native(self.db, name)

        try:
            name = self.pypi.real_name(name)
        except urllib2.HTTPError:
            logging.warning("PyPi error for {}".format(name))
            return

        versions = self.pypi.package_releases(name)
        if not versions and not native_result:
            logging.warn("No versions found for {}".format(name))
            return

        for version in versions:
            data = self.db.get(name, version)
            if data:
                logging.info("Cached {}:{}".format(utils.normalize(name), utils.normalize(version)))
            elif self.is_version_blacklisted(name, version):
                logging.info("Blacklisted {}:{}".format(name, version))
            else:
                try:
                    logging.info(
                        "Fetching {}:{}".format(
                            utils.normalize(name),
                            utils.normalize(version)
                        )
                    )
                    data = self.extractor.from_pypi(self.db, name, version)

                    # did we get something useful?
                    if not data:
                        self.blacklist_version(name, version)
                except Exception as e:
                    logging.warn(
                        "Unhandled exception while processing {}:{} - {}".format(
                            name,
                            version,
                            e
                        )
                    )
                    self.blacklist_version(name, version)

            # register
            data = self.db.get(name, version)
            if data:
                self.add_todos_from_db(data['name'], data['version'], extra)

        self.done_with_all_versions(name, extra)
