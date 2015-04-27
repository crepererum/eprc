import itertools
import json
import logging
import os.path
import pkg_resources
import subprocess


class VariableRegister(object):
    VIRTUAL_VERSION = pkg_resources.parse_version("virtual")

    def __init__(self):
        self.map_set = {}            # (name, set(version), extra) -> variable
        self.map_set_rev = {}        # variable -> (name, set(version), extra)
        self.map_single = {}         # (name, version, extra) -> variable
        self.map_single_rev = {}     # variable -> (name, version, extra)
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


def solve(scheduler, db, must_satisfy, tmpdir, solver, outpath):
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

        with open(outpath, "w") as outfile:
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

        logging.info("Wrote requirements to {}".format(outpath))
    else:
        logging.error("Cannot find a solution")
