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

## Application runtime (private, owner-only)

### Region: ap-south-1 (Mumbai), in an AWS account the owner controls

Rider assist answers Indian (Vobiz) numbers, and TRAI requires the call media to
be anchored on servers in India, so the runtime runs in `ap-south-1`. A
single-region runtime is the simplest option for one owner, and Mumbai is also
closer to the owner and to Zepto. The hackathon account (`WSParticipantRole`,
policy `ws-default-policy`) denies every regional service outside `us-west-2`
and `us-east-1`, ECS, RDS, EFS, ECR, Secrets Manager, Logs, EC2 and Lightsail
included, so this stack must be deployed in the owner's own account (profile
`gharsaathi`). Re-create `domain.yaml` (any region; Route 53 is global) and
`certificate.yaml` (`us-east-1`) there and re-delegate the Name.com nameservers.

Rider traffic does not go through CloudFront: the edge that terminates TLS is
not guaranteed to be in India. With `RIDER_ENABLED=true`, `rider.gharsaathi.app`
points straight at the Mumbai ALB, which forwards only `/api/rider/vobiz/*` (the
Vobiz-signed answer URL) and the `/api/rider/stream` WebSocket to the backend.
ALB forwards WebSocket upgrades natively. Everything else on that host gets the
ALB's 403. The rider bridge sends call audio to OpenAI Realtime, which is outside
India unless the OpenAI project has India data residency. Confirm that before
enabling rider assist with real calls.

`registry.yaml` creates immutable ECR repositories for the frontend and backend.
`application.yaml` creates the private runtime:

```text
browser ──HTTPS──> CloudFront (gharsaathi.app, www) ──HTTPS + X-Origin-Verify──> ALB (origin.gharsaathi.app)
                                                     /api/*, /health ──> backend task  ──> RDS PostgreSQL 17
                                                     everything else ──> frontend task  └─> EFS backend/.data
```

- CloudFront with the `us-east-1` certificate. `/_next/static/*` is cached; everything
  else (API, pages, cookies, WebSocket upgrades) passes through uncached. Origin read
  timeout 60 s (the CloudFront maximum without a quota increase); typed chat turns are
  capped at 50 s by `CHAT_TURN_TIMEOUT_SECONDS`.
- ALB: HTTPS only, ingress only from CloudFront's origin-facing prefix list, and both
  listener rules require the secret `X-Origin-Verify` header. Idle timeout 120 s;
  uvicorn and Next.js keep connections alive for 130 s so the ALB never reuses a closed one.
- One frontend and one backend ECS Fargate task on ARM64 (Graviton is ~20 % cheaper
  than x86 and builds natively on Apple-silicon machines). The backend service runs
  at most one task (`MaximumPercent: 100`): provider tokens, OAuth state, tracking
  watches and in-memory order cards/photos are single-writer state. A backend deploy
  therefore has a short `/api` gap. Migrations (`alembic upgrade head`) run on start.
- EFS (encrypted, IAM-authorised access point, uid 1001) mounted at
  `/workspace/backend/.data`: encrypted Swiggy/Zepto connections and pending OAuth
  state, support drafts/evidence, order-tracking watches. These survive task
  restarts and deploys. Order cards (5 min) and photos (15 min) stay in memory by
  design; a restart drops them and the user asks again.
- RDS PostgreSQL 17 `db.t4g.micro`, encrypted, private, 7-day backups, deletion protection.
- No NAT gateway: tasks run in the default VPC's public subnets with public IPs for
  egress (OpenAI, Clerk, Zepto, ECR); their security groups accept traffic only from
  the ALB. VPC endpoints would cost more than the public IPs and still not reach OpenAI.
- Secrets: `gharsaathi/app` (one JSON secret written by `scripts/aws-secrets.sh`) and
  `gharsaathi/database` (generated by CloudFormation). No secret is a stack parameter.
- Owner-only: `ALLOWED_USER_IDS` makes every API route (including the voice session
  and OAuth start) answer 403 `private_deployment` to any other Clerk user; the
  browser then shows "This deployment is private." API docs are disabled.

### Deploy

```bash
export AWS_PROFILE=gharsaathi AWS_REGION=ap-south-1
cp .env.aws.example .env.aws          # fill in ALLOWED_USER_IDS and the Clerk production keys
scripts/aws-secrets.sh                # create/update gharsaathi/app (prints key names only)
scripts/deploy-aws.sh plan            # ECR stack + images tagged with the git sha, change set only
scripts/deploy-aws.sh deploy          # deploy, wait for ECS and https://gharsaathi.app/health
RIDER_ENABLED=true scripts/deploy-aws.sh deploy   # later: adds rider.gharsaathi.app
```

`COMMERCE_MODE` defaults to `demo`. `COMMERCE_MODE=live` additionally needs
`CONFIRM_LIVE=zepto-callback-allowlisted` and Zepto's approval of
`https://gharsaathi.app/api/zepto/oauth/callback` (see `docs/production-mcp-access.md`).

These runtime resources are persistent and billable (~$70/month, see the cost table
in the deployment report). Tear down with `aws cloudformation delete-stack
--stack-name gharsaathi-application` after disabling RDS deletion protection; the
RDS snapshot and the EFS file system are retained.
