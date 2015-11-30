## Cloudformation Environment Generator
[![Build Status](https://ci.dualspark.com/api/badge/github.com/DualSpark/cloudformation-environmentbase/status.svg?branch=master)](https://ci.dualspark.com/github.com/DualSpark/cloudformation-environmentbase)

What is Environmentbase?
------------------

Environmentbase extends [troposphere](https://github.com/cloudtools/troposphere), a library of wrapper objects for programmatically generating Cloudformation templates. Environmentbase embraces this model of automation development and extends it in several ways:
- provides a configurable base layer of networking resources enabling you to focus on services instead of networking
- provides a small but growing library of functional infrastructure patterns encapsulating industry best practices.
- provides an extension mechanism to develop your own configurable, reusable 'patterns' using child-templates.


Moreover the Environmentbase platform allows for a service oriented development model, whereby small teams can build, test and deploy independent infrastructure automation templates, each focused on a specific service or function.  These templates can be imported and associated to a 'top-level' (integration) template to centrally deploy and manage the full environment. The same template can be deployed in any region or AWS account to produce identical environments.

Out of the box, this will create a VPC that should deploy cleanly into any region
across the publicly available AWS regions.  The VPC network, by default, will
include:

* A public (/24) and a private subnet (/22) in three different Availability Zones
* A highly available NAT instance per AZ
* An S3 bucket configured to allow Amazon ELB (within the same region) and AWS
  CloudTrail to aggregate logs

There are a number of configuration options documented within the script itself
using [docopt](http://docopt.org). An overview of the general capabilities and
features is as follows:

* This script queries the AWS VPC API to ensure that the AZ's selected for
  deployment will allow subnets to be deployed to them (sometimes an issue in
  older accounts)
* Modify the base network CIDR block and subnet size and count via parameters
* Set prefixes for S3 key names for ELB and CloudTrail logging paths within the
  created bucket

Used from the command line, this will generate the network alone, but when used
as a Python module, it's a powerful building block to help generate the basic
structures for more complex environments in CloudFormation very easily.

## Python Usage

The Python class EnvironmentBase is designed to be useful from command line
tools, but has further utility as a base class for more complicated
environments. The original aim of this script was to build a reusable artifact
that could serve as the common networking design for multi-AZ, multi-subnet
demo environments. As such, the environmentbase.py script contains a number of
methods that are meant to be used by sub-classes and provide abstractions for
common workflows and use cases. To use this class, simply add it as a dependency 
in your requirements.txt or setup.py. The following is an example showing how to
import core envbase components and patterns, including the bastion host pattern:

```python
from environmentbase.networkbase import NetworkBase
from environmentbase.patterns.bastion import Bastion

class MyEnvClass(NetworkBase):
    '''
    Class creates a VPC, common network components for the environment and a bastion host
    '''

    def create_hook(self):

        # Do custom troposphere resource creation here
        self.add_child_template(Bastion())


    def deploy_hook(self):

        # Do custom deploy steps here


if __name__ == '__main__':

    MyEnvClass()
```

Overriding these two functions allows you to hook into the template generation and stack creation processes of environmentbase to inject the resources and deployment steps for your environment. See the [Development](DEVELOPMENT.md) documentation for more detailed examples. This create_hook() will add a bastion host as a child stack of the environment. 

See [here](src/examples/) for more examples of using patterns.

Documentation within the class takes a modified usage of the
[doxygen](http://www.stack.nl/~dimitri/doxygen/manual/docblocks.html#pythonblocks)
standard by adding a @classarg identifier that indicates that a given method
utilizes an argument that's passed in via the class constructor along with the
type and description of that parameter.

## Getting Started

To use this script, you must install some requirements (listed [here](https://github.com/DualSpark/cloudformation-environmentbase/blob/master/setup.py#L65))  

We recommend you create a [virtual environment](https://virtualenv.pypa.io/en/latest/) to isolate the dependencies from the rest of your system, but it is not required.  
Run the following commands from the root of the environmentbase directory to install the dependencies:

```bash
python setup.py install
```

To use the script itself, you can run it directly from the command line:

```bash
environmentbase --help
```

You must have your AWS credentials configured as required by [boto](http://boto.readthedocs.org/en/latest/boto_config_tut.html).

If you have the AWS CLI, you can run `aws configure` to generate the credentials files in the appropriate place. If you have already configured the AWS CLI, then no further steps are necessary. 

You must ensure that the account you are authenticating with has at least the following permissions:

```javascript
{"Statement": [ {"Action": ["ec2:DescribeAvailabilityZones",
"ec2:DescribeRegions"], "Effect": "Allow", "Resource": "*" }]}
```

This is required to perform the VPC lookups. 

Once you have configured your credentials, you can run the generator as follows:

```bash
environmentbase init
```

This initialization command will generate two files: `config.json` and `ami_cache.json`. You may override the config filename with the `--config-file` parameter. This is useful when managing multiple stacks simultaneously.

You should now look at the generated `config.json` file and fill out at least the following fields:

`template : ec2_key_default` - This must be the name of a valid SSH key in your AWS account  
`template : s3_bucket` - S3 bucket used to upload the generated cloudformation templates  
`logging : s3_bucket` - S3 bucket used for cloudtrail and ELB logging  

You must ensure that the above two buckets exist and that you have access to write to them (they can be the same bucket). Also, the logging s3_bucket must have the correct access policy -- it needs to allow the AWS ELB and Cloudtrail accounts access to upload their logging data. See a sample access policy [here](src/environmentbase/data/logging_bucket_policy.json), just replace all instances of `%%S3_BUCKET%%` with your logging bucket name and attach the policy to your S3 bucket.

You may also edit the other fields to customize the environment to your liking. After you have configured your environment, run:

```bash
environmentbase create
```

This will generate the cloudformation templates using your updated config. It will save them both to S3 in your template bucket as well as locally. You can use the config `template.include_timestamp` setting to toggle whether or not a timestamp will be included the template filenames (This can be useful for keeping versioned templates, it is enabled by default). Then run:

```bash
environmentbase deploy
```

This will create a cloudformation stack from your generated template on [AWS](https://console.aws.amazon.com/cloudformation/)

You can use the config setting `global.monitor_stack` to enable real time tracking of the event stream from the stack deployment. You can then enable `global.write_stack_outputs` to automatically save all the stack outputs to a local file as they are brought up in AWS. You can also hook into the stack event stream with your own scripting using the `stack_event_hook()` function in environmentbase. Simply override this function in your controller and inject any real time deployment scripting. 

You may run the following command to delete your stack when you are done with it:

```bash
environmentbase delete
```

See [File Descriptions](FILE_DESCRIPTIONS.md) for a detailed explanation on the various files generated and consumed by EnvironmentBase
