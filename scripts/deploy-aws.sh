#!/usr/bin/env bash
# Build, push and deploy the private GharSaathi stack on AWS.
#
#   scripts/deploy-aws.sh plan     build + push images, create a change set (does not execute it)
#   scripts/deploy-aws.sh deploy   build + push images, deploy the stack, wait for health
#
# Prerequisites: gharsaathi-registry/-domain (us-west-2) and gharsaathi-certificate
# (us-east-1) stacks; `scripts/aws-secrets.sh` has written gharsaathi/app; .env.aws
# has ALLOWED_USER_IDS and NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY. Images are tagged with
# the git commit (ECR tags are immutable), so the working tree must be clean.
#
# Env overrides: AWS_PROFILE (gharsaathi), AWS_REGION (ap-south-1: TRAI keeps rider
# call media in India), DOMAIN_STACK_REGION (region of the gharsaathi-domain stack;
# default AWS_REGION) or HOSTED_ZONE_ID, COMMERCE_MODE (demo), RIDER_ENABLED (false),
# SUBNET_IDS (two comma-separated public subnets), ALLOW_DIRTY=1 (review builds only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-}"
export AWS_PROFILE="${AWS_PROFILE:-gharsaathi}"
REGION="${AWS_REGION:-ap-south-1}"
DOMAIN_STACK_REGION="${DOMAIN_STACK_REGION:-${REGION}}"
RIDER_ENABLED="${RIDER_ENABLED:-false}"
STACK="gharsaathi-application"
COMMERCE_MODE="${COMMERCE_MODE:-demo}"
ENV_AWS="${GHARSAATHI_ENV_AWS:-${REPO_ROOT}/.env.aws}"

die() { printf 'deploy-aws: %s\n' "$*" >&2; exit 1; }
say() { printf '==> %s\n' "$*"; }
aws_() { aws --region "${REGION}" "$@"; }
output() { aws --region "$1" cloudformation describe-stacks --stack-name "$2" --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text; }
env_value() { [[ -f "${ENV_AWS}" ]] && grep -E "^$1=" "${ENV_AWS}" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }

case "${ACTION}" in plan|deploy) ;; *) die "usage: $0 {plan|deploy}" ;; esac
[[ "${COMMERCE_MODE}" == demo || "${COMMERCE_MODE}" == live ]] || die "COMMERCE_MODE must be demo or live"
if [[ "${COMMERCE_MODE}" == live && "${CONFIRM_LIVE:-}" != "zepto-callback-allowlisted" ]]; then
  die "live mode needs CONFIRM_LIVE=zepto-callback-allowlisted (see docs/production-mcp-access.md)"
fi
for tool in aws docker git python3 curl; do command -v "${tool}" >/dev/null || die "missing ${tool}"; done

say "AWS account $(aws sts get-caller-identity --query Account --output text) · ${REGION}"

ALLOWED_USER_IDS="$(env_value ALLOWED_USER_IDS)"
CLERK_PK="$(env_value NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY)"
WEB_PUSH_SUBJECT="$(env_value WEB_PUSH_SUBJECT)"
RIDER_NUMBER="$(env_value RIDER_ASSIST_NUMBER)"
[[ -n "${ALLOWED_USER_IDS}" ]] || die "set ALLOWED_USER_IDS (the owner's Clerk user id) in ${ENV_AWS}"
[[ -n "${CLERK_PK}" ]] || die "set NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY in ${ENV_AWS}"
[[ "${CLERK_PK}" == pk_live_* ]] || say "warning: Clerk publishable key is not pk_live_ (development instance)"
export AWS_REGION="${REGION}"
"${REPO_ROOT}/scripts/aws-secrets.sh" --check >/dev/null || die "gharsaathi/app is missing required keys; run scripts/aws-secrets.sh"
APP_SECRET_ARN="$(aws_ secretsmanager describe-secret --secret-id gharsaathi/app --query ARN --output text)"
WEB_PUSH_ENABLED=false
SECRET_KEYS="$("${REPO_ROOT}/scripts/aws-secrets.sh" --check)"
if grep -q 'WEB_PUSH_VAPID_PRIVATE_KEY: set' <<<"${SECRET_KEYS}"; then WEB_PUSH_ENABLED=true; fi
if [[ "${RIDER_ENABLED}" == true ]] && ! grep -q 'VOBIZ_AUTH_TOKEN: set' <<<"${SECRET_KEYS}"; then
  die "RIDER_ENABLED=true needs VOBIZ_AUTH_ID/VOBIZ_AUTH_TOKEN in gharsaathi/app (scripts/aws-secrets.sh)"
fi

say "ensure ECR repositories (gharsaathi-registry) in ${REGION}"
aws_ cloudformation deploy --stack-name gharsaathi-registry --template-file "${REPO_ROOT}/infra/aws/registry.yaml" \
  --no-fail-on-empty-changeset --tags Project=GharSaathi ManagedBy=CloudFormation >/dev/null
PG_VERSION="${PG_VERSION:-17.11}"
aws_ rds describe-db-engine-versions --engine postgres --engine-version "${PG_VERSION}" --query 'DBEngineVersions[0].EngineVersion' --output text | grep -qx "${PG_VERSION}" \
  || die "PostgreSQL ${PG_VERSION} is not offered in ${REGION}; set PG_VERSION"

FRONTEND_REPO="$(output "${REGION}" gharsaathi-registry FrontendRepositoryUri)"
BACKEND_REPO="$(output "${REGION}" gharsaathi-registry BackendRepositoryUri)"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-$(output "${DOMAIN_STACK_REGION}" gharsaathi-domain HostedZoneId)}"
CERT_ARN="$(output us-east-1 gharsaathi-certificate CertificateArn)"
PREFIX_LIST_ID="$(aws_ ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing --query 'PrefixLists[0].PrefixListId' --output text)"
VPC_ID="${VPC_ID:-$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)}"
if [[ -z "${SUBNET_IDS:-}" ]]; then
  SUBNET_IDS="$(aws_ ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" Name=default-for-az,Values=true \
    --query 'sort_by(Subnets,&AvailabilityZone)[0:2].SubnetId' --output text | tr '\t' ',')"
fi
[[ "$(tr ',' '\n' <<<"${SUBNET_IDS}" | grep -c .)" == 2 ]] || die "need exactly two subnets, got ${SUBNET_IDS}"

cd "${REPO_ROOT}"
TAG="$(git rev-parse --short=12 HEAD)"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  [[ "${ALLOW_DIRTY:-}" == 1 ]] || die "working tree has uncommitted changes; commit first (or ALLOW_DIRTY=1)"
  TAG="${TAG}-dirty-$(date +%Y%m%d%H%M%S)"
fi
FRONTEND_IMAGE="${FRONTEND_REPO}:${TAG}"
BACKEND_IMAGE="${BACKEND_REPO}:${TAG}"

say "ECR login"
aws_ ecr get-login-password | docker login --username AWS --password-stdin "${BACKEND_REPO%%/*}" >/dev/null

image_exists() { aws_ ecr describe-images --repository-name "${1#*/}" --image-ids imageTag="${TAG}" >/dev/null 2>&1; }
# Fargate tasks are ARM64 (Graviton: ~20% cheaper; native on Apple-silicon build hosts).
if image_exists "${BACKEND_REPO}"; then say "backend ${TAG} already in ECR"; else
  say "build + push backend ${TAG}"
  docker buildx build --platform linux/arm64 --provenance=false -t "${BACKEND_IMAGE}" --push backend
fi
if image_exists "${FRONTEND_REPO}"; then say "frontend ${TAG} already in ECR"; else
  say "build + push frontend ${TAG}"
  docker buildx build --platform linux/arm64 --provenance=false \
    --build-arg NEXT_PUBLIC_API_BASE_URL= \
    --build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="${CLERK_PK}" \
    -t "${FRONTEND_IMAGE}" --push frontend
fi

PARAMS=(
  "HostedZoneId=${HOSTED_ZONE_ID}"
  "VpcId=${VPC_ID}"
  "PublicSubnetIds=${SUBNET_IDS}"
  "CloudFrontPrefixListId=${PREFIX_LIST_ID}"
  "FrontendImage=${FRONTEND_IMAGE}"
  "BackendImage=${BACKEND_IMAGE}"
  "CloudFrontCertificateArn=${CERT_ARN}"
  "AppSecretArn=${APP_SECRET_ARN}"
  "AllowedUserIds=${ALLOWED_USER_IDS}"
  "ClerkPublishableKey=${CLERK_PK}"
  "CommerceMode=${COMMERCE_MODE}"
  "WebPushEnabled=${WEB_PUSH_ENABLED}"
  "WebPushSubject=${WEB_PUSH_SUBJECT}"
  "PostgresEngineVersion=${PG_VERSION}"
  "RiderEnabled=${RIDER_ENABLED}"
  "RiderNumber=${RIDER_NUMBER}"
)
DEPLOY_ARGS=(
  cloudformation deploy --stack-name "${STACK}" --template-file infra/aws/application.yaml
  --capabilities CAPABILITY_IAM --parameter-overrides "${PARAMS[@]}"
  --tags Project=GharSaathi Environment=production ManagedBy=CloudFormation CostCenter=gharsaathi
)

if [[ "${ACTION}" == plan ]]; then
  say "creating change set (not executed)"
  aws_ "${DEPLOY_ARGS[@]}" --no-execute-changeset
  exit 0
fi

say "deploying ${STACK} (first run: ~20-30 min for RDS, CloudFront and certificates)"
aws_ "${DEPLOY_ARGS[@]}" --no-fail-on-empty-changeset
say "waiting for ECS services to be stable"
aws_ ecs wait services-stable --cluster gharsaathi --services backend frontend

DOMAIN="$(output "${REGION}" "${STACK}" SiteUrl)"
for _ in {1..30}; do
  if curl -fsS --max-time 10 "${DOMAIN}/health" >/dev/null && curl -fsS --max-time 10 -o /dev/null "${DOMAIN}/"; then
    say "healthy: ${DOMAIN} (image ${TAG}, COMMERCE_MODE=${COMMERCE_MODE})"
    exit 0
  fi
  sleep 10
done
die "stack deployed but ${DOMAIN} is not healthy; check: aws logs tail /ecs/gharsaathi/backend --since 15m"
