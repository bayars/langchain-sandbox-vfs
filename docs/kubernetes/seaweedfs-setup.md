# SeaweedFS on Kubernetes

SeaweedFS is the default VFS backend for this project. This guide covers a production-grade Kubernetes deployment using the SeaweedFS Operator.

## Architecture

```
┌─────────────────────────────────────────────┐
│              SeaweedFS Cluster               │
│                                              │
│  Master (x3)     ← cluster coordination      │
│  Volume (x3+)    ← actual blob storage       │
│  Filer (x2)      ← metadata / directory tree │
│  S3 Gateway (x2) ← S3-compatible HTTP API    │
└─────────────────────────────────────────────┘
         │ S3 API :8333
         ▼
   Agent / Langflow / clients
```

For dev/staging: `weed server` single-node mode (all components in one pod).  
For production: separate Deployments/StatefulSets via the Operator.

---

## Quick Start: Single-Node (dev/staging)

```yaml
# k8s/seaweedfs-dev.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: seaweedfs-s3-config
  namespace: deep-agent
data:
  s3.json: |
    {
      "identities": [
        {
          "name": "agent",
          "credentials": [{ "accessKey": "agent_access", "secretKey": "agent_secret" }],
          "actions": ["Read", "Write", "List", "Tagging", "Admin"]
        }
      ]
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: seaweedfs
  namespace: deep-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: seaweedfs
  template:
    metadata:
      labels:
        app: seaweedfs
    spec:
      containers:
        - name: seaweedfs
          image: chrislusf/seaweedfs:latest
          args:
            - server
            - -dir=/data
            - -s3
            - -s3.port=8333
            - -s3.config=/etc/seaweedfs/s3.json
            - -master.volumeSizeLimitMB=1024
          ports:
            - containerPort: 9333  # master
            - containerPort: 8333  # S3
            - containerPort: 9301  # filer
          volumeMounts:
            - name: data
              mountPath: /data
            - name: s3-config
              mountPath: /etc/seaweedfs
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: seaweedfs-pvc
        - name: s3-config
          configMap:
            name: seaweedfs-s3-config
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: seaweedfs-pvc
  namespace: deep-agent
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 50Gi
---
apiVersion: v1
kind: Service
metadata:
  name: seaweedfs
  namespace: deep-agent
spec:
  selector:
    app: seaweedfs
  ports:
    - name: master
      port: 9333
    - name: s3
      port: 8333
    - name: filer
      port: 9301
```

Apply:
```bash
kubectl create namespace deep-agent
kubectl apply -f k8s/seaweedfs-dev.yaml
```

---

## Production: SeaweedFS Operator

### 1. Install the Operator

```bash
# Add Helm repo
helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs-operator/helm/
helm repo update

# Install operator into its own namespace
helm install seaweedfs-operator seaweedfs/seaweedfs-operator \
  --namespace seaweedfs-operator \
  --create-namespace
```

### 2. Deploy a SeaweedFS Cluster

```yaml
# k8s/seaweedfs-cluster.yaml
apiVersion: seaweedfs.com/v1
kind: SeaweedFS
metadata:
  name: seaweedfs
  namespace: deep-agent
spec:
  image: chrislusf/seaweedfs:latest

  master:
    replicas: 3
    volumeSizeLimitMB: 10000
    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 1Gi

  volume:
    replicas: 3
    storageClassName: standard   # replace with your StorageClass
    storageCapacity: 100Gi
    resources:
      requests:
        cpu: 100m
        memory: 512Mi
      limits:
        cpu: 1000m
        memory: 2Gi

  filer:
    replicas: 2
    resources:
      requests:
        cpu: 100m
        memory: 256Mi

  s3:
    enabled: true
    port: 8333
    httpsPort: 0           # set to 8334 and add TLS secret for HTTPS
    config:
      name: seaweedfs-s3-config   # ConfigMap with s3.json (identities)
```

Apply:
```bash
kubectl apply -f k8s/seaweedfs-cluster.yaml
```

The operator creates master, volume, and filer StatefulSets with anti-affinity rules automatically.

### 3. S3 Service (for agent access)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: seaweedfs-s3
  namespace: deep-agent
spec:
  selector:
    app.kubernetes.io/component: s3
    app.kubernetes.io/name: seaweedfs
  ports:
    - port: 8333
      targetPort: 8333
  type: ClusterIP
```

Agent env var:
```
S3_ENDPOINT=seaweedfs-s3.deep-agent.svc.cluster.local:8333
```

---

## OIDC / STS Integration (future — Keycloak + Azure AD)

SeaweedFS implements the AWS STS `AssumeRoleWithWebIdentity` action.

### Step 1: Configure Keycloak as Azure AD broker

```
Azure AD (corporate IdP)
    → Keycloak realm "deep-agent" (OIDC broker)
        → SeaweedFS STS endpoint
```

In Keycloak: create a client `seaweedfs`, set `audiences` claim to match the role ARN prefix.

### Step 2: Add an IAM role to s3.json

```json
{
  "identities": [
    {
      "name": "AgentRole",
      "credentials": [],
      "actions": ["Read", "Write", "List"],
      "conditions": {
        "StringLike": {
          "jwt:email": ["*@yourcompany.com"]
        }
      }
    }
  ]
}
```

### Step 3: Exchange OIDC token for S3 credentials

```python
import boto3

sts = boto3.client(
    "sts",
    endpoint_url="http://seaweedfs-s3:8333",
    aws_access_key_id="",
    aws_secret_access_key="",
    region_name="us-east-1",
)

response = sts.assume_role_with_web_identity(
    RoleArn="arn:aws:iam::deep-agent:role/AgentRole",
    RoleSessionName="agent-session",
    WebIdentityToken=keycloak_jwt,   # JWT from Keycloak
)

creds = response["Credentials"]
# Use creds["AccessKeyId"], creds["SecretAccessKey"], creds["SessionToken"]
# with boto3 or minio-py for the actual S3 calls — rotates automatically
```

### Step 4: Update agent/storage.py

```python
# In _client(), detect session token and pass it:
from minio.credentials import AssumeRoleWithWebIdentity

provider = AssumeRoleWithWebIdentity(
    sts_endpoint="http://seaweedfs-s3:8333",
    jwt_provider_lambda=lambda: get_jwt_from_keycloak(),
    role_arn="arn:aws:iam::deep-agent:role/AgentRole",
)
return Minio(S3_ENDPOINT, credentials=provider, secure=_SECURE)
```

---

## CSI Driver (mount volumes directly into Pods)

SeaweedFS has a Kubernetes CSI driver so sandbox Jobs can mount VFS directories as filesystem volumes instead of downloading files via S3.

```bash
kubectl apply -f https://raw.githubusercontent.com/seaweedfs/seaweedfs-csi-driver/master/deploy/kubernetes/seaweedfs-csi.yaml
```

StorageClass for dynamic provisioning:
```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: seaweedfs
provisioner: seaweedfs-csi-driver
parameters:
  path: /agent-vfs
  replication: "001"
reclaimPolicy: Retain
volumeBindingMode: Immediate
```

Using it in the sandbox Job spec (in `agent/sandbox.py`):
```python
volumes=[client.V1Volume(
    name="vfs",
    csi=client.V1CSIVolumeSource(
        driver="seaweedfs-csi-driver",
        volume_attributes={"path": f"/agent-vfs/{thread_id}"},
    ),
)]
```

This replaces the ConfigMap injection approach and allows arbitrary binary file sizes.
