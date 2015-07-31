from environmentbase.networkbase import NetworkBase
from environmentbase.patterns import rds


class MyRootTemplate(NetworkBase):
    """
    Example class showing how to use the RDS pattern file to generate an RDS database as a child stack
    """

    def __init__(self, *args, **kwargs):
        self.add_config_handler(rds.RDS)
        super(MyRootTemplate, self).__init__(*args, **kwargs)

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
