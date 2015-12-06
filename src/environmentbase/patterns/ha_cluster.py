from environmentbase.template import Template
from environmentbase import resources
from troposphere import Ref, Parameter, Base64, Join, Output, GetAtt, ec2, route53, autoscaling
import troposphere.constants as tpc
from troposphere.policies import CreationPolicy, ResourceSignal
from troposphere.policies import UpdatePolicy, AutoScalingRollingUpdate

SCHEME_INTERNET_FACING = 'internet-facing'
SCHEME_INTERNAL = 'internal'
PUBLIC_ACCESS_CIDR = '0.0.0.0/0'


class HaCluster(Template):
    """
    Generic highly available cluster template
    Contains an ELB, Autoscaling Group, Security Groups, and optional internal DNS record
    """

    def __init__(self,
                 name='HaCluster',
                 ami_name='amazonLinuxAmiId',
                 user_data='',
                 env_vars={},
                 min_size=1, max_size=1,
                 instance_type='t2.micro',
                 subnet_layer=None,
                 elb_scheme=SCHEME_INTERNET_FACING,
                 elb_listeners=[
                    {
                        'elb_protocol': 'HTTP',
                        'elb_port': 80
                    }
                 ],
                 elb_health_check_port=None,
                 elb_health_check_protocol='TCP',
                 elb_health_check_path='',
                 elb_idle_timeout=None,
                 cname='',
                 custom_tags={},
                 scaling_policies=None,
                 creation_policy_timeout=None,
                 allow_default_ingress=True):
        
        # This will be the name used in resource names and descriptions
        self.name = name

        # This is the name used to identify the AMI from the ami_cache.json file
        self.ami_name = ami_name

        # This is the contents of the userdata script as a string
        self.user_data = user_data

        # This is a dictionary of environment variables to inject into the instances
        self.env_vars = env_vars

        # These define the lower and upper boundaries of the autoscaling group
        self.min_size = min_size
        self.max_size = max_size

        # The type of instance for the autoscaling group
        self.instance_type = instance_type
        
        # This is the subnet layer that the ASG is in (public, private, ...)
        self.subnet_layer = subnet_layer

        # This is the type of ELB: internet-facing gets a publicly accessible DNS, while internal is only accessible to the VPC
        self.elb_scheme = elb_scheme

        # This should be a list of dictionaries defining each listener for the ELB
        # Each dictionary can contain elb_port [required], elb_protocol, instance_port, instance_protocol, ssl_cert_name
        self.elb_listeners = elb_listeners

        # This is the health check port for the cluster
        self.elb_health_check_port = elb_health_check_port

        # The ELB health check protocol for the cluster (HTTP, HTTPS, TCP, SSL)
        self.elb_health_check_protocol = elb_health_check_protocol

        # The ELB health check path for the cluster (Only for HTTP and HTTPS)
        self.elb_health_check_path = elb_health_check_path

        # Add a creation policy with a custom timeout if one was specified
        if creation_policy_timeout:
            self.creation_policy = CreationPolicy(ResourceSignal=ResourceSignal(Timeout='PT' + str(creation_policy_timeout) + 'M'))
        else:
            self.creation_policy = None

        # Add update policy
        self.update_policy = UpdatePolicy(
            AutoScalingRollingUpdate=AutoScalingRollingUpdate(
                PauseTime='PT1M',
                MinInstancesInService="1",
                MaxBatchSize='1',
                # WaitOnResourceSignals=True
            )
        )

        # The Idle Timeout for the ELB (how long your connection can stay idle before being terminated)
        self.elb_idle_timeout = elb_idle_timeout

        # This is an optional fully qualified DNS name to create a CNAME in a private hosted zone
        self.cname = cname

        # Translate the custom_tags dict to a list of autoscaling Tags
        self.custom_tags = []
        for key, value in custom_tags.iteritems():
            self.custom_tags.append(autoscaling.Tag(key, value, True))

        # A list of dictionaries describing scaling policies to be passed to add_asg
        self.scaling_policies = scaling_policies

        # Indicates whether ingress rules should be added to the ELB for type-appropriate CIDR ranges 
        # Internet facing ELBs would allow ingress from PUBLIC_ACCESS_CIDR and private ELBs will allow ingress from the VPC CIDR
        self.allow_default_ingress = allow_default_ingress

        super(HaCluster, self).__init__(template_name=self.name)


    def build_hook(self):
        """
        Hook to add tier-specific assets within the build stage of initializing this class.
        """

        # Set the subnet_layer if it wasn't passed in
        self.set_subnet_layer()

        # Create security groups for the ASG and ELB and connect them together
        self.add_security_groups()

        # Add the IAM role for the autoscaling group
        self.add_cluster_instance_profile()

        # Add the ELB for the autoscaling group
        self.add_cluster_elb()

        # Add the CNAME for the ELB
        self.add_cname()

        # Add the userdata for the autoscaling group
        self.add_user_data()

        # Add the autoscaling group for the cluster
        self.add_cluster_asg()

        # Add the outputs for the stack
        self.add_outputs()


    def set_subnet_layer(self):
        """
        If the subnet layer is not passed in, use a private subnet if there are any, otherwise use a public subnet.
        This needs to happen in the build hook, since subnets is not yet initialized in the constructor. You 
        probably won't need to override this. This logic is also duplicated in template.add_asg(), but we need to 
        set it out here so we can pass the same subnet to template.add_elb()
        """
        if not self.subnet_layer:
            if len(self._subnets.get('private')) > 0:
                self.subnet_layer = self._subnets['private'].keys()[0]
            else:
                self.subnet_layer = self._subnets['public'].keys()[0]

    def add_security_groups(self):
        """
        Wrapper method to encapsulate process of creating security groups for this tier
        Creates security groups for both ASG and ELB and opens the ports between them
        Sets self.security_groups as a dictionary with two security_groups: ha_cluster and elb
        """

        elb_sg_ingress_rules = []
        
        if self.allow_default_ingress:
            # Determine ingress rules for ELB security -- open to internet for internet-facing ELB, open to VPC for internal ELB
            access_cidr = PUBLIC_ACCESS_CIDR if self.elb_scheme == SCHEME_INTERNET_FACING else self.vpc_cidr

            # Add the ingress rules to the ELB security group        
            for elb_port in [listener.get('elb_port') for listener in self.elb_listeners]:
                elb_sg_ingress_rules.append(ec2.SecurityGroupRule(FromPort=elb_port, ToPort=elb_port, IpProtocol='tcp', CidrIp=access_cidr))

        # Create the ELB security group and attach the ingress rules
        elb_sg_name = '%sElbSecurityGroup' % self.name
        elb_sg = self.add_resource(
            ec2.SecurityGroup(
                elb_sg_name,
                GroupDescription='Security group for %s ELB' % self.name,
                VpcId=self.vpc_id,
                SecurityGroupIngress=elb_sg_ingress_rules)
        )

        # Create the ASG security group 
        ha_cluster_sg_name = '%sSecurityGroup' % self.name
        ha_cluster_sg = self.add_resource(
            ec2.SecurityGroup(
                ha_cluster_sg_name,
                GroupDescription='Security group for %s' % self.name,
                VpcId=self.vpc_id)
        )

        # Create the reciprocal rules between the ELB and the ASG for all instance ports
        # NOTE: The condition in the list comprehension exists because elb_port is used as a default when instance_port is not specified
        cluster_sg_ingress_ports = {listener.get('instance_port') if listener.get('instance_port') else listener.get('elb_port') for listener in self.elb_listeners}

        # Also add the health check port to the security group rules
        if self.elb_health_check_port:
            cluster_sg_ingress_ports.add(self.elb_health_check_port)

        for cluster_sg_ingress_port in cluster_sg_ingress_ports:
            self.create_reciprocal_sg(
                elb_sg, elb_sg_name,
                ha_cluster_sg, ha_cluster_sg_name,
                from_port=cluster_sg_ingress_port)

        self.security_groups = {'ha_cluster': ha_cluster_sg, 'elb': elb_sg}

        return self.security_groups


    def add_cluster_instance_profile(self):
        """
        Wrapper method to encapsulate process of adding the IAM role for the autoscaling group
        Sets self.instance_profile with the IAM Role resource, used in creation of the Launch Configuration
        """
        self.instance_profile = None


    def add_cluster_elb(self):
        """
        Wrapper method to encapsulate process of creating the ELB for the autoscaling group
        Sets self.cluster_elb with the ELB resource
        """
        # If the ELB is internal, use the same subnet layer as the ASG
        elb_subnet_layer = self.subnet_layer if self.elb_scheme == SCHEME_INTERNAL else None

        # This creates the ELB, opens the specified ports, and attaches the security group and logging bucket
        self.cluster_elb = self.add_elb(
            resource_name=self.name,
            security_groups=[self.security_groups['elb']],
            listeners=self.elb_listeners,
            utility_bucket=self.utility_bucket,
            subnet_layer=elb_subnet_layer,
            scheme=self.elb_scheme,
            idle_timeout=self.elb_idle_timeout,
            health_check_port=self.elb_health_check_port,
            health_check_protocol=self.elb_health_check_protocol,
            health_check_path=self.elb_health_check_path
        )


    def add_cname(self):
        """
        Wrapper method to encapsulate process of creating a CNAME DNS record for the ELB
        Requires InternalHostedZone parameter
        Sets self.cname_record with the record resource
        """

        if not self.cname:
            return

        hosted_zone = self.add_parameter(Parameter(
            'InternalHostedZone',
            Description='Internal Hosted Zone Name',
            Type='String'))

        self.cname_record = self.add_resource(route53.RecordSetType(
            self.name.lower() + 'DnsRecord',
            HostedZoneId=Ref(hosted_zone),
            Comment='CNAME record for %s' % self.name,
            Name=self.cname,
            Type='CNAME',
            TTL='300',
            ResourceRecords=[GetAtt(self.cluster_elb, 'DNSName')]))


    def add_user_data(self):
        """
        Wrapper method to encapsulate process of constructing userdata for the autoscaling group
        Sets self.user_data_payload constructed from the passed in user_data and env_vars 
        """
        self.user_data_payload = self.construct_user_data(self.env_vars, self.user_data)


    def add_cluster_asg(self):
        """
        Wrapper method to encapsulate process of creating the autoscaling group
        Sets self.cluster_asg with the autoscaling group resource
        """
        self.cluster_asg = self.add_asg(
            layer_name=self.name,
            security_groups=[self.security_groups['ha_cluster'], self.common_security_group],
            load_balancer=self.cluster_elb,
            ami_name=self.ami_name,
            user_data=self.user_data_payload,
            instance_type=self.instance_type,
            min_size=self.min_size,
            max_size=self.max_size,
            subnet_layer=self.subnet_layer,
            instance_profile=self.instance_profile,
            custom_tags=self.custom_tags,
            creation_policy=self.creation_policy,
            update_policy=self.update_policy,
            scaling_policies=self.scaling_policies
        )

    def add_outputs(self):
        """
        Wrapper method to encapsulate creation of stack outputs for this template
        """
        self.add_output(Output('%sELBDNSName' % self.name, Value=GetAtt(self.cluster_elb, 'DNSName')))
        self.add_output(Output('%sSecurityGroupId' % self.name, Value=Ref(self.security_groups['ha_cluster'])))
        self.add_output(Output('%sElbSecurityGroupId' % self.name, Value=Ref(self.security_groups['elb'])))

