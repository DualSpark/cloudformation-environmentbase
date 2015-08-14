from environmentbase.template import Template
from troposphere import Ref, Join, Base64, FindInMap
from troposphere.ec2 import SecurityGroup, SecurityGroupIngress, SecurityGroupEgress
from troposphere.autoscaling import AutoScalingGroup, LaunchConfiguration, Tags
from troposphere.iam import Policy, Role, InstanceProfile


class HaNat(Template):
    '''
    Adds a highly available NAT that also serves as an NTP server
    Creates a 1-1 autoscaling group in the provided public subnet and creates
    a route directing egress traffic from the private subnet through this NAT
    '''

    def __init__(self, subnet_index, instance_type='t2.micro', name='HaNat'):
        '''
        Method initializes HA NAT in a given environment deployment
        @param subnet_index [int] ID of the subnet that the NAT instance will be deployed to
        @param instance_type [string] - Type of NAT instance in the autoscaling group
        '''
        self.subnet_index = subnet_index
        self.instance_type = instance_type

        # These will be initialized and consumed by various functions called in the build hook
        self.sg = None
        self.instance_profile = None

        super(HaNat, self).__init__(template_name=name)

    def build_hook(self):
        '''
        Hook to add tier-specific assets within the build stage of initializing this class.
        '''
        self.add_nat_sg()
        self.add_nat_instance_profile()
        self.add_nat_asg()

    def add_nat_sg(self):
        '''
        Create the NAT security group and add the ingress/egress rules
        '''
        self.sg = self.add_resource(SecurityGroup(
            "Nat%sSG" % str(self.subnet_index),
            VpcId=Ref(self.vpc_id),
            GroupDescription="Security group for NAT host."
        ))
        self.add_nat_sg_rules()

    def add_nat_sg_rules(self):
        '''
        Add the security group rules necessary for the NAT to operate
        For now, this is opening all ingress from the VPC and all egress to the internet
        '''
        self.add_resource(SecurityGroupIngress(
            "Nat%sIngress" % str(self.subnet_index),
            ToPort="-1",
            FromPort="-1",
            IpProtocol="-1",
            GroupId=Ref(self.sg),
            CidrIp=self.vpc_cidr
        ))
        self.add_resource(SecurityGroupEgress(
            "Nat%sEgress" % str(self.subnet_index),
            ToPort="-1",
            FromPort="-1",
            IpProtocol="-1",
            GroupId=Ref(self.sg),
            CidrIp='0.0.0.0/0'
        ))

    def add_nat_instance_profile(self):
        '''
        Create the NAT role and instance profile
        '''
        nat_role = self.add_resource(Role(
            "Nat%sRole" % str(self.subnet_index),
            AssumeRolePolicyDocument={
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {
                        "Service": ["ec2.amazonaws.com"]
                    },
                    "Action": ["sts:AssumeRole"]
                 }]
            },
            Path="/",
            Policies=[Policy(
                PolicyName="NAT%sPolicy" % str(self.subnet_index),
                PolicyDocument={
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "ec2:DescribeInstances",
                            "ec2:ModifyInstanceAttribute",
                            "ec2:DescribeSubnets",
                            "ec2:DescribeRouteTables",
                            "ec2:CreateRoute",
                            "ec2:ReplaceRoute",
                            "ec2:StartInstances",
                            "ec2:StopInstances"
                        ],
                        "Resource": "*"
                    }]
                }
            )]
        ))

        self.instance_profile = self.add_resource(InstanceProfile(
            "Nat%sInstanceProfile" % str(self.subnet_index),
            Path="/",
            Roles=[Ref(nat_role)]
        ))

    def add_nat_asg(self):

        nat_launch_config = self.add_resource(LaunchConfiguration(
            "Nat%sLaunchConfig" % str(self.subnet_index),
            UserData=Base64(Join("", [
                "#!/bin/bash -v\n",
                "function log { logger -t \"vpc\" -- $1; }\n",
                "function die {\n",
                "  [ -n \"$1\" ] && log \"$1\"\n",
                "  log \"Configuration of HA NAT failed!\"\n",
                "  exit 1\n",
                "}\n",

                "# Sanitize PATH\n",
                "PATH=\"/usr/sbin:/sbin:/usr/bin:/bin\"\n",

                "# Configure the instance to run as a Port Address Translator (PAT) to provide\n",
                "# Internet connectivity to private instances.\n",

                "log \"Beginning Port Address Translator (PAT) configuration...\"\n",
                "log \"Determining the MAC address on eth0...\"\n",
                "ETH0_MAC=$(cat /sys/class/net/eth0/address) ||\n",
                "    die \"Unable to determine MAC address on eth0.\"\n",
                "log \"Found MAC ${ETH0_MAC} for eth0.\"\n",

                "VPC_CIDR_URI=\"http://169.254.169.254/latest/meta-data/network/interfaces/macs/${ETH0_MAC}/vpc-ipv4-cidr-block\"\n",
                "log \"Metadata location for vpc ipv4 range: ${VPC_CIDR_URI}\"\n",

                "VPC_CIDR_RANGE=$(curl --retry 3 --silent --fail ${VPC_CIDR_URI})\n",
                "if [ $? -ne 0 ]; then\n",
                "   log \"Unable to retrive VPC CIDR range from meta-data, using 0.0.0.0/0 instead. PAT may be insecure!\"\n",
                "   VPC_CIDR_RANGE=\"0.0.0.0/0\"\n",
                "else\n",
                "   log \"Retrieved VPC CIDR range ${VPC_CIDR_RANGE} from meta-data.\"\n",
                "fi\n",

                "log \"Enabling PAT...\"\n",
                "sysctl -q -w net.ipv4.ip_forward=1 net.ipv4.conf.eth0.send_redirects=0 && (\n",
                "   iptables -t nat -C POSTROUTING -o eth0 -s ${VPC_CIDR_RANGE} -j MASQUERADE 2> /dev/null ||\n",
                "   iptables -t nat -A POSTROUTING -o eth0 -s ${VPC_CIDR_RANGE} -j MASQUERADE ) ||\n",
                "       die\n",

                "sysctl net.ipv4.ip_forward net.ipv4.conf.eth0.send_redirects | log\n",
                "iptables -n -t nat -L POSTROUTING | log\n",

                "log \"Configuration of NAT/PAT complete.\"\n",

                "# Install AWS CLI tool\n",
                "#apt-get -y install python-pip\n",
                "#pip install --upgrade awscli  && log \"AWS CLI Upgraded Successfully. Beginning HA NAT configuration...\"\n"

                "awscmd=\"/usr/bin/aws\"\n",

                "# Set CLI Output to text\n",
                "export AWS_DEFAULT_OUTPUT=\"text\"\n",

                "# Set Instance Identity URI\n",
                "II_URI=\"http://169.254.169.254/latest/dynamic/instance-identity/document\"\n",

                "# Set region of NAT instance\n",
                "REGION=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep region | awk -F\\\" '{print $4}')\n",

                "# Set AWS CLI default Region\n",
                "export AWS_DEFAULT_REGION=$REGION\n",

                "# Set AZ of NAT instance\n",
                "AVAILABILITY_ZONE=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep availabilityZone | awk -F\\\" '{print $4}')\n",

                "# Set Instance ID from metadata\n",
                "INSTANCE_ID=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep instanceId | awk -F\\\" '{print $4}')\n",

                "# Set VPC_ID of Instance\n",
                "VPC_ID=$(${awscmd} ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[*].Instances[*].VpcId') ||\n",
                "  die \"Unable to determine VPC ID for instance.\"\n",

                "# Determine Main Route Table for the VPC\n",
                "MAIN_RT=$(${awscmd} ec2 describe-route-tables --query 'RouteTables[*].RouteTableId' --filters Name=vpc-id,Values=$VPC_ID Name=association.main,Values=true) ||\n",
                "  die \"Unable to determine VPC Main Route Table.\"\n",

                "log \"HA NAT configuration parameters: Instance ID=$INSTANCE_ID, Region=$REGION, Availability Zone=$AVAILABILITY_ZONE, VPC=$VPC_ID\"\n",

                "# Get list of subnets in same VPC that have tag network=private\n",
                "PRIVATE_SUBNETS=\"$(${awscmd} ec2 describe-subnets --query 'Subnets[*].SubnetId' --filters Name=availability-zone,Values=$AVAILABILITY_ZONE Name=vpc-id,Values=$VPC_ID Name=state,Values=available Name=tag:network,Values=private)\"\n",
                "# Substitute the previous line with the next line if you want only one NAT instance for all zones (make sure to set the autoscale group to min/max/desired 1)\n",
                "#--filters Name=vpc-id,Values=$VPC_ID Name=state,Values=available Name=tag:network,Values=private)\n",
                "# If no private subnets found, exit out\n",
                "if [ -z \"$PRIVATE_SUBNETS\" ]; then\n",
                "  die \"No private subnets found to modify for HA NAT.\"\n",
                "else \n",
                "  log \"Modifying Route Tables for following private subnets: $PRIVATE_SUBNETS\"\n",
                "fi\n",
                "for subnet in $PRIVATE_SUBNETS; do\n",
                "  ROUTE_TABLE_ID=$(${awscmd} ec2 describe-route-tables --query 'RouteTables[*].RouteTableId' --filters Name=association.subnet-id,Values=$subnet);\n",
                "  # If private tagged subnet is associated with Main Routing Table, do not create or modify route.\n",
                "  if [ \"$ROUTE_TABLE_ID\" = \"$MAIN_RT\" ]; then\n",
                "    log \"$subnet is associated with the VPC Main Route Table. HA NAT script will NOT edit Main Route Table.\"\n",
                "  # If subnet is not associated with a Route Table, skip it.\n",
                "  elif [ -z \"$ROUTE_TABLE_ID\" ]; then\n",
                "    log \"$subnet is not associated with a Route Table. Skipping this subnet.\"\n",
                "  else\n",
                "    # Modify found private subnet's Routing Table to point to new HA NAT instance id\n",
                "    ${awscmd} ec2 create-route --route-table-id $ROUTE_TABLE_ID --destination-cidr-block 0.0.0.0/0 --instance-id $INSTANCE_ID &&\n",
                "    log \"$ROUTE_TABLE_ID associated with $subnet modified to point default route to $INSTANCE_ID.\"\n",
                "    if [ $? -ne 0 ] ; then\n",
                "      log \"Route already exists, replacing existing route.\"\n",
                "      ${awscmd} ec2 replace-route --route-table-id $ROUTE_TABLE_ID --destination-cidr-block 0.0.0.0/0 --instance-id $INSTANCE_ID\n",
                "    fi\n",
                "  fi\n",
                "done\n",

                "if [ $? -ne 0 ] ; then\n",
                "  die\n",
                "fi\n",

                "# Turn off source / destination check\n",
                "${awscmd} ec2 modify-instance-attribute --instance-id $INSTANCE_ID --no-source-dest-check &&\n",
                "log \"Source Destination check disabled for $INSTANCE_ID.\"\n",

                "log \"Configuration of HA NAT complete.\"\n",
                "yum update -y aws*\n",
                ". /etc/profile.d/aws-apitools-common.sh\n",
                "# Configure iptables\n",
                "/sbin/iptables -t nat -A POSTROUTING -o eth0 -s 0.0.0.0/0 -j MASQUERADE\n",
                "/sbin/iptables-save > /etc/sysconfig/iptables\n",
                "exit 0\n"
            ])),
            ImageId=FindInMap('RegionMap', Ref('AWS::Region'), 'natAmiId'),
            KeyName=Ref('ec2Key'),
            SecurityGroups=[Ref(self.sg)],
            EbsOptimized=False,
            IamInstanceProfile=Ref(self.instance_profile),
            InstanceType=self.instance_type,
            AssociatePublicIpAddress=True
        ))

        nat_asg = self.add_resource(AutoScalingGroup(
            "Nat%sASG" % str(self.subnet_index),
            DesiredCapacity=1,
            Tags=Tags(Name=Join("-", [Ref(self.vpc_id), "NAT"])),
            MinSize=1,
            MaxSize=1,
            Cooldown="30",
            LaunchConfigurationName=Ref(nat_launch_config),
            HealthCheckGracePeriod=30,
            HealthCheckType="EC2",
            VPCZoneIdentifier=[Ref(self.subnets['public'][self.subnet_index])]
        ))

        return nat_asg
