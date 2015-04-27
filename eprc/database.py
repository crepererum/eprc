import redis
import json

import utils


class Database(object):
    def __init__(self, host, port, db):
        self.redis = redis.StrictRedis(host=host, port=port, db=db)

    @staticmethod
    def name_version_to_key(name, version):
        return "{}:{}".format(utils.normalize(name), utils.normalize(version))

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
            for key in self.redis.keys("{}:*".format(utils.normalize(name)))
        ]
