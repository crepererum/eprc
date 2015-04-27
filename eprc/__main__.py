import argparse
import itertools
import logging
import pkg_resources

from database import Database
from extractor import Extractor
from pypi import PyPi
from scheduler import Scheduler
import solver
import utils


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
        default="java -jar {}".format(pkg_resources.resource_filename(__name__, "sat4j-pb.jar"))
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
            solver.solve(scheduler, db, must_satisfy, tmpdir, args.solver, args.outfile)


    except utils.HandledError as e:
        logging.error(e.message)

if __name__ == '__main__':
    run()
