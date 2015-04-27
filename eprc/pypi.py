import pkgtools.pypi
import urllib2


class PyPi(object):
    def __init__(self):
        self.pypi = pkgtools.pypi.PyPIXmlRpc()

    def package_releases(self, name):
        return self.pypi.package_releases(name, show_hidden=True)

    def release_urls(self, name, version):
        return self.pypi.release_urls(name, version)

    def real_name(self, package_name, timeout=None):
        """Replaces buggy pkgtools.pypi.real_name."""
        r = urllib2.Request(
            'http://pypi.python.org/pypi/{0}'.format(package_name)
        )
        return urllib2.urlopen(r, timeout=timeout).geturl().split('/')[-1]
