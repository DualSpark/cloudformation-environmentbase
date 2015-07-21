import json
from pkg_resources import resource_string

__author__ = 'Eric Price'

DEFAULT_CONFIG_FILENAME = 'config.json'
DEFAULT_AMI_CACHE_FILENAME = 'ami_cache.json'
COMMON_STRINGS_FILENAME = 'common_strings.json'


def _get_internal_resource(resource_name):
    """Retrieves resource embedded in the package (even if installed as a zipped archive)."""
    return json.loads(resource_string(__name__, 'data/' + resource_name))

FACTORY_DEFAULT_CONFIG = _get_internal_resource(DEFAULT_CONFIG_FILENAME)

FACTORY_DEFAULT_AMI_CACHE = _get_internal_resource(DEFAULT_AMI_CACHE_FILENAME)

COMMON_STRINGS = _get_internal_resource(COMMON_STRINGS_FILENAME)


def get_str(key, default=None):
    return COMMON_STRINGS.get(key, default)
