from troposphere import Output, Ref, Join, iam
import troposphere as t
import boto.s3
from boto.s3.key import Key
import hashlib
import json
import boto
import time
from datetime import datetime


class Template(t.Template):
    '''
    Custom wrapper for Troposphere Template object which handles S3 uploads and a specific
    workflow around hashing the template to allow for a validation mechanism of a template's
    consistency since it was generated.
    '''

    def __init__(self, template_name):
        '''
        Init method for environmentbase.Teplate class
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

    def merge(self, template):
        '''
        Copies into this Template the Parameters, Outputs, Resources,
        :param template:
        :return:
        '''

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