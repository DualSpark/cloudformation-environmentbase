from environmentbase.template import Template
from troposphere import Parameter, Ref, Join, Tags, Base64
from troposphere.ec2 import SecurityGroup, SecurityGroupIngress
from troposphere.autoscaling import AutoScalingGroup, LaunchConfiguration
from troposphere.iam import Policy, Role, InstanceProfile


class HaNat(Template):
    '''
    Adds a highly available NAT that also serves as an NTP server
    '''

    def __init__(self, asg_min=1, asg_max=1, instance_type='t2.micro', install_ntp=False):
        '''
        Method initializes HA NAT in a given environment deployment
        @param asg_size [number] - Number of instances in the autoscaling group
        @param instance_type [string] - Type of instances in the autoscaling group
        @param install_ntp [boolean] - Toggle for installing NTP on the NAT instances
        '''
        self.asg_min = asg_min
        self.asg_max = asg_max
        self.instance_type = instance_type
        self.install_ntp = install_ntp

        super(HaNat, self).__init__(template_name='HaNat')

    def build_hook(self):
        '''
        Hook to add tier-specific assets within the build stage of initializing this class.
        '''

        NatImage = self.add_parameter(Parameter(
            "NatImage",
            Type="String",
            Description="AMI to use for the NAT instance",
            MinLength="12",
            AllowedPattern="ami-(\\w{8})",
            MaxLength="12",
            ConstraintDescription="must be a valid AMI ID of the form ami-abcd1234",
            Default="ami-7850793d"
        ))

        NatDNSIngress = self.add_resource(SecurityGroupIngress(
            "NatDNSIngress",
            ToPort="53",
            FromPort="53",
            IpProtocol="udp",
            GroupId=Ref("NatSG"),
            CidrIp=Ref(self.vpc_cidr)
        ))

        NatHTTPSIngress = self.add_resource(SecurityGroupIngress(
            "NatHTTPSIngress",
            ToPort="443",
            FromPort="443",
            IpProtocol="tcp",
            GroupId=Ref("NatSG"),
            CidrIp=Ref(self.vpc_cidr)
        ))

        NatASG = self.add_resource(AutoScalingGroup(
            "NatASG",
            AvailabilityZones=["us-west-1a", "us-west-1b", "us-west-1c"],
            DesiredCapacity="3",
            Tags=Tags(
                Name=Join("-", [Ref(self.vpc_id), "NAT"]),
            ),
            MinSize="3",
            MaxSize="3",
            Cooldown="30",
            LaunchConfigurationName=Ref("NatAsgLaunchConfiguration"),
            HealthCheckGracePeriod=30,
            HealthCheckType="EC2"
        ))

        NatHTTPIngress = self.add_resource(SecurityGroupIngress(
            "NatHTTPIngress",
            ToPort="80",
            FromPort="80",
            IpProtocol="tcp",
            GroupId=Ref("NatSG"),
            CidrIp=Ref(self.vpc_cidr)
        ))

        NatNTPIngress = self.add_resource(SecurityGroupIngress(
            "NatNTPIngress",
            ToPort="123",
            FromPort="123",
            IpProtocol="udp",
            GroupId=Ref("NatSG"),
            CidrIp=Ref(self.vpc_cidr)
        ))

        NatSG = self.add_resource(SecurityGroup(
            "NatSG",
            VpcId=Ref(self.vpc_id),
            GroupDescription="Security group for NAT host."
        ))

        NatICMPIngress = self.add_resource(SecurityGroupIngress(
            "NatICMPIngress",
            ToPort="-1",
            FromPort="-1",
            IpProtocol="icmp",
            GroupId=Ref(NatSG),
            CidrIp=Ref(self.vpc_cidr)
        ))

        NatRole = self.add_resource(Role(
            "NatRole",
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
                PolicyName="NATPolicy",
                PolicyDocument={
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "ec2:DescribeInstances",
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

        NatInstanceProfile = self.add_resource(InstanceProfile(
                "NatInstanceProfile",
                Path="/",
                Roles=[Ref(NatRole)]
        ))

        NatAsgLaunchConfiguration = self.add_resource(LaunchConfiguration(
            "NatAsgLaunchConfiguration",
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
                "#pip install --upgrade awscli  && log \"AWS CLI Upgraded Successfully. Beginning HA NAT configuration...\""

                "awscmd=\"/usr/local/bin/aws\"\n",

                "# Set CLI Output to text\n",
                "export AWS_DEFAULT_OUTPUT=\"text\"\n",

                "# Set Instance Identity URI\n",
                "II_URI=\"http://169.254.169.254/latest/dynamic/instance-identity/document\"\n",

                "# Set region of NAT instance\n",
                "REGION=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep region | awk -F\" '{print $4}')\n",

                "# Set AWS CLI default Region\n",
                "export AWS_DEFAULT_REGION=$REGION\n",

                "# Set AZ of NAT instance\n",
                "AVAILABILITY_ZONE=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep availabilityZone | awk -F\" '{print $4}')\n",

                "# Set Instance ID from metadata\n",
                "INSTANCE_ID=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep instanceId | awk -F\" '{print $4}')\n",

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
                "${awscmd} ec2 modify-instance-attribute --instance-id $INSTANCE_ID --source-dest-check \"{\"Value\": false}\" &&\n",
                "log \"Source Destination check disabled for $INSTANCE_ID.\"\n",

                "log \"Configuration of HA NAT complete.\"\n",
                "yum update -y aws*\n",
                ". /etc/profile.d/aws-apitools-common.sh\n",
                "# Configure iptables\n",
                "/sbin/iptables -t nat -A POSTROUTING -o eth0 -s 0.0.0.0/0 -j MASQUERADE\n",
                "/sbin/iptables-save > /etc/sysconfig/iptables\n",
                "exit 0\n"

            ])),
            ImageId=Ref(NatImage),
            KeyName=Ref('ec2Key'),
            SecurityGroups=[Ref(NatSG)],
            EbsOptimized=False,
            IamInstanceProfile=Ref(NatInstanceProfile),
            InstanceType="t2.small",
            AssociatePublicIpAddress=True
        ))
