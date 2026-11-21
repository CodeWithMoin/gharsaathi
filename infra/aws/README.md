# GharSaathi AWS infrastructure

`domain.yaml` owns the production Route 53 hosted zone in the application's
`us-west-2` deployment stack. `certificate.yaml` owns the CloudFront certificate and
must be deployed separately in `us-east-1`, because CloudFront only uses ACM
certificates from that region.

Create the hosted zone first:

```bash
aws cloudformation deploy \
  --profile hackathon-bedrock \
  --region us-west-2 \
  --stack-name gharsaathi-domain \
  --template-file infra/aws/domain.yaml \
  --parameter-overrides DomainName=gharsaathi.app \
  --tags Project=GharSaathi Environment=production ManagedBy=CloudFormation CostCenter=gharsaathi
```

Copy the four `NameServers` output values into Name.com as custom nameservers for
`gharsaathi.app`. Wait until public `NS` queries return those values. Then deploy the
certificate stack in `us-east-1`; CloudFormation adds the DNS validation records to
the Route 53 hosted zone automatically:

```bash
aws cloudformation deploy \
  --profile hackathon-bedrock \
  --region us-east-1 \
  --stack-name gharsaathi-certificate \
  --template-file infra/aws/certificate.yaml \
  --parameter-overrides DomainName=gharsaathi.app HostedZoneId=<domain-stack-output> \
  --tags Project=GharSaathi Environment=production ManagedBy=CloudFormation CostCenter=gharsaathi
```

Do not create the CloudFront distribution until the frontend/backend origin is
deployed and healthy. Pointing the apex domain at an incomplete distribution would
make the new `.app` domain fail because `.app` requires HTTPS.

## Application runtime

`registry.yaml` creates immutable ECR repositories for the frontend and backend.
`application.yaml` creates the safe-demo runtime:

- CloudFront with the issued `us-east-1` certificate;
- an HTTPS-only ALB origin protected by a CloudFront-only header;
- one frontend and one backend ECS Fargate task;
- encrypted PostgreSQL on RDS;
- Secrets Manager values for the database, application encryption, and OpenAI;
- Route 53 records for the apex, `www`, and the protected origin.

The first deployment intentionally sets `COMMERCE_MODE=demo`. Swiggy and Zepto
OAuth must not be enabled until the exact production callbacks in
`docs/production-mcp-access.md` are allowlisted by their respective providers.

These runtime resources are persistent and billable. Review the AWS account,
region, parameter values, and estimated cost before deploying the stack.
