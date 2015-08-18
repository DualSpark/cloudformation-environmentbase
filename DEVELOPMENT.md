Installing EnvironmentBase
-----------------

If you will be working in the EnvironmentBase project, you should use the following command to install it:
```
python setup.py develop
```

If you do not plan on modifying the code and will simply be using it, instead run:
```
python setup.py install
```  

If you have the AWS CLI, you can run `aws configure` to generate the credentials files in the appropriate place. If you have already configured the AWS CLI, then no further steps are necessary. 

You must ensure that the account you are authenticating with has at least the following permissions:

```javascript
{"Statement": [ {"Action": ["ec2:DescribeAvailabilityZones",
"ec2:DescribeRegions"], "Effect": "Allow", "Resource": "*" }]}
```

This is required to perform the VPC lookups. 


Run/Test/Clean
--------------
### Run
```bash
environmentbase [create|deploy|delete] [options]
```

### From the source repo you can run the unit test suite from setup.py
```bash
python setup.py test
```

### To remove build files
```bash
python setup.py clean —-all
```

Note: *.pyc files will be regenerated in src whenever you run the test suite but as long as they are git ignored it’s not a big deal. You can still remove them with `rm src/**/*.pyc` 


Getting started
------------------

Here is a simple EnvironmentBase project that utilizes one of the packaged patterns

```python
from environmentbase.networkbase import NetworkBase
from environmentbase.patterns import bastion


class MyEnv(NetworkBase):
    def create_action(self):
        self.initialize_template()
        self.construct_network()

        self.add_child_template(bastion.Bastion())

        self.write_template_to_file()

if __name__ == '__main__':
    MyEnv()

```

To generate the cloudformation template for this python code, save the above snippet in a file called my_env.py and run `python my_env.py create`.

The first time you run it, it will look at the patterns used and generate a config.json file with the relevant fields added. Fill this config file out, adding values for at least the following fields:  

template : ec2_key_default - SSH key used to log into your EC2 instances  
template : s3_bucket - S3 bucket used to upload the generated cloudformation templates  

Next run `python my_env.py create` to generate the cloudformation template using the updated config. 

Then run `python my_env.py deploy` to create the stack on [cloudformation](https://console.aws.amazon.com/cloudformation/)

Note that this example extends NetworkBase instead of EnvironmentBase. NetworkBase attaches several additional resources to the template that constitute a full VPC with subnets, routing tables, and a default security group.

This should bring up a stack containing all of the configured network resources as well as a bastion host. Try SSHing into the bastion host using the SSH key specified in the config.json to validate that it worked.

