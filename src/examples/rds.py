from environmentbase.networkbase import NetworkBase
from environmentbase.patterns import rds


class MyRootTemplate(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    # When no config.json file exists a new one is created using the 'factory default' file.  This function
    # augments the factory default before it is written to file with the config values required by an ElkTemplate
    @staticmethod
    def get_factory_defaults_hook():
        return rds.RDS.DEFAULT_CONFIG

    # When the user request to 'create' a new RDS template the config.json file is read in. This file is checked to
    # ensure all required values are present. Because RDS has additional requirements beyond that of
    # EnvironmentBase this function is used to add additional validation checks.
    @staticmethod
    def get_config_schema_hook():
        return rds.RDS.CONFIG_SCHEMA

    def create_action(self):
        self.initialize_template()
        self.construct_network()

        # Supply the user password as a manual parameter binding
        self.manual_parameter_bindings['mydbRdsMasterUserPassword'] = 'secret123password'

        # Create the rds instance pattern (includes standard standard parameters)
        my_db = rds.RDS(
            security_groups=[],
            subnet_set='private',
            rds_args=self.config['db']['mydb'])

        # Attach pattern as a child template
        self.add_child_template(my_db)

        # After attaching the db as a child template you can access the created resources
        # {
        #     'rds': <troposphere.rds.DBInstance object at 0x102dc90d0>,
        #     'endpoint_address': <troposphere.GetAtt object at 0x102dc9150>,
        #     'masterpassword': <troposphere.Ref object at 0x102dc9210>,
        #     'securitygroups': [],
        #     'masteruser': <troposphere.Ref object at 0x102dc91d0>,
        #     'endpoint_port': <troposphere.GetAtt object at 0x102dc9190>,
        #     'dbname': <troposphere.Ref object at 0x102dc9110>
        # }
        print my_db.data

        self.write_template_to_file()


if __name__ == '__main__':

    MyRootTemplate()
