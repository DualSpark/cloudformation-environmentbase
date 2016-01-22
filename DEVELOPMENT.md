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
environmentbase [init|create|deploy|delete] [options]
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
from environmentbase.environmentbase import EnvConfig
from environmentbase.networkbase import NetworkBase
from environmentbase.patterns.bastion import Bastion


class MyEnv(NetworkBase):

    def create_hook(self):

        self.add_child_template(Bastion())

if __name__ == '__main__':
    my_config = EnvConfig(config_handlers=[Bastion])
    MyEnv(env_config=my_config)

```

To generate the cloudformation template for this python code, save the above snippet in a file called `my_env.py` and run `python my_env.py init`.

This will look at the patterns passed into the EnvConfig object and generate a config.json file with the relevant fields added. Fill this config file out, adding values for at least the following fields:  

`template : ec2_key_default` - SSH key used to log into your EC2 instances  
`template : s3_bucket` - S3 bucket used to upload the generated cloudformation templates  

Next run `python my_env.py create` to generate the cloudformation template using the updated config. Since we overrode environmentbase's `create_hook` function, this will hook into environmentbase's create action and add the bastion stack and any other resources you specified.

NOTE: You can also override config values using environment variables. You can create env variables using the format:  
`<section label>_<config_key>` in all caps (e.g. `TEMPLATE_EC2_KEY_DEFAULT`)  

These are read in after the config file is loaded, so will override any values in your config.json  

Then run `python my_env.py deploy` to create the stack on [cloudformation](https://console.aws.amazon.com/cloudformation/)

Note that this example extends NetworkBase instead of EnvironmentBase. NetworkBase attaches several additional resources to the template that constitute a full VPC with subnets, routing tables, and a default security group.

This should bring up a stack containing all of the configured network resources as well as a bastion host. Try SSHing into the bastion host using the SSH key specified in the config.json to validate that it worked.


Network Configuration
---------------------
By default, networkbase will create one public and one private subnet for each availability zone in your VPC. You can modify this configuration by changing the default values in the network block of config:

```json
"network": {
        "network_cidr_base": "10.0.0.0",
        "network_cidr_size": "16",
        "az_count": 3,
        "subnet_types": [
            "public",
            "private"
        ],
        "subnet_config": [
            {
                "type": "public", 
                "size": "18",
                "name": "public"
            },
            {
                "type": "private", 
                "size": "22",
                "name": "private"
            },
        ],
    },
```

From here, you can change the VPC's address range, the types of subnets in your environment, and the subnet mask size of each subnet. You can define as many subnet tiers as you like in the subnet_config section, each new tier you add will be created in each of the AZs. For example, this default configuration would create a VPC with an address range of 10.0.0.0/16 over three AZs; one public subnet with a CIDR mask of 18 in each AZ; and one private subnet with a CIDR mask of 22 in each AZ, for a total of 6 subnets (2 tiers x 3 AZs). Public subnets get an egress route to the VPC's internet gateway, whereas private subnets communicate to the internet via a highly available NAT instance created in a public subnet within the same AZ.


Creating your own controller:
-----------------------------

The following functions are created for the purpose of being overridden to augment default beheavior.

### `deploy_hook()`:
Extension point for modifying behavior of deploy action. Called after config is loaded and before cloudformation deploy_stack is called. Some things you can do in deploy_hook include modifying config or deploy_parameter_bindings or run arbitrary commands with boto.

### `create_hook(self)`:
Override in your subclass for custom resource creation.  Called after config is loaded and template is initialized.  After the hook completes the templates are serialized and written to file and uploaded to S3.

### `delete_hook()`:
Extension point for modifying behavior of delete action. Called after config is loaded and before cloudformation deploy_stack is called. Can be used to manage out-of-band resources with boto.

### `stack_event_hook(event_data)`:
Extension point for reacting to the cloudformation stack event stream.  If global.monitor_stack is enabled in config this function is used to react to stack events. Once a stack is created a notification topic will begin emitting events to a queue.  Each event is passed to this call for further processing.  The return value is used to indicate whether processing is complete (true indicates processing is complete, false indicates you are not yet done).
Details about the event data can be read [here](http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-listing-event-history.html)

The event_data hash provided the following mappings from the raw cloudformation event:  
status = ResourceStatus  
type = ResourceType  
name = LogicalResourceId  
reason = ResourceStatusReason  
props = ResourceProperties  

## Dealing with versions

1. Basic commands

  Reading tag information 

  - List all tags (apply across all branches): `git tag -l`

  - Show commit associated to a tag. This is the same as git show <commit hash>, plus the tag annotation: `git show <tag name>`

  Creating tags

  - We use annotated tags to include extra information: `git tag -a 0.8.4 -m "Version 0.8.4"`

  - Once the tag is created locally it needs to be pushed upstream: `git push origin master --tags`

  Removing a branch or tag

  - Remote: `git push origin :refs/<tags|heads>/<name>` e.g. `git push origin :refs/tags/0.8.5` OR `git push origin :refs/heads/0.8.5`

  - Local: `git <branch|tag> -d 0.8.5`

  Switching to a specific tag: `git checkout 0.8.4`


2. Referencing a specific version of EnvironmentBase with setup.py.

  Note: This works the same for tags and branches (since we use the same names)

  ```python
  from setuptools import setup

  eb_version = “0.8.0”
  setup(
      name=“<your project name here>”,
      version="0.1",
      install_requires=[
          'cfn-environment-base==' + eb_version
      ],
      dependency_links=[
          "https://github.com/DualSpark/cloudformation-environmentbase/zipball/%(eb_version)#egg=cfn-environment-base-%(eb_version)" % locals()
      ],
      package_dir={"": "src"},
      include_package_data=True,
      zip_safe=True
  )
  ```


3. New release procedure
  1. Create a new branch with name <major>.<minor>.<hotfix> e.g “0.8.5”
  2. Make code changes, test .. etc (merge changes from master into this branch)
     Remember to update: src/environmentbase/version.py to the same value as the branch name.
  3. Merge into master
  4. Delete the version branch 
  5. Create tag on master with same name as branch you just deleted.
  
  Note that the branch is deleted **before** the tag is created because they share the same name.  Otherwise referring to things by the version name may be ambigious.
