from environmentbase.networkbase import NetworkBase


class MyEnvClass(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    def create_action(self):

        self.initialize_template()
        self.construct_network()

        # Do custom troposphere resource creation here

        self.write_template_to_file()

    def deploy_action(self):

        # Do custom deploy steps here

        super(MyEnvClass, self).deploy_action()


if __name__ == '__main__':

    MyEnvClass()
