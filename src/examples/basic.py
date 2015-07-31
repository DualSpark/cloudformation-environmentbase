from environmentbase.networkbase import NetworkBase


class MyEnvClass(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    def __init__(self, *args, **kwargs):

        # Add config handlers here if using template patterns requiring configuration
        # self.add_config_handler(<template class>)

        super(MyEnvClass, self).__init__(*args, **kwargs)

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
