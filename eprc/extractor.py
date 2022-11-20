import json
import logging
import os.path
import pkg_resources
import shutil
import subprocess
import tarfile
import urllib2
import zipfile

import utils


class Extractor(object):
    def __init__(
            self,
            virtualenv,
            tmpdir,
            pypi,
            extractors_path=pkg_resources.resource_filename(
                __name__,
                "extractors"
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
            if name and utils.normalize(name) != utils.normalize(data['name']):
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
                    and utils.normalize(version) != utils.normalize(data['version']):
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
        name = self.pypi.real_name(name)

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
                def is_within_directory(directory, target):
                    
                    abs_directory = os.path.abspath(directory)
                    abs_target = os.path.abspath(target)
                
                    prefix = os.path.commonprefix([abs_directory, abs_target])
                    
                    return prefix == abs_directory
                
                def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
                
                    for member in tar.getmembers():
                        member_path = os.path.join(path, member.name)
                        if not is_within_directory(path, member_path):
                            raise Exception("Attempted Path Traversal in Tar File")
                
                    tar.extractall(path, members, numeric_owner=numeric_owner) 
                    
                
                safe_extract(archive_file, extracted_path)
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
            utils.normalize(name),
            utils.normalize(version)
        )
        shutil.rmtree(extracted_path)

        return data

    def from_native(self, db, name):
        try:
            # only try to extract it if module exist
            __import__(utils.normalize(name))
            data_simple = self._run_extractor(
                pyfile=self.extractor_bundled,
                args=[utils.normalize(name)]
            )
            if data_simple:
                data = {
                    'name': utils.normalize(data_simple['name']),
                    'version': utils.normalize(data_simple['version']),
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
