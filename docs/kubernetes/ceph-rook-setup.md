# Ceph RGW via Rook on Kubernetes

Use this guide when you need:
- ~95%+ AWS S3 API parity (S3 Select, all ACL modes, full bucket replication)
- Full per-tenant namespace isolation (IAM Accounts, each tenant has their own root user)
- Compliance workloads (HIPAA, FedRAMP, SOC 2) requiring WORM / Object Lock in COMPLIANCE mode
- Native OIDC federation with documented Keycloak integration
- Structured audit logs (ops log + usage log) per user/bucket

**Minimum cluster requirements:** 3 nodes with dedicated raw block devices, 16 GB RAM per OSD node, 10 GbE networking.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Kubernetes Cluster                  │
│                                                       │
│  Rook Operator ──→ CephCluster CRD                   │
│                     ├── OSD (x3+)  ← raw block devs  │
│                     ├── MON (x3)   ← consensus        │
│                     ├── MGR (x1+)  ← dashboard/stats │
│                     └── RGW (x2)   ← S3 gateway       │
│                                                       │
│  CephObjectStore CRD ──→ RGW Pods                    │
│  CephObjectStoreUser CRD ──→ IAM users               │
└──────────────────────────────────────────────────────┘
```

---

## Step 1: Install the Rook Operator

```bash
helm repo add rook-release https://charts.rook.io/release
helm repo update

helm install --create-namespace \
  --namespace rook-ceph \
  rook-ceph rook-release/rook-ceph \
  --version v1.17.0 \
  --set csi.enableRbdDriver=false \
  --set csi.enableCephfsDriver=false
```

Verify:
```bash
kubectl -n rook-ceph get pod -l app=rook-ceph-operator
```

---

## Step 2: Deploy a CephCluster

**Important:** each OSD node must have at least one raw, unformatted block device (e.g., `/dev/nvme1n1`). Rook will not use partitions or mounted filesystems.

```yaml
# k8s/ceph-cluster.yaml
apiVersion: ceph.rook.io/v1
kind: CephCluster
metadata:
  name: rook-ceph
  namespace: rook-ceph
spec:
  cephVersion:
    image: quay.io/ceph/ceph:v19.2.0   # Squid (latest stable)
    allowUnsupported: false

  dataDirHostPath: /var/lib/rook

  mon:
    count: 3
    allowMultiplePerNode: false

  mgr:
    count: 1
    modules:
      - name: pg_autoscaler
        enabled: true

  dashboard:
    enabled: true
    ssl: false

  crashCollector:
    disable: false

  # Storage: auto-discover all raw unused block devices on every node
  storage:
    useAllNodes: true
    useAllDevices: true
    # Or be explicit:
    # nodes:
    #   - name: worker-1
    #     devices:
    #       - name: nvme1n1
```

Apply and wait (can take 5–10 min for all OSDs to come up):
```bash
kubectl apply -f k8s/ceph-cluster.yaml
kubectl -n rook-ceph get cephcluster rook-ceph -w
# Wait until HEALTH=HEALTH_OK
```

---

## Step 3: Create an Object Store (RGW)

```yaml
# k8s/ceph-objectstore.yaml
apiVersion: ceph.rook.io/v1
kind: CephObjectStore
metadata:
  name: agent-store
  namespace: rook-ceph
spec:
  metadataPool:
    failureDomain: host
    replicated:
      size: 3
  dataPool:
    failureDomain: host
    replicated:
      size: 3
  preservePoolsOnDelete: false
  gateway:
    port: 80
    httpsPort: 0      # set to 443 and add sslCertificateRef for TLS
    instances: 2      # HA — 2 RGW pods
    resources:
      requests:
        cpu: 500m
        memory: 1Gi
      limits:
        cpu: 2000m
        memory: 2Gi
  zone:
    name: default
```

```bash
kubectl apply -f k8s/ceph-objectstore.yaml
# Rook creates a Service: rook-ceph-rgw-agent-store in namespace rook-ceph
```

---

## Step 4: Create IAM Accounts (multi-tenancy)

Ceph's IAM Accounts model (introduced in Squid 2025) gives each tenant full namespace isolation:

```bash
# Create an account for the deep-agent service
radosgw-admin account create \
  --account-name deep-agent \
  --account-id RGW12345678901

# Create the account's root user
radosgw-admin user create \
  --account-id RGW12345678901 \
  --account-root \
  --uid deep-agent-root \
  --display-name "Deep Agent Root"

# Create a service user within the account
radosgw-admin user create \
  --account-id RGW12345678901 \
  --uid agent-svc \
  --display-name "Agent Service User"

# Create access keys
radosgw-admin key create --uid agent-svc --key-type s3 --gen-access-key
```

---

## Step 5: Bucket and IAM Policy

```bash
# Create a bucket (from within the account)
aws s3api create-bucket \
  --bucket agent-vfs \
  --endpoint-url http://rook-ceph-rgw-agent-store.rook-ceph.svc:80

# Attach an IAM policy scoped to this bucket
cat > /tmp/agent-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket"],
    "Resource": ["arn:aws:s3:::agent-vfs","arn:aws:s3:::agent-vfs/*"]
  }]
}
EOF

radosgw-admin policy put --uid agent-svc --policy-doc file:///tmp/agent-policy.json
```

---

## Step 6: OIDC / STS (Keycloak + Azure AD)

Ceph RGW has first-class OIDC support. Official docs: https://docs.ceph.com/en/latest/radosgw/keycloak/

### Configure Keycloak as Azure AD broker

```
Azure AD → (OIDC) → Keycloak realm "deep-agent" → Ceph RGW STS
```

### Register OIDC provider in Ceph

```bash
radosgw-admin oidc-provider create \
  --provider-url https://keycloak.example.com/realms/deep-agent \
  --client-id seaweedfs-agent \
  --thumbprint <keycloak-cert-thumbprint> \
  --extra-params '{"audience": ["seaweedfs-agent"]}'
```

### Create a trust policy for role assumption

```bash
cat > /tmp/trust-policy.json <<EOF
{
  "Version":"2012-10-17",
  "Statement":[{
    "Effect":"Allow",
    "Principal":{
      "Federated":"arn:aws:iam:::oidc-provider/keycloak.example.com/realms/deep-agent"
    },
    "Action":"sts:AssumeRoleWithWebIdentity",
    "Condition":{
      "StringEquals":{
        "keycloak.example.com/realms/deep-agent:sub":"agent-service-account"
      }
    }
  }]
}
EOF

radosgw-admin role create \
  --role-name AgentRole \
  --assume-role-policy-doc file:///tmp/trust-policy.json
```

### Exchange OIDC token for S3 credentials (agent code)

```python
import boto3

sts = boto3.client(
    "sts",
    endpoint_url="http://rook-ceph-rgw-agent-store.rook-ceph.svc:80",
    aws_access_key_id="",
    aws_secret_access_key="",
    region_name="default",
)

response = sts.assume_role_with_web_identity(
    RoleArn="arn:aws:iam:::role/AgentRole",
    RoleSessionName="agent-session",
    WebIdentityToken=keycloak_jwt,
)
creds = response["Credentials"]
```

---

## Step 7: Enable Audit Logs

```bash
# Enable per-request ops logging
radosgw-admin region set --infile region.json   # set log_meta=true, log_data=true

# Via ceph.conf in ConfigMap
[client.rgw.agent-store]
rgw_enable_ops_log = true
rgw_enable_usage_log = true
```

Logs are available via:
```bash
radosgw-admin log list
radosgw-admin log show --object <log-object>
```

---

## Service Address for Agent

```bash
# The RGW service created by Rook:
kubectl -n rook-ceph get svc rook-ceph-rgw-agent-store

# Use in env var:
S3_ENDPOINT=rook-ceph-rgw-agent-store.rook-ceph.svc.cluster.local:80
```

---

## Upgrade Path

Ceph upgrades must be done sequentially (n → n+1, not n → n+2). Example Squid → Tentacle:

```bash
kubectl -n rook-ceph patch CephCluster rook-ceph \
  --type=json \
  -p='[{"op":"replace","path":"/spec/cephVersion/image","value":"quay.io/ceph/ceph:v20.2.0"}]'

# Monitor upgrade
kubectl -n rook-ceph get cephcluster rook-ceph -w
```

Rolling upgrade with no downtime for reads/writes is supported as long as all MONs and OSDs are `HEALTH_OK` before starting.
