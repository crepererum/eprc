import argparse
import contextlib
import itertools
import json
import logging
import os
import os.path
import pkg_resources
import pkgtools.pypi
import re
import redis
import shutil
import subprocess
import tarfile
import tempfile
import urllib2
import zipfile


@contextlib.contextmanager
def TemporaryDirectory():
    name = tempfile.mkdtemp()
    try:
        yield name
    finally:
        shutil.rmtree(name)


def normalize(string):
    string = string.strip()\
        .lower()\
        .replace("_", "-")

    return re.sub("[^a-z0-9.-]", "", string)


def real_name(package_name, timeout=None):
    """Replaces buggy pkgtools.pypi.real_name."""
    r = urllib2.Request('http://pypi.python.org/pypi/{0}'.format(package_name))
    return urllib2.urlopen(r, timeout=timeout).geturl().split('/')[-1]


class HandledError(Exception):
    def __init__(self, msg, *args, **kwargs):
        super(Exception, self).__init__()
        self.message = msg.format(*args, **kwargs)


class Database(object):
    def __init__(self, host, port, db):
        self.redis = redis.StrictRedis(host=host, port=port, db=db)

    @staticmethod
    def name_version_to_key(name, version):
        return "{}:{}".format(normalize(name), normalize(version))

    def set(self, name, version, data):
        self.redis.set(
            self.name_version_to_key(name, version),
            json.dumps(data)
        )

    def get(self, name, version):
        string = self.redis.get(self.name_version_to_key(name, version))
        if string:
            return json.loads(string)
        else:
            return None

    def all_versions(self, name):
        return [
            key.split(":")[1]
            for key in self.redis.keys("{}:*".format(normalize(name)))
        ]


class Extractor(object):
    def __init__(
            self,
            virtualenv,
            tmpdir,
            pypi,
            extractors_path=os.path.abspath(
                os.path.join(os.path.dirname(__file__), 'extractors')
            )
            ):
        self.extractor_setup_py = os.path.join(extractors_path, "setup_py.py")
        self.extractor_bundled = os.path.join(extractors_path, "bundled.py")
        self.virtualenv = virtualenv
        self.tmpdir = tmpdir
        self.pypi = pypi

    def _run_extractor(
            self,
            pyfile,
            args=None,
            cwd=None,
            env=None,
            packages=None):
        if not env:
            env = os.environ.copy()
        extract_path = os.path.join(self.tmpdir, "extractor_result.json")
        env['ILLUVATAR_EXTRACT_PATH'] = extract_path

        # FIXME do not create a new venv all the time
        # ideas:
        #  - overlay fs
        #  - clone (copy does not work, use virtualenv-clone)
        #  - copy + `virtualenv --relocatable ENV`
        #    (see https://pypi.python.org/pypi/virtualenv/1.3.1#making-environments-relocatable)
        with open(os.devnull, "w") as fnull:
            venvdir = os.path.join(self.tmpdir, "venv")
            subprocess.check_call(
                [self.virtualenv, venvdir],
                stdout=fnull
            )
            pip = os.path.join(venvdir, "bin", "pip")

            if packages:
                args = [pip, "install"]
                args.extend(packages)
                subprocess.check_call(
                    args,
                    stdout=fnull
                )

        try:
            python = os.path.join(venvdir, "bin", "python")

            what_to_call = [python, os.path.abspath(pyfile)]
            if args:
                what_to_call.extend(args)

            subprocess.check_call(
                what_to_call,
                cwd=cwd,
                env=env
            )
            shutil.rmtree(venvdir)

            with open(extract_path, 'r') as infile:
                data = json.load(infile)
            os.remove(extract_path)

            return data
        except subprocess.CalledProcessError:
            return None

    def from_path(self, path, db, name=None, version=None):
        logging.debug("Extract from '{}'".format(path))

        # fire up setup_py.py
        data = self._run_extractor(
            pyfile=self.extractor_setup_py,
            cwd=path,
            packages=["mock"]
        )

        if data:
            # try to fix some weird cases (e.g. numpy)
            if name and data['name'] == 'None':
                data['name'] = name
            if version and data['version'] == 'None':
                data['version'] = version

            # some packages are messed up
            if name and normalize(name) != normalize(data['name']):
                logging.warn(
                    "Package '{}':'{}' gives wrong name '{}'".format(
                        name,
                        version,
                        data['name']
                    )
                )
                data['name'] = name
            if name \
                    and version \
                    and normalize(version) != normalize(data['version']):
                logging.warn(
                    "Package '{}':'{}' gives wrong version '{}'".format(
                        name,
                        version,
                        data['version']
                    )
                )
                data['version'] = version

            db.set(data['name'], data['version'], data)
            return data
        else:
            return None

    def from_pypi(self, db, name, version):
        name = real_name(name)

        # find source package
        url = None
        for entry in self.pypi.release_urls(name, version):
            if entry['packagetype'] == 'sdist':
                url = entry['url']
        if not url:
            logging.warn("No source URL found for {}:{}".format(name, version))
            return None

        # download source package
        archive_path = os.path.join(self.tmpdir, os.path.basename(url))
        fp = urllib2.urlopen(url)
        with open(archive_path, "wb") as archive_file:
            archive_file.write(fp.read())

        # extract archive
        # FIXME be smarter and more secure about extraction
        #       (paths, permissions, ...)
        extracted_path = archive_path + ".extracted"
        if archive_path.endswith("zip"):
            with zipfile.ZipFile(archive_path, "r") as archive_file:
                archive_file.extractall(extracted_path)
        else:
            with tarfile.open(archive_path, "r:gz") as archive_file:
                archive_file.extractall(extracted_path)
        os.remove(archive_path)

        # extract dependency information
        # FIXME be smarter about finding setup.py
        target_path = os.path.join(
            extracted_path,
            os.listdir(extracted_path)[0]
        )
        data = self.from_path(
            target_path,
            db,
            normalize(name),
            normalize(version)
        )
        shutil.rmtree(extracted_path)

        return data

    def from_native(self, db, name):
        try:
            # only try to extract it if module exist
            __import__(normalize(name))
            data_simple = self._run_extractor(
                pyfile=self.extractor_bundled,
                args=[normalize(name)]
            )
            if data_simple:
                data = {
                    'name': normalize(data_simple['name']),
                    'version': normalize(data_simple['version']),
                    'setup_requires': [],
                    'install_requires': [],
                    'tests_require': [],
                    'extras_require': {}
                }
                db.set(data['name'], data['version'], data)
                return data
            else:
                return None
        except ImportError:
            return None


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
                candidate = (normalize(pkg['name']), normalize(extra_wish))
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
        self.done.add((normalize(name), normalize(extra)))

    def blacklist_version(self, name, version):
        self.blacklist.add((normalize(name), normalize(version)))

    def is_version_blacklisted(self, name, version):
        return (normalize(name), normalize(version)) in self.blacklist

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
            name = real_name(name)
        except urllib2.HTTPError:
            logging.warning("PyPi error for {}".format(name))
            return

        versions = self.pypi.package_releases(name, show_hidden=True)
        if not versions and not native_result:
            logging.warn("No versions found for {}".format(name))
            return

        for version in versions:
            data = self.db.get(name, version)
            if data:
                logging.info("Cached {}:{}".format(normalize(name), normalize(version)))
            elif self.is_version_blacklisted(name, version):
                logging.info("Blacklisted {}:{}".format(name, version))
            else:
                try:
                    logging.info(
                        "Fetching {}:{}".format(
                            normalize(name),
                            normalize(version)
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


class VariableRegister(object):
    VIRTUAL_VERSION = pkg_resources.parse_version("virtual")

    def __init__(self):
        self.map_set = {}            # (name, set(version), extra) -> variable
        self.map_set_rev = {}            # variable -> (name, set(version), extra)
        self.map_single = {}             # (name, version, extra) -> variable
        self.map_single_rev = {}         # variable -> (name, version, extra)
        self.versions_register = {}  # name -> set(set(version))
        self.count = 1

    def register_set(self, name, versions, extras):
        for e in extras:
            key = (name, frozenset(versions), e)
            if key in self.map_set:
                raise Error("Unreachable!")
            else:
                variable = self.count
                self.count += 1
                self.map_set[key] = variable
                self.map_set_rev[variable] = key

    def register_single(self, name, version, extras):
        if name not in self.versions_register:
            self.versions_register[name] = set()
        self.versions_register[name].add(version)

        for e in extras:
            key = (name, version, e)
            if key in self.map_single:
                raise Error("Unreachable!")
            else:
                variable = self.count
                self.count += 1
                self.map_single[key] = variable
                self.map_single_rev[variable] = key

    def get_virtual_variable(self):
        variable = self.count
        self.count += 1
        return variable


def solve(scheduler, db, must_satisfy, tmpdir, solver, outfile):
    register = VariableRegister()

    # get all names and known extras
    name_extras = dict()
    for name, extra in scheduler.done:
        if name not in name_extras:
            name_extras[name] = set()
        name_extras[name].add(extra)

    for name in name_extras.iterkeys():
        name_extras[name].add("")

    # register all names
    # also compress single versions to set of versions if the
    # requirements are identical
    # FIXME separate extras from core
    for name in name_extras.iterkeys():
        all_versions = [
            pkg_resources.parse_version(version)
            for version in db.all_versions(name)
        ]
        if not all_versions:
            logging.warn("Create virtual version for {}".format(name))
            all_versions = [VariableRegister.VIRTUAL_VERSION]

        aliases = {}
        for version in all_versions:
            data = db.get(name, str(version))
            normalized = json.dumps(data, sort_keys=True)
            if normalized not in aliases:
                aliases[normalized] = set()
            aliases[normalized].add(version)
            register.register_single(name, version, name_extras[name])

        for data_json, versions in aliases.iteritems():
            register.register_set(name, versions, name_extras[name])

    opb_optimization = []
    opb_clauses = []

    # clauses for requirements
    for (name, versions, extra), variable in register.map_set.iteritems():
        data = db.get(name, str(iter(versions).next()))
        if not data:
            continue

        if extra:
            requirement_iter = data['extras_require'].get(extra, [])
        else:
            requirement_iter = itertools.chain(data['install_requires'], data['tests_require'], data['setup_requires'])

        # create representation variable for the entire set of versions and link it
        # (e.g. at least one version variable is true => set variable must be true)
        #     (V1 v v2 v ... v VN => SET)
        #     <=> ((V1 v V2 v .. v VN) v -SET)
        set_variable = register.get_virtual_variable()
        setlink_clause = ""
        for version in versions:
            variable = register.map_single[(name, version, extra)]
            setlink_clause += "-1 x{}  ".format(variable)
        setlink_clause += "{} x{}  >=  0;".format(len(versions), set_variable)
        opb_clauses.append(setlink_clause)

        for requ_data in requirement_iter:
            # build requirement object from requ_data
            # official version:
            #
            #     requirement_string = "{}".format(requ_data['name'])
            #     if requ_data['specs']:
            #         requirement_string += ','.join("{}{}".format(spec["op"], spec["version"]) for spec in requ_data['specs'])
            #     requirement = pkg_resources.Requirement.parse(requirement_string)
            #
            # but that is too slow, so use the undocumented API
            requirement = pkg_resources.Requirement(
                requ_data['name'],
                [(spec['op'], spec['version']) for spec in requ_data['specs']],
                requ_data['extras']
            )


            # create virtual variable for that requirement
            # and make the set variable require this virtual variable
            virtual_variable = register.get_virtual_variable()
            opb_clauses.append("-1 x{}  1 x{}  >=  0;".format(set_variable, virtual_variable))

            # check all known versions against this requirement
            # and put them in a possible set of satisfiying variable for the virtual object
            # `VIRT => V1 v V2 v ... v VN`
            or_clause = "-1 x{}".format(virtual_variable)
            requ_versions = register.versions_register.get(requ_data['name'], set())
            if not requ_versions:
                # oops, we can never satisfy this
                # opb_clauses.append("-1 x{}  >=  1;".format(variable))
                pass # DEBUG
            for requ_version in requ_versions:
                if (requ_version == VariableRegister.VIRTUAL_VERSION) or (requ_version in requirement):
                    # add constraint for base + all requested extras
                    for requ_extra in itertools.chain([''], requ_data['extras']):
                        requ_variable = register.map_single[(requ_data['name'], requ_version, requ_extra)]
                        or_clause += "  1 x{}".format(requ_variable)

            # finish the or-clause and push it
            or_clause += "  >=  0;"
            opb_clauses.append(or_clause)

    # clauses for general information of packages
    for name, versions in register.versions_register.iteritems():
        # maximum one version
        opb_clauses.append(
            "  ".join(
                "-1 x{}".format(register.map_single[name, version, ''])
                for version in versions
            ) + "  >=  -1;"
        )

        # extras require base
        for version in versions:
            variable_base = register.map_single[name, version, '']
            for extra in name_extras[name]:
                if extra:
                    variable_extra = register.map_single[name, version, extra]
                    opb_clauses.append("-1 x{}  1 x{}  >= 0;".format(variable_extra, variable_base))

        # order versions by history for optimization
        # FIXME add ability to require minimal version
        # FIXME implement better weights for versions
        #       (e.g. 0.1.0, 0.1.1, 0.2.0)
        for weight, version in enumerate(sorted(versions, reverse=True)):
            opb_optimization.append("{} x{}".format(weight, register.map_single[name, version, '']))

    # initial starting point
    for name, version in must_satisfy:
        variable = register.map_single[(name, pkg_resources.parse_version(version), '')]
        opb_clauses.append("1 x{}  >=  1;".format(variable))

    # write opb file
    opb_filepath = os.path.join(tmpdir, "to_solve.opb")
    with open(opb_filepath, "w") as opb_file:
        opb_file.write("* #variable= {} #constraint= {}\n".format(register.count, len(opb_clauses)))
        opb_file.write("min: ")
        for x in opb_optimization:
            opb_file.write(x)
            opb_file.write(" ")
        opb_file.write(";\n")

        for clause in opb_clauses:
            opb_file.write(clause)
            opb_file.write("\n")

    logging.info("#Variables = {}   #Constraints= {}".format(register.count, len(opb_clauses)))
    # run solver
    result_path = os.path.join(tmpdir, "result.txt")
    subprocess.check_call(
        "{} {} | tee {}".format(solver, opb_filepath, result_path),
        shell=True
    )

    # analyze result
    result_status = None
    result_result = None
    with open(result_path) as result_file:
        for line in result_file:
            line = line.strip()
            if line.startswith("s"):
                result_status = line[2:]
            elif line.startswith("v"):
                result_result = line[2:]
    if result_status == "OPTIMUM FOUND":
        packages = {}
        for part in result_result.split(" "):
            # only looking for true assigments
            if part.startswith("x"):
                variable = int(part[1:])
                if variable in register.map_single_rev:
                    name, version, extra = register.map_single_rev[variable]
                    if (name, version) not in packages:
                        packages[(name, version)] = set()
                    packages[(name, version)].add(extra)

        with open(outfile, "w") as outfile:
            for (name, version), extras in sorted(
                    packages.iteritems(),
                    key=lambda ((name, _version), _extras): name):
                requirement_string = "{}".format(name)

                if version != VariableRegister.VIRTUAL_VERSION:
                    requirement_string += "=={}".format(version)

                extras.remove("")
                if extras:
                    requirement_string += "[" + ",".join(sorted(extras)) + "]"

                outfile.write(requirement_string)
                outfile.write("\n")

        logging.info("Wrote requirements to {}".format(outfile))
    else:
        logging.error("Cannot find a solution")


def parse_args():
    parser = argparse.ArgumentParser(
        prog='eprc',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "-e", "--virtualenv",
        type=str,
        default="virtualenv2"
    )

    parser.add_argument(
        'paths',
        nargs='+',
        type=str
    )

    parser.add_argument(
        "-c", "--cached",
        action="store_true",
        default=False
    )

    parser.add_argument(
        "-s", "--solver",
        type=str,
        default="java -jar sat4j-pb.jar"
    )

    parser.add_argument(
        "-o", "--outfile",
        type=str,
        default="requirements.txt"
    )

    return parser.parse_args()


def run():
    try:
        args = parse_args()

        with TemporaryDirectory() as tmpdir:
            logging.getLogger().setLevel(logging.INFO)
            logging.basicConfig(
                format="%(asctime)s [%(levelname)s]: %(message)s"
            )
            pypi = pkgtools.pypi.PyPIXmlRpc()
            extractor = Extractor(
                virtualenv=args.virtualenv,
                tmpdir=tmpdir,
                pypi=pypi
            )
            db = Database(
                host="localhost",
                port=6378,
                db=0
            )
            scheduler = Scheduler(
                db=db,
                extractor=extractor,
                pypi=pypi
            )

            # start with given paths
            # also remember what we have got here,
            # because it is important for the PBO part later
            must_satisfy = []
            for p in args.paths:
                splitted = p.split(':')
                cwd = splitted[0]
                if len(splitted) > 1:
                    extras = splitted[1].split(',')
                else:
                    extras = []

                data = extractor.from_path(cwd, db)

                must_satisfy.append(
                    (
                        normalize(data['name']),
                        normalize(data['version'])
                    )
                )
                scheduler.add_todos_from_db(data['name'], data['version'], '')
                scheduler.done_with_all_versions(data['name'], '')
                for e in itertools.chain([''], extras):
                    scheduler.add_todos_from_db(
                        data['name'],
                        data['version'],
                        e
                    )
                    scheduler.done_with_all_versions(data['name'], e)

            # run until no tasks left
            todo = scheduler.get()
            while todo:
                (name, extra) = todo

                if args.cached:
                    scheduler.process_cached(name, extra)
                else:
                    scheduler.process_extract(name, extra)
                todo = scheduler.get()

            # finally solve our problem
            solve(scheduler, db, must_satisfy, tmpdir, args.solver, args.outfile)


    except HandledError as e:
        logging.error(e.message)

if __name__ == '__main__':
    run()
