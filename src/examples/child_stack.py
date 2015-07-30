from environmentbase.networkbase import NetworkBase
from environmentbase.template import Template
from troposphere import ec2


class MyRootTemplate(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    def create_action(self):

        self.initialize_template()
        self.construct_network()

        self.add_child_template(MyChildTemplate('ChildTemplate'))

        self.write_template_to_file()


class MyChildTemplate(Template):
    """
    Class creates a VPC and common network components for the environment
    """

    def __init__(self, template_name):
        super(MyChildTemplate, self).__init__(template_name)

    # Called from add_child_template() after some common parameters are attached to this instance, see docs for details
    def build_hook(self):
        self.add_resource(ec2.Instance("ec2instance", InstanceType="m3.medium", ImageId="ami-e7527ed7") )


if __name__ == '__main__':

    MyRootTemplate()
