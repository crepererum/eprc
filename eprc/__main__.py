import argparse
import itertools
import logging
import pkg_resources
import pprint

from database import Database
from extractor import Extractor
from pypi import PyPi
from scheduler import Scheduler
import solver
import utils


def run_calc(args):
    try:
        with utils.TemporaryDirectory() as tmpdir:
            logging.getLogger().setLevel(logging.INFO)
            logging.basicConfig(
                format="%(asctime)s [%(levelname)s]: %(message)s"
            )
            pypi = PyPi()
            extractor = Extractor(
                virtualenv=args.virtualenv,
                tmpdir=tmpdir,
                pypi=pypi
            )
            db = Database(
                host=args.redis_host,
                port=args.redis_port,
                db=args.redis_db
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
                        utils.normalize(data['name']),
                        utils.normalize(data['version'])
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
            solver.solve(
                scheduler,
                db,
                must_satisfy,
                tmpdir,
                args.solver,
                args.outfile,
                args.include_starting_points
            )

    except utils.HandledError as e:
        logging.error(e.message)


def run_get(args):
    db = Database(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db
    )

    if args.version:
        pprint.pprint(db.get(args.name, args.version))
    else:
        pprint.pprint([
            db.get(args.name, version)
            for version in db.all_versions(args.name)
        ])


def run():
    parser = argparse.ArgumentParser(
        prog='eprc',
        description='Experimental Python Requirements Calculator',
        epilog='WARNING: Do not use this for produciton use!',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        '--redis-host',
        help='Redis host used for caching.',
        type=str,
        default='localhost'
    )

    parser.add_argument(
        '--redis-port',
        help='Redis port',
        type=int,
        default=6378
    )

    parser.add_argument(
        '--redis-db',
        help='Redis DB number',
        type=int,
        default=0
    )

    subparsers = parser.add_subparsers()

    parser_calc = subparsers.add_parser(
        'calc',
        help='Calculate requirements and write them to a requirements '
        'file used by pip'
    )
    parser_calc.set_defaults(func=run_calc)

    parser_calc.add_argument(
        "-e", "--virtualenv",
        help='The virtualenv command used to create clean environments for '
        'process isolation.',
        type=str,
        default="virtualenv2"
    )

    parser_calc.add_argument(
        'paths',
        help='Paths of the packages you want the requirements calculate for.',
        nargs='+',
        type=str
    )

    parser_calc.add_argument(
        "-c", "--cached",
        help='Only used cached data and do not extract new requirements from '
        'PyPi packages',
        action="store_true",
        default=False
    )

    parser_calc.add_argument(
        "-i", "--include-starting-points",
        help='Include requirements that are given by paths, e.g. if one of '
        'the paths contain `foo` at version 1.0, `foo==1.0` will be added to'
        'the output file.',
        action="store_true",
        default=False
    )

    parser_calc.add_argument(
        "-s", "--solver",
        help='The Pseudo Boolean Constraint Optimzation solver used for '
        'finding a feasable and good set of packages to install. It must '
        'accept OPB files and must write the solution to STDOUT. See the '
        'following PDF for a complete specification: '
        'http://www.cril.univ-artois.fr/PB12/format.pdf',
        type=str,
        default="java -jar {}".format(
            pkg_resources.resource_filename(__name__, "sat4j-pb.jar")
        )
    )

    parser_calc.add_argument(
        "-o", "--outfile",
        help='Output file (usually requirements.txt) that can be used by pip.',
        type=str,
        default="requirements.txt"
    )

    parser_get = subparsers.add_parser(
        'get',
        help='Gets cached requirements data from database.'
    )
    parser_get.set_defaults(func=run_get)

    parser_get.add_argument(
        'name',
        help='Name of the package.',
        type=str
    )

    parser_get.add_argument(
        'version',
        help='Optional package version.',
        nargs='?',
        type=str
    )

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    run()
