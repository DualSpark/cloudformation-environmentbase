from environmentbase.template import Template
import environmentbase.resources as res
from troposphere import Ref, Parameter, GetAtt, rds


class RDS(Template):
    """
    Adds an RDS instance.
    """

    # default configuration values
    DEFAULT_CONFIG = {
        'db': {
            'mydb': {
                'db_instance_type_default': 'db.m1.small',
                'rds_user_name': 'defaultusername',
                # Name cannot include non-alphanumeric characters (e.g. '-')
                'master_db_name': 'mydb',
                'volume_size': 100,
                'backup_retention_period': 30,
                'rds_engine': 'mysql',
                # 5.6.19 is no longer supported
                'rds_engine_version': '5.6.22',
                'preferred_backup_window': '02:00-02:30',
                'preferred_maintenance_window': 'sun:03:00-sun:04:00'
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
                'preferred_maintenance_window': 'str'
            }
        }
    }

    def __init__(self,
                 db_name='mydb',
                 security_groups=list(),
                 subnet_set='private',
                 rds_args=DEFAULT_CONFIG['db']['mydb']):
        """
        Method initializes host in a given environment deployment
        @param name [string] - name of the tier to assign
        @param ingress_port [number] - port to allow ingress on. Must be a valid ELB ingress port. More info here: http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-ec2-elb-listener.html
        @param access_cidr [string] - CIDR notation for external access to this tier.
        """

        self.db_name = db_name
        self.rds_args = rds_args
        self.security_groups = security_groups
        self.subnet_set = subnet_set

        super(RDS, self).__init__(template_name=db_name+'RDSInstance')

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

        admin_rds_instance_type = self.add_parameter(Parameter(
            self.db_name.lower() + 'RdsInstanceType',
            Default=self.rds_args.get('db_instance_type_default'),
            Type='String',
            Description='DB Instance Type for the shared admin RDS instance.',
            AllowedValues=res.COMMON_STRINGS.get('valid_db_instance_types'),
            ConstraintDescription=res.COMMON_STRINGS.get('valid_db_instance_type_message')))

        admin_rds_user_name = self.add_parameter(Parameter(
            self.db_name.lower() + 'RdsUserName',
            Default=self.rds_args.get('rds_user_name'),
            Type='String',
            Description='Master RDS User name for the shared admin RDS instance',
            MinLength=3,
            MaxLength=64,
            ConstraintDescription='must be 3 or more characters and not longer than 64 characters.'))

        admin_rds_password = self.add_parameter(Parameter(
            self.db_name.lower() + 'RdsMasterUserPassword',
            NoEcho=True,
            Type='String',
            Description='Master RDS User Password for the shared admin RDS instance.',
            MinLength=12,
            MaxLength=64,
            ConstraintDescription='must be 12 or more characters and not longer than 64 characters.'))

        admin_rds_db_name = self.add_parameter(Parameter(
            self.db_name.lower() + 'RdsDbName',
            Type='String',
            Default=self.rds_args.get('master_db_name'),
            Description='Master RDS database name for the shared admin RDS instance.',
            MinLength=3,
            MaxLength=32,
            ConstraintDescription='must be 3 or more characters and not longer than 64 characters.'))

        admin_rds_db_subnet_group = self.add_resource(rds.DBSubnetGroup(
            self.db_name.lower() + 'RdsSubnetGroup',
            DBSubnetGroupDescription='Subnet group for RDS instance',
            SubnetIds=self.subnets[self.subnet_set]))

        admin_rds = self.add_resource(rds.DBInstance(
            self.db_name.lower() + 'RdsInstance',
            AllocatedStorage=self.rds_args.get('volume_size', '100'),
            BackupRetentionPeriod=self.rds_args.get('backup_retention_period', '30'),
            DBInstanceClass=Ref(admin_rds_instance_type),
            DBName=Ref(admin_rds_db_name),
            VPCSecurityGroups=self.security_groups,
            DBSubnetGroupName=Ref(admin_rds_db_subnet_group),
            Engine=self.rds_args.get('rds_engine', 'mysql'),
            EngineVersion=self.rds_args.get('rds_engine_version', '5.6.19'),
            MasterUsername=Ref(admin_rds_user_name),
            MasterUserPassword=Ref(admin_rds_password),
            PreferredBackupWindow=self.rds_args.get('preferred_backup_window', '02:00-02:30'),
            PreferredMaintenanceWindow=self.rds_args.get('preferred_maintenance_window', 'sun:03:00-sun:04:00'),
            MultiAZ=True))

        self.data = {'rds': admin_rds,
                'dbname': Ref(admin_rds_db_name),
                'endpoint_address': GetAtt(admin_rds, 'Endpoint.Address'),
                'endpoint_port': GetAtt(admin_rds, 'Endpoint.Port'),
                'masteruser': Ref(admin_rds_user_name),
                'masterpassword': Ref(admin_rds_password),
                'securitygroups': self.security_groups}
