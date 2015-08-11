from environmentbase.template import Template, tropo_to_string
import environmentbase.resources as res
from environmentbase.networkbase import NetworkBase
from troposphere import Ref, Parameter, GetAtt, Output, Join, rds, ec2


class RDS(Template):
    """
    Adds an RDS instance.
    """

    # default configuration values
    DEFAULT_CONFIG = {
        'db': {
            # Database label
            'mydb': {
                'db_instance_type_default': 'db.m1.small',
                'rds_user_name': 'defaultusername',
                # Actual database name, cannot include non-alphanumeric characters (e.g. '-')
                'master_db_name': 'mydb',
                'volume_size': 100,
                'backup_retention_period': 30,
                'rds_engine': 'mysql',
                # 5.6.19 is no longer supported
                'rds_engine_version': '5.6.22',
                'preferred_backup_window': '02:00-02:30',
                'preferred_maintenance_window': 'sun:03:00-sun:04:00',
                # Name of vm snapshot to use, empty string ('') means don't use an old snapshot
                # Note: 'master_db_name' value will be overridden if snapshot_id is non-empty
                'snapshot_id': '',
                # Empty string is ignored, requiring manual parameter binding instead
                'password': 'changeme'
            }
        }
    }

    # schema of expected types for config values
    CONFIG_SCHEMA = {
        'db': {
            '*': {
                'db_instance_type_default': 'str',
                'rds_user_name': 'str',
                'master_db_name': 'str',
                'volume_size': 'int',
                'backup_retention_period': 'int',
                'rds_engine': 'str',
                'rds_engine_version': 'str',
                'preferred_backup_window': 'str',
                'preferred_maintenance_window': 'str',
                'snapshot_id': 'str',
                'password': 'str'
            }
        }
    }

    def __init__(self,
                 tier_name,
                 connect_from_cidr=None,
                 connect_from_sg=None,
                 subnet_set='private',
                 config_map=DEFAULT_CONFIG['db']):
        """
        Method initializes host in a given environment deployment
        @param tier_name: [string] - name of the tier to assign
        @param connect_from_cidr: [Ref|string] - CIDR notation for external access to RDS instance. Cannot be used in conjunction with connect_from_sg. If neither setting is used the CIDR of the VPC will be used.
        @param connect_from_sg: [Ref|string] - Name of security group allowed to access RDS instance. Cannot be used in conjunction with connect_from_cidr.
        @param subnet_set: 'public' or 'private', Type of subnets to use
        @param config_map: map of database config settings to be deployed.
        """

        self.tier_name = tier_name
        self.config_map = config_map
        self.subnet_set = subnet_set

        if connect_from_cidr and connect_from_sg:
            raise ValueError("RDS instance cannot be configured to be accessible from both CIDR and security group.")

        self.connect_from_cidr = connect_from_cidr
        self.connect_from_sg = connect_from_sg

        self.data = {}

        super(RDS, self).__init__(template_name=tier_name+'RDSInstance')

    # When no config.json file exists a new one is created using the 'factory default' file.  This function
    # augments the factory default before it is written to file with the config values required
    @staticmethod
    def get_factory_defaults():
        return RDS.DEFAULT_CONFIG

    # When the user request to 'create' a new RDS template the config.json file is read in. This file is checked to
    # ensure all required values are present. Because RDS has additional requirements beyond that of
    # EnvironmentBase this function is used to add additional validation checks.
    @staticmethod
    def get_config_schema():
        return RDS.CONFIG_SCHEMA

    def add_parameters(self, db_label, db_config):
        instance_type_param = self.add_parameter(Parameter(
            db_label.lower() + self.tier_name.title() + 'RdsInstanceType',
            Default=db_config.get('db_instance_type_default'),
            Type='String',
            Description='DB Instance Type for the RDS instance.',
            AllowedValues=res.COMMON_STRINGS.get('valid_db_instance_types'),
            ConstraintDescription=res.COMMON_STRINGS.get('valid_db_instance_type_message')))

        name_param = self.add_parameter(Parameter(
            db_label.lower() + self.tier_name.title() + 'RdsDbName',
            Type='String',
            Default=db_config.get('master_db_name'),
            Description='Master RDS database name for the RDS instance.',
            MinLength=3,
            MaxLength=32,
            ConstraintDescription='must be 3 or more characters and not longer than 64 characters.'))

        user_name_param = self.add_parameter(Parameter(
            db_label.lower() + self.tier_name.title() + 'RdsUserName',
            Default=db_config.get('rds_user_name'),
            Type='String',
            Description='Master RDS User name for the RDS instance',
            MinLength=3,
            MaxLength=64,
            ConstraintDescription='must be 3 or more characters and not longer than 64 characters.'))

        user_password_param = self.add_parameter(Parameter(
            db_label.lower() + self.tier_name.title() + 'RdsMasterUserPassword',
            NoEcho=True,
            Type='String',
            Description='Master RDS User Password for the RDS instance.',
            MinLength=12,
            MaxLength=64,
            ConstraintDescription='must be 12 or more characters and not longer than 64 characters.'))

        return instance_type_param, name_param, user_name_param, user_password_param

    # Called after add_child_template() has attached common parameters and some instance attributes:
    # - RegionMap: Region to AMI map, allows template to be deployed in different regions without updating AMI ids
    # - ec2Key: keyname to use for ssh authentication
    # - vpcCidr: IP block claimed by whole VPC
    # - vpcId: resource id of VPC
    # - commonSecurityGroup: sg identifier for common allowed ports (22 in from VPC)
    # - utilityBucket: S3 bucket name used to send logs to
    # - availabilityZone[0-3]: Indexed names of AZs VPC is deployed to
    # - [public|private]Subnet[0-9]: indexed and classified subnet identifiers
    #
    # and some instance attributes referencing the attached parameters:
    # - self.vpc_cidr
    # - self.vpc_id
    # - self.common_security_group
    # - self.utility_bucket
    # - self.subnets: keyed by type and index (e.g. self.subnets['public'][1])
    # - self.azs: List of parameter references
    def build_hook(self):

        for db_label, db_config in self.config_map.iteritems():

            (db_instance_type,
             db_name,
             db_user_name,
             db_user_password) = self.add_parameters(db_label, db_config)

            subnet_group = self.add_resource(rds.DBSubnetGroup(
                db_label.lower() + 'RdsSubnetGroup',
                DBSubnetGroupDescription='Subnet group for the RDS instance',
                SubnetIds=self.subnets[self.subnet_set]))

            rds_sg = self.add_resource(
                ec2.SecurityGroup(
                    db_label.lower() + self.tier_name.title() + 'RdsSg',
                    GroupDescription='Security group for %s RDS tier' % self.tier_name.lower(),
                    VpcId=Ref(self.vpc_id))
            )

            rds_instance = self.add_resource(rds.DBInstance(
                db_label.lower() + self.tier_name.title() + 'RdsInstance',
                AllocatedStorage=db_config.get('volume_size', '100'),
                BackupRetentionPeriod=db_config.get('backup_retention_period', '30'),
                DBInstanceClass=Ref(db_instance_type),
                DBName=Ref(db_name),
                VPCSecurityGroups=[Ref(rds_sg)],
                DBSubnetGroupName=Ref(subnet_group),
                Engine=db_config.get('rds_engine', 'mysql'),
                EngineVersion=db_config.get('rds_engine_version', '5.6.19'),
                MasterUsername=Ref(db_user_name),
                MasterUserPassword=Ref(db_user_password),
                PreferredBackupWindow=db_config.get('preferred_backup_window', '02:00-02:30'),
                PreferredMaintenanceWindow=db_config.get('preferred_maintenance_window', 'sun:03:00-sun:04:00'),
                MultiAZ=True))

            # Set the snapshot id if provided (and null out the db name to avoid cfn error)
            if db_config['snapshot_id']:
                rds_instance.DBSnapshotIdentifier = db_config['snapshot_id']
                # DBName must be null when restoring from snapshot
                rds_instance.DBName = ''

            # Create the sg ingress rule for whatever port the rds instance needs
            ingress_rule = ec2.SecurityGroupIngress(
                db_label.lower() + self.tier_name.title() + 'RdsIngressRule',
                FromPort=GetAtt(rds_instance, "Endpoint.Port"),
                ToPort=GetAtt(rds_instance, "Endpoint.Port"),
                IpProtocol='tcp',
                GroupId=Ref(rds_sg))

            # Set the allowed origin on the ingress rule according the requested connect_from setting
            # OR vpc_cidr in no other setting provided
            if self.connect_from_sg:
                ingress_rule.SourceSecurityGroupId = self.connect_from_sg
            elif self.connect_from_cidr:
                ingress_rule.CidrIp = self.connect_from_cidr
            else:
                ingress_rule.CidrIp = Ref(self.vpc_cidr)

            self.add_resource(ingress_rule)

            # Add the connection endpoint output
            self.add_output(Output(
                db_label.lower() + self.tier_name.title() + 'RdsEndpoint',
                Value=Join('', [
                    Ref(db_user_name), '@',
                    GetAtt(rds_instance, "Endpoint.Address"), ':',
                    GetAtt(rds_instance, "Endpoint.Port")])
            ))

            self.data[db_label] = {
                'rds': rds_instance,
                'dbname': Ref(db_name),
                'endpoint_address': GetAtt(rds_instance, 'Endpoint.Address'),
                'endpoint_port': GetAtt(rds_instance, 'Endpoint.Port'),
                'masteruser': Ref(db_user_name),
                'masterpassword': Ref(db_user_password),
                'securitygroup': rds_sg
            }


class Controller(NetworkBase):
    """
    Example class showing how to use the RDS pattern file to generate an RDS database as a child stack

    To connect to this database log into the bastion and run:
    > sudo yum install -y mysql
    > mysql -h <db endpoint> -P <db port> -u <db username> -p
    """

    def __init__(self, *args, **kwargs):
        self.add_config_handler(RDS)
        super(Controller, self).__init__(*args, **kwargs)

    def create_action(self):
        self.initialize_template()
        self.construct_network()

        # Create the rds instance pattern (includes standard standard parameters)
        my_db = RDS(
            'dbTier',
            subnet_set='private',
            config_map=db_config)

        # Attach pattern as a child template
        self.add_child_template(my_db)

        # Our template is complete output it to file
        self.write_template_to_file()

    def deploy_action(self):
        self._load_db_passwords_from_env()

        for db_label, db_config in self.config['db'].iteritems():
            self.deploy_parameter_bindings.append({
                'ParameterKey': db_label.lower() + 'dbTier'.title() + 'RdsMasterUserPassword',
                'ParameterValue': db_config['password']
            })
        super(Controller, self).deploy_action()

if __name__ == '__main__':

    db_config = {
        'label1': {
            'db_instance_type_default': 'db.m1.small',
            'rds_user_name': 'defaultusername',
            # Actual database name, cannot include non-alphanumeric characters (e.g. '-')
            'master_db_name': 'mydb',
            'volume_size': 100,
            'backup_retention_period': 30,
            'rds_engine': 'mysql',
            # 5.6.19 is no longer supported
            'rds_engine_version': '5.6.22',
            'preferred_backup_window': '02:00-02:30',
            'preferred_maintenance_window': 'sun:03:00-sun:04:00',
            # Name of vm snapshot to use, empty string ('') means don't use an old snapshot
            # Note: 'master_db_name' value will be overridden if snapshot_id is non-empty
            'snapshot_id': '',
            'password': 'changeme111111111111'
        },
        'label2': {
            'db_instance_type_default': 'db.m1.small',
            'rds_user_name': 'defaultusername',
            # Actual database name, cannot include non-alphanumeric characters (e.g. '-')
            'master_db_name': 'mydb2',
            'volume_size': 100,
            'backup_retention_period': 30,
            'rds_engine': 'mysql',
            # 5.6.19 is no longer supported
            'rds_engine_version': '5.6.22',
            'preferred_backup_window': '02:00-02:30',
            'preferred_maintenance_window': 'sun:03:00-sun:04:00',
            # Name of vm snapshot to use, empty string ('') means don't use an old snapshot
            # Note: 'master_db_name' value will be overridden if snapshot_id is non-empty
            'snapshot_id': '',
            'password': 'changeme1111111111111'
        }
    }

    my_config = res.FACTORY_DEFAULT_CONFIG
    my_config['db'] = db_config

    Controller(config=my_config)
