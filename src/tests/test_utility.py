from unittest2 import TestCase
from environmentbase import utility
from environmentbase.template import Template


class Parent(object):
    pass


class A(Parent):
    pass


class B(Parent):
    pass


class C(Parent):
    pass


class MyTemplate(Template):
    @staticmethod
    def get_factory_defaults():
        return {'new_section': {'new_key': 'value'}}

    @staticmethod
    def get_config_schema():
        return {'new_section': {'new_key': 'basestring'}}


class UtilityTestCase(TestCase):

    def test__get_subclasses_of(self):
        actual_subclasses = [A, B, C]
        retreived_subclasses = utility._get_subclasses_of('tests.test_utility', 'Parent')
        self.assertEqual(actual_subclasses, retreived_subclasses)

    def test_get_pattern_list(self):
        # Count patterns from previous test runs (no way to unload classes as far as I know)
        num_patterns = len(utility.get_pattern_list())

        # Verify that a loaded a pattern is identified
        mod = __import__('environmentbase.patterns.bastion', fromlist=['Bastion'])
        klazz = getattr(mod, 'Bastion')
        patterns = utility.get_pattern_list()

        self.assertGreater(len(patterns), num_patterns)
        self.assertIn(klazz, patterns)

    def test__update_from_patterns(self):
        _dict = {}
        utility._update_from_patterns(_dict, 'get_factory_defaults')
        self.assertIn('new_section', _dict)
        self.assertEqual('value', _dict['new_section']['new_key'])

        _dict = {}
        utility._update_from_patterns(_dict, 'get_config_schema')
        self.assertIn('new_section', _dict)
        self.assertEqual('basestring', _dict['new_section']['new_key'])
