from environmentbase.template import Template
from troposphere import Ref, Join, Output, GetAtt, AWS_ACCOUNT_ID
from troposphere.cloudfront import Distribution, DistributionConfig, Origin, S3Origin
from troposphere.cloudfront import DefaultCacheBehavior, ForwardedValues, Logging


class CloudFront(Template):
    """
    Creates a CloudFront distribution from a static resource
    """

    def __init__(self, resource_name, domain_name, origin_path='', dist_config=None):
        """
        This will create a cloudfront distribution from a static resource
        @param resource_name [string] - name of the cloudfront distribution to be created
        @param domain_name [string] - domain name of the s3 bucket containing the resource
        @param origin_path [string] - path to the folder containing the resources (optional)
        """

        self.resource_name = resource_name
        self.domain_name = domain_name
        self.origin_path = origin_path
        self.dist_config = dist_config

        super(CloudFront, self).__init__(template_name=resource_name)

    def build_hook(self):
        """
        Hook to add tier-specific assets within the build stage of initializing this class.
        """

        if not self.dist_config:
            self.dist_config = DistributionConfig(
                Origins=[Origin(
                    Id="Origin",
                    DomainName=self.domain_name,
                    OriginPath=self.origin_path,
                    S3OriginConfig=S3Origin(),
                )],
                DefaultCacheBehavior=DefaultCacheBehavior(
                    TargetOriginId="Origin",
                    ForwardedValues=ForwardedValues(
                        QueryString=False
                    ),
                    ViewerProtocolPolicy="allow-all"),
                Enabled=True
            )

        if self.utility_bucket:
            self.dist_config.Logging = Logging(
                Bucket=Join('.', [self.utility_bucket, 's3.amazonaws.com']),
                IncludeCookies=True,
                Prefix=Join('/', ['AWSLogs', Ref(AWS_ACCOUNT_ID), 'CloudFront'])
            )

        cf_distribution = self.add_resource(Distribution(
            self.resource_name,
            DistributionConfig=self.dist_config
        ))

        self.add_output([
            Output("DistributionId", Value=Ref(cf_distribution)),
            Output("DistributionName", Value=Join("", ["http://", GetAtt(cf_distribution, "DomainName")])),
        ])
