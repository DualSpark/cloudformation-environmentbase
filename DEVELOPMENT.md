Install/Uninstall
-----------------
`python setup.py install`
1 Creates egg file in (<name>-<version>-<py-version>.egg) in ./dist and <python_home>/site-packages 
2 Copies dist package to <python_home>/site-packages
3 Generates wrapper script somewhere on PATH so you can just run ‘environmentbase’

`python setup.py develop`
1 Creates local filesystem reference to repo (<name>.egg-link) into <python_home>/site-packages
2 Generates wrapper script like #3 above

`pip uninstall -y <name>`
- Removes *.egg or *.egg-link from <python_home>/site-packages
- Will need to run twice if both exist

* name and version here refer to setup.py::setup(name=<name>, version=<version> … )


Run/Test/Clean
--------------
# After environmentbase is installed (as either *.egg or *.egg-link) you can run in two ways:
`python -m environmentbase [parameters]`
OR
`environmentbase [parameters]`

They basically do the same thing but the second way is simpler
- The first way runs environmentbase.__main__ from the egg archive directly 
- The second runs an auto-generated wrapper script which invokes environmentbase.__main__.main()

# From the source repo you can run the unit test suite from setup.py
`python setup.py test`

# To remove build files
python setup.py clean —-all

Note: *.pyc files will be regenerated in src whenever you run the test suite but as long as they are git ignored it’s not a big deal. You can still remove them with `rm src/**/*.pyc` 


What is Environmentbase?
------------------

Environmentbase extends [troposphere](https://github.com/cloudtools/troposphere), a library of wrapper objects for programattically generating Cloudformation templates. Environmentbase embraces this model of automation development and extends it in a couple ways:
- provides a configurable base layer of networking resources enabling you to focus on services instead of networking
- provides a small but growing library of functional infrastructure patterns encapsulating industry best practices.
- provides extension mechanism to develop your own configurable, reusable 'patterns' using child-templates.

extends that into a platform for constructing complex Cloudformation templates out of reusable patterns encapsulating best practices.  

Moreover the Environmentbase platform allows for a service oriented development model.  Whereby small teams can build, test and deploy independent infrastructure automation templates, each focused on a specific service or function.  These templates can be imported and associated to a 'top-level' (integration) template to centrally deploy and manage the full environment. The same template can be deployed in any region or AWS account to product identical environments.

Getting started
------------------

First you should familiarize yourself with [Troposphere](https://github.com/cloudtools/troposphere), more examples [here](https://github.com/cloudtools/troposphere/tree/master/examples).

Once a template is generated and output to file it can be deployed using either the AWS CLI like this:

    aws cloudformation create-stack \
    --stack-name CodeDeployDemoStack \
    --template-url templateURL \
    --parameters ParameterKey=<input name 1>,ParameterValue=<input value 1>    
                 ParameterKey=<input name 2>,ParameterValue=<input value 2> \
                 ... \
    --capabilities CAPABILITY_IAM

or with one of the AWS API language bindings, like [boto](http://boto.readthedocs.org/en/latest/).

```python
conn.create_stack(
    'mystack', 
    template_body=file://<path to file>, # for local file OR template_url=<S3 file location>, for S3 location
    parameters=[(<input name 1>, <input value 1>), (<input name 2>, <input value 2>), ...], 
    notification_arns=[], 
    disable_rollback=False, 
    timeout_in_minutes=None,
    capabilities=['CAPABILITY_IAM'])
```

Environmentbase workflow
------------------------

In contrast to the above workflow here is a very simple Environmentbase project that creates and ec2 instance.

```python
from environmentbase.environmentbase import EnvironmentBase
from troposphere import ec2, FindInMap, Ref


class MyEnv(EnvironmentBase):
    def create_action(self):
        self.initialize_template()

        self.template.add_resource(ec2.Instance(
            "ec2instance", 
            InstanceType="m3.medium", 
            ImageId=FindInMap('RegionMap', Ref('AWS::Region'), 'amazonLinuxAmiId')))

        self.write_template_to_file()

if __name__ == '__main__':
    MyEnv()

```

There is a simple MVC model associated to the EnvironmentBase class allowing you to just run the EnvironmentBase subclass:

```bash
$ python setup.py develop
$ environmentbase --help
environmentbase

Tool bundle manages generation, deployment, and feedback of cloudformation resources.

Usage:
    environmentbase (create|deploy) [--config-file <FILE_LOCATION>] [--debug] [--template-file=<TEMPLATE_FILE>]

Options:
  -h --help                            Show this screen.
  -v --version                         Show version.
  --debug                              Prints parent template to console out.
  --config-file <CONFIG_FILE>          Name of json configuration file. Default value is config.json
  --stack-name <STACK_NAME>            User-definable value for the CloudFormation stack being deployed.
  --template-file=<TEMPLATE_FILE>      Name of template to be either generated or deployed.
```

To generate the cloudforamtion template for this python code run `python myenv.py create` then run `python myenv.py deploy` to send the template to cloudformation to deploy.  For this to work you must have an AWS access and secret key pair handy.  Either configured through the [AWS CLI](http://docs.aws.amazon.com/cli/latest/reference/configure/index.html) or in the Environmentbase config.json under the 'boto' section. 

On first run a new config.json file is generated for you in the current working directory.


Here is a slightly different example:

```python
from environmentbase.networkbase import NetworkBase
from troposphere import ec2, FindInMap, Ref


class MyEnv(NetworkBase):
    def create_action(self):
        self.initialize_template()
        self.construct_network()

        self.template.add_resource(ec2.Instance(
            "ec2instance",
            InstanceType="m3.medium",
            ImageId=FindInMap('RegionMap', Ref('AWS::Region'), 'amazonLinuxAmiId')))

        self.write_template_to_file()

if __name__ == '__main__':
    MyEnv()
```

This example extends NetworkBase instead of EnvironmentBase. NetworkBase attaches a bunch of additional resources to the template that constitute a full VPC with subnets, routing tables, and a default security group.
