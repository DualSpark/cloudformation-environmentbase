from environmentbase.networkbase import NetworkBase


class MyEnvClass(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    def create_hook(self):
        # Do custom troposphere resource creation here
        super(MyEnvClass, self).create_hook()

if __name__ == '__main__':
    MyEnvClass()
