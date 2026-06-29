# get visibilyity timeout
aws sqs get-queue-attributes \
	--profile local \
	--queue-url http://floci:4566/000000000000/my-local-queue \
	--attribute-names VisibilityTimeout

# set visibility timeout
aws sqs set-queue-attributes \
	--profile local \
	--queue-url http://floci:4566/000000000000/my-local-queue \
	--attributes VisibilityTimeout=90

# send message
aws sqs send-message \
	--profile local \
	--queue-url http://floci:4566/000000000000/my-local-queue \
	--message-body file://message.json
