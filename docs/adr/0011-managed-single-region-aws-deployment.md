# ADR-0011: Managed Single-Region AWS Deployment

## Status
Accepted

## Context
Talvora must be deployed in an enterprise-grade cloud environment that guarantees data security, high availability, point-in-time recovery, and operational simplicity for a small engineering team during the initial pilot. Multi-region architectures introduce distributed consensus overhead, cross-region replication latency, and severe split-brain risks without demonstrable pilot necessity.

## Decision
Talvora will be deployed on a **Managed Single-Region AWS Architecture** provisioned via **OpenTofu**:

1. **Deployment Architecture:**
   - **Compute:** AWS ECS Fargate running Linux containers across multiple Availability Zones. Tasks are segregated by IAM roles into:
     - `api`: Handles incoming HTTPS traffic from an Application Load Balancer (ALB).
     - `doc_worker`: Restricted VPC subnet with no internet egress.
     - `io_worker`: Outbound NAT gateway access for OpenAI and Google APIs.
     - `coordinator`: Lightweight periodic runner.
   - **Database:** Amazon RDS PostgreSQL 17 Multi-AZ with automated backups (PITR up to 35 days) and KMS storage encryption.
   - **Storage:** Private Amazon S3 buckets with server-side KMS encryption, bucket versioning, and strict bucket policies.
   - **Messaging:** Amazon SQS Standard queues with Dead Letter Queues (DLQ) and CloudWatch alarm triggers.
   - **Identity:** AWS Cognito User Pools for staff OIDC authentication.
   - **Email:** Amazon SES for platform transactional emails; recruiter emails delegate to authorized Google accounts via OAuth.
   - **Secrets Management:** AWS Secrets Manager with KMS envelope encryption.
2. **Infrastructure as Code (IaC):**
   - All cloud resources are defined declaratively in OpenTofu (`infra/opentofu/`) with remote state locking in S3 + DynamoDB.
3. **Point-in-Time Recovery (PITR) & Disaster Recovery:**
   - Target RPO ≤ 15 minutes, RTO ≤ 4 hours.
   - Database restore rehearsals must execute the persistent deletion ledger replay before serving traffic to prevent resurrecting deleted PII.

## Consequences
- **Positive:**
  - High availability across multiple Availability Zones without multi-region operational complexity.
  - Fully managed infrastructure minimizing operational maintenance overhead.
- **Negative / Trade-offs:**
  - Regional outage at AWS could cause downtime (acceptable within 99.9% pilot SLA).
  - Multi-region data residency requires deploying dedicated regional stacks for specific customer jurisdictions.
