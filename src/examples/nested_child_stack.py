from environmentbase.networkbase import NetworkBase
from environmentbase.template import Template
from environmentbase.environmentbase import EnvConfig
from environmentbase.patterns.bastion import Bastion
from troposphere import ec2


class Controller(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """
    def create_hook(self):
        self.add_child_template(ChildTemplate('Child'))
        self.add_child_template(Bastion('Bastion'))


class ChildTemplate(Template):
    """
    Class creates a VPC and common network components for the environment
    """

    # Called from add_child_template() after some common parameters are attached to this instance, see docs for details
    def build_hook(self):
        self.add_resource(ec2.Instance("ChildEC2", InstanceType="m3.medium", ImageId="ami-e7527ed7"))
        self.add_child_template(GrandchildTemplate('Grandchild'))

    # When no config.json file exists a new one is created using the 'factory default' file.  This function
    # augments the factory default before it is written to file with the config values required
    @staticmethod
    def get_factory_defaults():
        return {'my_child_template': {'favorite_color': 'blue'}}

    # When the user request to 'create' a new template the config.json file is read in. This file is checked to
    # ensure all required values are present. Because MyChildTemplate has additional requirements beyond that of
    # EnvironmentBase this function is used to add additional validation checks.
    @staticmethod
    def get_config_schema():
        return {'my_child_template': {'favorite_color': 'str'}}


class GrandchildTemplate(Template):
    def build_hook(self):
        self.add_resource(ec2.Instance("GrandchildEC2", InstanceType="m3.medium", ImageId="ami-e7527ed7"))

if __name__ == '__main__':

    # EnvConfig holds references to handler classes used to extend certain functionality
    # of EnvironmentBase. The config_handlers list takes any class that implements
    # get_factory_defaults() and get_config_schema().
    env_config = EnvConfig(config_handlers=[ChildTemplate])

    Controller(env_config=env_config)
