# Storage Backend Reference

## Why MinIO CE Was Replaced

MinIO Community Edition was permanently archived on **February 14, 2026**:

- No new binaries or Docker images published
- Security patches are case-by-case only; no SLA
- AGPL v3 license: any networked service using modified MinIO must publish its full source — enterprise-incompatible
- Commercial replacement (MinIO AIStor) starts at **$96,000/year**
- Kubeflow Pipelines officially dropped MinIO and adopted SeaweedFS as the default in December 2025

---

## Current Backend: SeaweedFS

**License:** Apache 2.0  
**S3 API parity:** ~85–90%  
**Status:** Active (weekly releases, v4.23 May 2026, 32K GitHub stars)

### Why SeaweedFS

| Requirement | Coverage |
|---|---|
| S3-compatible API | Full — all minio Python SDK calls work unchanged |
| OIDC/SSO | `AssumeRoleWithWebIdentity` STS endpoint — OSS. Admin UI OIDC = Enterprise tier |
| Multi-tenancy IAM | IAM users, groups, roles, bucket policies — OSS |
| Object versioning | Supported |
| Object lock / WORM | Supported |
| Presigned URLs | Supported |
| Kubernetes | Operator + CSI driver available |
| AI/VFS proven | Kubeflow Pipelines default storage since Dec 2025 |

### Configuration

All config uses generic `S3_*` env vars (with `MINIO_*` fallback for backward compatibility):

```env
S3_ENDPOINT=localhost:8333   # SeaweedFS S3 port
S3_ACCESS_KEY=agent_access
S3_SECRET_KEY=agent_secret
S3_BUCKET=agent-vfs
S3_SECURE=false
```

The `s3.json` identity config (`infra/seaweedfs/s3.json`) defines access keys and their permissions.

### OIDC Integration Path (future)

When ready to integrate OIDC:

1. **Keycloak + Azure AD federation**
   ```
   Azure AD → (SAML or OIDC) → Keycloak realm → SeaweedFS STS
   ```
   Keycloak acts as a broker: Azure AD provides corporate identity, Keycloak normalizes to OIDC and issues JWTs.

2. **SeaweedFS STS endpoint**
   ```http
   POST http://seaweedfs:8333/?Action=AssumeRoleWithWebIdentity
   &WebIdentityToken=<keycloak_jwt>
   &RoleArn=arn:aws:iam::ACCOUNT:role/AgentRole
   ```
   Returns temporary `AccessKeyId`, `SecretAccessKey`, `SessionToken` scoped to the role.

3. **IAM role configuration** in `s3.json`
   ```json
   {
     "name": "AgentRole",
     "actions": ["Read", "Write", "List"],
     "conditions": {
       "StringLike": {
         "jwt:email": "*@yourcompany.com"
       }
     }
   }
   ```

4. **Agent code** calls `sts_client.assume_role_with_web_identity(...)` and rotates credentials automatically.

References:
- SeaweedFS IAM docs: https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API
- SeaweedFS STS: https://github.com/seaweedfs/seaweedfs/wiki/Security-IAM

---

## Alternative: Ceph RGW via Rook

See [kubernetes/ceph-rook-setup.md](kubernetes/ceph-rook-setup.md) for full Kubernetes deployment instructions.

**When to choose Ceph over SeaweedFS:**

| Scenario | Recommendation |
|---|---|
| Regulated workloads (HIPAA, FedRAMP, SOC 2) | Ceph — full object lock compliance, WORM, audit logs |
| Multi-tenant with complete namespace isolation | Ceph — IAM Accounts give each tenant their own root user |
| Team with dedicated storage engineers | Ceph — high ops complexity pays off at scale |
| ~95%+ AWS S3 API parity required | Ceph — S3 Select, all ACL modes, full bucket replication |
| Small team, < 10 TB, single cluster | SeaweedFS — lower ops burden |

Ceph provides the same OIDC/STS path (`AssumeRoleWithWebIdentity` via the RGW STS API) with official Keycloak integration documented at:
https://docs.ceph.com/en/latest/radosgw/keycloak/

---

## Watch List: RustFS

**License:** Apache 2.0  
**Status:** Beta (v1.0-beta.1, April 2026)  
**OIDC:** Roadmap (issue #726) — not merged as of May 2026

MinIO-compatible API in Rust, ~2.3x faster for small objects per project benchmarks.
Re-evaluate for production in Q4 2026 after:
- v1.0 stable release
- OIDC merged and tested
- Third-party production validation

---

## Feature Comparison

| Feature | SeaweedFS (current) | Ceph RGW | RustFS (beta) | MinIO CE (archived) |
|---|---|---|---|---|
| License | Apache 2.0 | LGPL 2.1 | Apache 2.0 | AGPL v3 (dead) |
| S3 parity | ~85–90% | ~95%+ | ~80% | ~90% |
| OIDC/SSO | STS OSS; Admin UI = EE | Full, native, Keycloak docs | Roadmap | Was EE only |
| Object versioning | Yes | Yes | Unknown | Yes |
| Object lock / WORM | Yes | Yes | Unknown | Was EE only |
| Multi-tenancy IAM | IAM + policies (OSS) | Full IAM Accounts (2025) | Not yet | Was EE only |
| Audit logs | Log aggregation | Yes (ops + usage logs) | No | Was EE only |
| Presigned URLs | Yes | Yes | Yes | Yes |
| K8s operator | Yes | Yes (Rook) | Helm beta | No (archived) |
| Ops complexity | Low–Medium | High | Low | N/A (archived) |
| Last release | May 2026 | 2025–2026 (Squid/Tentacle) | Apr 2026 | Feb 2026 (archived) |
| Production ready | Yes | Yes | No | No |
| AI/VFS proven | Yes (Kubeflow) | Yes | No | Was (now dead) |
