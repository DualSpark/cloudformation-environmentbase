import unittest

from environmentbase.environmentbase import EnvironmentBase


class EnvironmentBaseTestCase(unittest.TestCase):
    def test_constructor(self):
        env_base = EnvironmentBase({})
        self.assertIsNotNone(env_base)

if __name__ == '__main__':
    unittest.main()
