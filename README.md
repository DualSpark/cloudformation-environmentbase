# Cloudformation Base Environment Generator
[![Build Status](https://ci.dualspark.com/api/badge/github.com/DualSpark/cloudformation-environmentbase/status.svg?branch=master)](https://ci.dualspark.com/github.com/DualSpark/cloudformation-environmentbase)

What is Environmentbase?
------------------

Environmentbase extends [troposphere](https://github.com/cloudtools/troposphere), a library of wrapper objects for programmatically generating Cloudformation templates. Environmentbase embraces this model of automation development and extends it in several ways:
- provides a configurable base layer of networking resources enabling you to focus on services instead of networking
- provides a small but growing library of functional infrastructure patterns encapsulating industry best practices.
- provides an extension mechanism to develop your own configurable, reusable 'patterns' using child-templates.

and combines all this into a platform for constructing complex Cloudformation templates out of reusable patterns encapsulating best practices. 

Moreover the Environmentbase platform allows for a service oriented development model, whereby small teams can build, test and deploy independent infrastructure automation templates, each focused on a specific service or function.  These templates can be imported and associated to a 'top-level' (integration) template to centrally deploy and manage the full environment. The same template can be deployed in any region or AWS account to produce identical environments.

Out of the box, this will create a VPC that should deploy cleanly into any region
across the publicly available AWS regions.  The VPC network, by default, will
include:

* A public (/24) and a private subnet (/22) in three different Availability Zones
* A highly available NAT instance per AZ
* A bastion host
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
in your requirements.txt or setup.py. The import and class definition for your 
project will look similar to the following:

```python
from environmentbase.networkbase import NetworkBase

class MyEnvClass(NetworkBase):
    '''
    Class creates a VPC and common network components for the environment
    '''

    def create_action(self):

        self.initialize_template()
        self.construct_network()

        # Do custom troposphere resource creation here

        self.write_template_to_file()


    def deploy_action(self):

        # Do custom deploy steps here

        super(MyEnvClass, self).deploy_action()


if __name__ == '__main__':

    MyEnvClass()
```

See the [Development](DEVELOPMENT.md) documentation for more detailed examples, including how to integrate the pre-packaged patterns

Documentation within the class takes a modified usage of the
[doxygen](http://www.stack.nl/~dimitri/doxygen/manual/docblocks.html#pythonblocks)
standard by adding a @classarg identifier that indicates that a given method
utilizes an argument that's passed in via the class constructor along with the
type and description of that parameter.

## Getting Started

To use this script, you must install some requirements (listed [here](https://github.com/DualSpark/cloudformation-environmentbase/blob/master/setup.py#L64))  

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
environmentbase create
```

This first run will provide you with a copy of config.json. To write to a different file, use the `--config-file` parameter to specify the filename. 

You should now look at the config.json file that was generated and fill out at least the following fields:

template : ec2_key_default - SSH key used to log into your EC2 instances  
template : s3_bucket - S3 bucket used to upload the generated cloudformation templates

You may also edit the other fields to customize the environment to your liking. After you have configured your environment, run the `environmentbase create` command again to generate the cloudformation templates using your updated config. Then run:

```bash
environmentbase deploy
```

This will create a cloudformation stack from your generated template on [AWS](https://console.aws.amazon.com/cloudformation/)

## File Descriptions

### ami_cache.json

This file is a simple JSON dictionary to use when assembling the 'RegionMap'
object within the CloudFormation template. This is a simple way to abstract AMI
Id's out of the solution and can be used in conjunction with Packer to populate
custom AMIs directly from external tools.

### config.json

This is a sample 'config' file to be passed into the command line use case and
identifies a simple dictionary of configuration keys and values to be used
within the execution of the script.

### environmentbase.py

This is the actual python script and can be used as a stand-alone class or as
a command line tool. Documentation is in
pseudo-[doxygen](http://www.stack.nl/~dimitri/doxygen/manual/docblocks.html#pythonblocks)
format and the command line argument parsing process is implemented in Docopt.
This script requires that all packages in the requirements.txt file are
installed.

### networkbase.py

This python script extends the functionlity of environmentbase.py to include all of the basic
network infrastructure including VPC, public and private subnets, NAT instances, and security groups.
The basic usage example shows how to use the class with your own code.

### environmentbase.template

This is a sample output from the environmentbase.py script run from the command
line with all arguments set to their defaults.

### EnvironmentBase.docx

A Microsoft Word-formatted version of this documentation.

### README.md

This file--documentation for usage of this set of scripts and files.

