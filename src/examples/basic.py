from environmentbase.networkbase import NetworkBase
from environmentbase.environmentbase import EnvConfig


class MyEnvClass(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    def create_hook(self):
        # Do custom troposphere resource creation here
        pass

    def deploy_action(self):

        # Do custom deploy steps here

        super(MyEnvClass, self).deploy_action()


if __name__ == '__main__':
    MyEnvClass()
