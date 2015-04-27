import urllib2

import pip.download
import pip.index

import pkgtools.pypi


class PyPi(object):
    def __init__(self):
        self.pypi = pkgtools.pypi.PyPIXmlRpc()
        self.pip_packagefinder = pip.index.PackageFinder(
            find_links=[],
            index_urls=['https://pypi.python.org/simple'],
            session=pip.download.PipSession()
        )

    def package_releases(self, name):
        """Use weird PIP system instead of the official PyPi API.

        They sometimes provide different results. But because eprc is intended
        to be used with PIP, we accept this buggy system here."""
        return list(set(
            str(candidate.version)
            for candidate in self.pip_packagefinder._find_all_versions(name)
        ))

    def release_urls(self, name, version):
        return self.pypi.release_urls(name, version)

    def real_name(self, package_name, timeout=None):
        """Replaces buggy pkgtools.pypi.real_name."""
        r = urllib2.Request(
            'http://pypi.python.org/pypi/{0}'.format(package_name)
        )
        return urllib2.urlopen(r, timeout=timeout).geturl().split('/')[-1]
