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
                 security_groups=[],
                 subnet_set='private',
                 rds_args=DEFAULT_CONFIG['db']['mydb']):
        """
        Method initializes bastion host in a given environment deployment
        @param name [string] - name of the tier to assign
        @param ingress_port [number] - port to allow ingress on. Must be a valid ELB ingress port. More info here: http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-ec2-elb-listener.html
        @param access_cidr [string] - CIDR notation for external access to this tier.
        """

        self.db_name = db_name
        self.rds_args = rds_args
        self.security_groups = security_groups
        self.subnet_set = subnet_set

        super(RDS, self).__init__(template_name=db_name+'RDSInstance')

    def build_hook(self):
        """
        Method creates centrally used RDS instance based on arguments as defined.
        @param db_name [string] Name of the DB to create in RDS
        @param rds_sg [Troposphere.ec2.SecurityGroup] Security Group to be assigned to the RDS instance created
        @param rds_args [dict] collection of settings for defaults on parameters and for RDS values that are less commonly parameterized
        @param subnet_set [string] one of the subnet types defined within the config.json file.
        """

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
            DBSubnetGroupDescription='Subnet group for artifactory RDS instance',
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
