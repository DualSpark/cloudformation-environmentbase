from environmentbase.networkbase import NetworkBase
from environmentbase.template import Template
from environmentbase.environmentbase import EnvConfig
from troposphere import ec2


class MyRootTemplate(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """
    def create_hook(self):
        self.add_child_template(MyChildTemplate('ChildTemplate'))


class MyChildTemplate(Template):
    """
    Class creates a VPC and common network components for the environment
    """

    # Called from add_child_template() after some common parameters are attached to this instance, see docs for details
    def build_hook(self):
        self.add_resource(ec2.Instance("ec2instance", InstanceType="m3.medium", ImageId="ami-e7527ed7") )

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

if __name__ == '__main__':

    # EnvConfig holds references to handler classes used to extend certain functionality
    # of EnvironmentBase. The config_handlers list takes any class that implements
    # get_factory_defaults() and get_config_schema().
    env_config = EnvConfig(config_handlers=[MyChildTemplate])

    MyRootTemplate(env_config=env_config)
