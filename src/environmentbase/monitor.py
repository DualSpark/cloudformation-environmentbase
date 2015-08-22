import utility
import json
import time
import re

TERMINAL_STATES = [
    'CREATE_COMPLETE',
    'UPDATE_COMPLETE',
    'UPDATE_ROLLBACK_COMPLETE',
    'CREATE_FAILED',
    'UPDATE_FAILED',
    'UPDATE_ROLLBACK_FAILED',
]


class StackMonitor(object):

    def __init__(self, env_name):
        self.stack_event_handlers = []
        self.env_name = env_name

    def setup_stack_monitor(self, config):
        # Topic and queue names are randomly generated so there's no chance of picking up messages from a previous runs
        name = self.env_name + '_' + time.strftime("%Y%m%d-%H%M%S") + '_' + utility.random_string(5)

        # Creating a topic is idempotent, so if it already exists then we will just get the topic returned.
        sns = utility.get_boto_resource(config, 'sns')
        topic_arn = sns.create_topic(Name=name).arn

        # Creating a queue is idempotent, so if it already exists then we will just get the queue returned.
        sqs = utility.get_boto_resource(config, 'sqs')
        queue = sqs.create_queue(QueueName=name)

        queue_arn = queue.attributes['QueueArn']

        # Ensure that we are subscribed to the SNS topic
        subscribed = False
        topic = sns.Topic(topic_arn)
        for subscription in topic.subscriptions.all():
            if subscription.attributes['Endpoint'] == queue_arn:
                subscribed = True
                break

        if not subscribed:
            topic.subscribe(Protocol='sqs', Endpoint=queue_arn)

        # Set up a policy to allow SNS access to the queue
        if 'Policy' in queue.attributes:
            policy = json.loads(queue.attributes['Policy'])
        else:
            policy = {'Version': '2008-10-17'}

        if 'Statement' not in policy:
            statement = {
                "Sid": "sqs-access",
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": "SQS:SendMessage",
                "Resource": "<SQS QUEUE ARN>",
                "Condition": {"StringLike": {"aws:SourceArn": "<SNS TOPIC ARN>"}}
            }
            statement['Resource'] = queue_arn
            statement['Condition']['StringLike']['aws:SourceArn'] = topic_arn
            policy['Statement'] = [statement]

            queue.set_attributes(Attributes={
                'Policy': json.dumps(policy)
            })

        return topic, queue

    def has_handlers(self):
        return len(self.stack_event_handlers) > 0

    def add_handler(self, handler):
        self.stack_event_handlers.append(handler)

    def cleanup_stack_monitor(self, topic, queue):
        if topic:
            topic.delete()
        if queue:
            queue.delete()

    def start_stack_monitor(self, queue, stack_name, config, debug=False):

        # Process messages by printing out body and optional author name
        poll_timeout = 3600  # an hour
        poll_interval = 5
        start_time = time.time()
        time.clock()
        elapsed = 0
        is_stack_running = True

        while elapsed < poll_timeout and is_stack_running and len(self.stack_event_handlers) > 0:

            elapsed = time.time() - start_time

            msgs = queue.receive_messages(WaitTimeSeconds=poll_interval, MaxNumberOfMessages=10)
            # print 'grabbed batch of %s' % len(msgs)

            for raw_msg in msgs:
                parsed_msg = json.loads(raw_msg.body)
                msg_body = parsed_msg['Message']

                # parse k='val' into a dict
                parsed_msg = {k: v.strip("'") for k, v in re.findall(r"(\S+)=('.*?'|\S+)", msg_body)}

                # remember the most interesting outputs
                data = {
                    "status": parsed_msg.get('ResourceStatus'),
                    "type": parsed_msg.get('ResourceType'),
                    "name": parsed_msg.get('LogicalResourceId'),
                    "reason": parsed_msg.get('ResourceStatusReason'),
                    "props": parsed_msg.get('ResourceProperties')
                }

                # attempt to parse the properties
                try:
                    data['props'] = json.loads(data['props'])
                except ValueError:
                    pass

                if debug:
                    print "New Stack Event --------------\n", \
                        data['status'], data['type'], data['name'], '\n', \
                        data['reason'], '\n'
                else:
                    pass

                # clear the message
                raw_msg.delete()

                # process handlers
                handlers_to_remove = []
                for handler in self.stack_event_handlers:
                    if handler.handle_stack_event(data, config):
                        handlers_to_remove.append(handler)

                # once a handlers job is done no need to keep checking for more events
                for handler in handlers_to_remove:
                    self.stack_event_handlers.remove(handler)

                # Finally test for the termination condition
                if data['type'] == "AWS::CloudFormation::Stack" \
                        and data['name'] == stack_name \
                        and data['status'] in TERMINAL_STATES:
                    is_stack_running = False
                    # print 'termination condition found!'