from troposphere import Output, Ref, Join, Parameter, Base64
from troposphere import iam
import troposphere as t
import boto.s3
from boto.s3.key import Key
import hashlib
import json
import boto
import time
from datetime import datetime
import resources as res


class Template(t.Template):
    """
    Custom wrapper for Troposphere Template object which handles S3 uploads and a specific
    workflow around hashing the template to allow for a validation mechanism of a template's
    consistency since it was generated.
    """

    # input parameters for public and private subnets provided externally
    subnets = {
        'public': [],
        'private': []
    }

    def __init__(self, template_name):
        '''
        Init method for environmentbase.Template class
        @param template_name [string] - name of this template, used when identifying this template when uploading, etc.
        '''
        t.Template.__init__(self)
        self.name = template_name
        self.AWSTemplateFormatVersion = ''

    def __get_template_hash(self):
        '''
        Private method holds process for hashing this template for future validation.
        '''
        m = hashlib.sha256()
        m.update(self.__validation_formatter())
        return m.hexdigest()

    def to_template_json(self):
        '''
        Centralized method for managing outputting this template with a timestamp identifying when it was generated and for creating a SHA256 hash representing the template for validation purposes
        '''
        # strip existing values
        for output_key in ['dateGenerated', 'templateValidationHash']:
            if output_key in self.outputs:
                self.outputs.pop(output_key)

        # set the date that this template was generated
        if 'dateGenerated' not in self.outputs:
            self.add_output(Output('dateGenerated',
                Value=str(datetime.utcnow()),
                Description='UTC datetime representation of when this template was generated'))

        # generate the template validation hash
        if 'templateValidationHash' not in self.outputs:
            self.add_output(Output('templateValidationHash',
                Value=self.__get_template_hash(),
                Description='Hash of this template that can be used as a simple means of validating whether a template has been changed since it was generated.'))

        return self.to_json()

    def validate_template(self):
        '''
        Centralized method for validating this templates' templateValidationHash value
        '''
        if 'templateValidationHash' not in self.outputs:
            raise ValueError('This template does not contain a templateValidationHash output value')
        else:
            output_value = self.outputs.pop('templateValidationHash')
            computed_hash = self.__get_template_hash()
            if output_value.Value != computed_hash:
                raise ValueError('Template failed validation check. Template hash is [' + output_value.get('Value') + '] and computed hash is [' + computed_hash + ']')
            else:
                return True

    def __validation_formatter(self):
        '''
        Validation formatter helps to ensure consistent formatting for hash validation workflow
        '''
        return json.dumps(json.loads(self.to_json()), separators=(',',':'))

    def add_parameter_idempotent(self,
                                 troposphere_parameter):
        '''
        Idempotent add (add only if not exists) for parameters within the template
        @param [Troposphere.Parameter] Troposphere Parameter to add to this template
        '''
        if troposphere_parameter.title not in self.parameters:
            return self.add_parameter(troposphere_parameter)
        else:
            return None

    def upload_template(self,
                        s3_bucket,
                        upload_key_name=None,
                        s3_key_prefix=None,
                        s3_canned_acl='public-read',
                        mock_upload=False):
        '''
        Upload helper to upload this template to S3 for consumption by other templates or end users.
        @param s3_bucket [string] name of the AWS S3 bucket to upload this template to.
        @param upload_key_name [string] direct manner of setting the name of the uploaded template
        @param s3_key_prefix [string] key name prefix to prepend to the key name for the upload of this template.
        @param s3_canned_acl [string] S3 canned ACL string value to use when setting permissions on uploaded key.
        @param mock_upload [boolean] boolean indicating if the upload of this template should be mocked or actually performed.
        '''
        key_serial = str(int(time.time()))

        if upload_key_name == None:
            upload_key_name = self.name

        if s3_key_prefix == None:
            s3_key_name = '/' +  upload_key_name + '.' + key_serial + '.template'
        else:
            s3_key_name = s3_key_prefix + '/' + upload_key_name + '.' + key_serial + '.template'

        if mock_upload:
            # return dummy url
            stack_url = 's3://www.dualspark.com' + s3_key_name
        else:
            conn = boto.connect_s3()
            bucket = conn.get_bucket(s3_bucket)
            key = Key(bucket)
            # upload contents
            key.key = s3_key_name
            key.set_contents_from_string(self.to_json())
            key.set_acl(s3_canned_acl)
            # get stack url
            stack_url = key.generate_url(expires_in=0, query_auth=False)
            stack_url = stack_url.split('?')[0]

        return stack_url

    def add_instance_profile(self, layer_name, iam_policies, path_prefix):
        iam_role_obj = iam.Role(layer_name + 'IAMRole',
                AssumeRolePolicyDocument={
                    'Statement': [{
                        'Effect': 'Allow',
                        'Principal': {'Service': ['ec2.amazonaws.com']},
                        'Action': ['sts:AssumeRole']
                    }]},
                    Path=Join('', ['/' + path_prefix + '/', layer_name , '/']))

        if iam_policies != None:
            iam_role_obj.Policies = iam_policies

        iam_role = self.add_resource(iam_role_obj)

        return self.add_resource(iam.InstanceProfile(layer_name + 'InstancePolicy',
                Path='/' + path_prefix + '/',
                Roles=[Ref(iam_role)]))

    def add_common_parameters(self, public_subnet_count=2, private_subnet_count=2):
        """
        Adds parameters to template for use as a child stack:
            vpcCidr,
            vpcId,
            commonSecurityGroup,
            utilityBucket,
            each subnet: [public|private]Subnet[0-9],
            each AZ name: availabilityZone[0-9]
        """
        self.vpc_cidr = self.add_parameter(Parameter(
            'vpcCidr',
            Description='CIDR of the VPC network',
            Type='String',
            AllowedPattern=res.get_str('cidr_regex'),
            ConstraintDescription=res.get_str('cidr_regex_message')))

        self.vpc_id = self.add_parameter(Parameter(
            'vpcId',
            Description='ID of the VPC network',
            Type='String'))

        self.common_security_group = self.add_parameter(Parameter(
            'commonSecurityGroup',
            Description='Security Group ID of the common security group for this environment',
            Type='String'))

        self.utility_bucket = self.add_parameter(Parameter(
            'utilityBucket',
            Description='Name of the S3 bucket used for infrastructure utility',
            Type='String'))

        largest_subnet_type = max(int(public_subnet_count), int(private_subnet_count))

        for y in ['public', 'private']:
            if y not in self.subnets:
                self.subnets[y] = []
            for x in range(0, largest_subnet_type):
                subnet_param = Parameter(
                    y.lower() + 'Subnet' + str(x),
                    Description='Private subnet ' + str(x),
                    Type='String')
                self.add_parameter(subnet_param)
                self.subnets[y].append(Ref(subnet_param))

        self.azs = []

        for x in range(0, largest_subnet_type):
            az_param = Parameter(
                'availabilityZone' + str(x),
                Description='Availability Zone ' + str(x),
                Type='String')
            self.add_parameter(az_param)
            self.azs.append(Ref(az_param))

    @staticmethod
    def build_bootstrap(bootstrap_files,
                        variable_declarations=None,
                        cleanup_commands=None,
                        prepend_line='#!/bin/bash'):
        """
        Method encapsulates process of building out the bootstrap given a set of variables and a bootstrap file to source from
        Returns base 64-wrapped, joined bootstrap to be applied to an instnace
        @param bootstrap_files [ string[] ] list of paths to the bash script(s) to read as the source for the bootstrap action to created
        @param variable_declaration [ list ] list of lines to add to the head of the file - used to inject bash variables into the script
        @param cleanup_commnds [ string[] ] list of lines to add at the end of the file - used for layer-specific details
        """
        if prepend_line != '':
            ret_val = [prepend_line]
        else:
            ret_val = []

        if variable_declarations is not None:
            for line in variable_declarations:
                ret_val.append(line)
        for bootstrap_file in bootstrap_files:
            for line in Template.get_file_contents(bootstrap_file):
                ret_val.append(line)
        if cleanup_commands is not None:
            for line in cleanup_commands:
                ret_val.append(line)
        return Base64(Join("\n", ret_val))

    @staticmethod
    def get_file_contents(file_name):
        """
        Method encpsulates reading a file into a list while removing newline characters
        @param file_name [string] path to file to read
        """
        ret_val = []
        with open(file_name) as f:
            content = f.readlines()
        for line in content:
            if not line.startswith('#~'):
                ret_val.append(line.replace("\n", ""))
        return ret_val
