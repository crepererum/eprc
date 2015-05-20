*******************************************
Experimental Python Requirements Calculator
*******************************************

.. image:: https://img.shields.io/github/tag/crepererum/eprc.svg
        :target: https://github.com/crepererum/eprc/releases

.. image:: https://img.shields.io/pypi/dm/eprc.svg
        :target: https://pypi.python.org/pypi/eprc

.. image:: https://img.shields.io/github/license/crepererum/eprc.svg
        :target: https://github.com/crepererum/eprc/blob/master/LICENSE

This is an **experimental** approach to fix one of the most fundamental issues
with `pip <https://pip.pypa.io/>`_: `The lack of a dependency resolver
<https://github.com/pypa/pip/issues/988>`_.

.. warning::

    Do **not** use this on production systems. It is highly experimtal and may
    contain security bugs!

Requirements
============
To use eprc, you need the following components:

- Python 2.7 (for eprc and the extractors)
- Java 7 or 8 (for the solver)
- Redis (for caching/storage)

Usage
=====
Make sure that Redis is up and running and that the project that you want to
install has a `setup.py`. Now lets generate a `requirements.txt` file that has
a closed and optimal set of packages:

.. code-block:: shell

    eprc calc path/to/project

.. hint::

    This might take a while because eprc fetches all versions of all somehow
    required packages and tries to extras meta information. This is only done
    once because Redis caches this meta data for future sessions. There are
    plans to set up a public caching server.

.. caution::

    In case that eprc is not able to find a requirements set without conflicts,
    you will only get a message and no `requirements.txt` file will be
    written. Sadly there won't be any information about the packages that
    resulted in the conflict.

In case that eprc was able to find a requirements set without conflicts, you can
now install this set and your project:

.. code-block:: shell

    pip install requirements.txt
    cd path/to/project && python setup.py install

.. tip::

    Use `eprc calc --help` to get more information about the different options
    and how to calculate a requirements set for multiple projects
    simultaneously.
