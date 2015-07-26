from environmentbase.networkbase import NetworkBase
from environmentbase.patterns import rds


class MyRootTemplate(NetworkBase):
    """
    Example class showing how to use the RDS pattern file to generate an RDS database as a child stack
    """

    # When no config.json file exists a new one is created using the 'factory default' file.  This function
    # augments the factory default before it is written to file with the config values required
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
        #     'rds': DBInstance
        #     'endpoint_address':GetAtt
        #     'masterpassword': Ref
        #     'securitygroups': [SecurityGroups],
        #     'masteruser': Ref
        #     'endpoint_port': GetAtt
        #     'dbname': Ref
        # }
        print my_db.data

        # Our template is complete output it to file
        self.write_template_to_file()


if __name__ == '__main__':

    MyRootTemplate()
