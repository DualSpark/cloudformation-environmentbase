from pkg_resources import resource_string

try:
    import commentjson as json
except ImportError:
    import json


def _get_internal_resource(resource_name):
    """Retrieves resource embedded in the package (even if installed as a zipped archive)."""
    return json.loads(resource_string(__name__, 'data/' + resource_name))


DEFAULT_CONFIG_FILENAME = 'config.json'
FACTORY_DEFAULT_CONFIG = _get_internal_resource(DEFAULT_CONFIG_FILENAME)


DEFAULT_AMI_CACHE_FILENAME = 'ami_cache.json'
FACTORY_DEFAULT_AMI_CACHE = _get_internal_resource(DEFAULT_AMI_CACHE_FILENAME)


CONFIG_REQUIREMENTS_FILENAME = 'config_schema.json'
CONFIG_REQUIREMENTS = _get_internal_resource(CONFIG_REQUIREMENTS_FILENAME)


COMMON_STRINGS_FILENAME = 'common_strings.json'
COMMON_STRINGS = _get_internal_resource(COMMON_STRINGS_FILENAME)


def get_str(key, default=None):
    return COMMON_STRINGS.get(key, default)


def get_type(typename):
    """
    Convert typename to type object
    :param typename: String name of type
    :return: __builtin__ type instance
    """
    types = {
        'bool': bool,
        'int': int,
        'float': float,
        'str': str,
        'basestring': basestring
    }
    return types.get(typename, None)
